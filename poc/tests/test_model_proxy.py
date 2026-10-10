"""Exercise model admission at the HTTP boundary with a local provider stand-in."""

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from usage.model_proxy import ModelProxy
from usage.origin import scopes, sign, verify
from usage.policy import Policy


class ProviderHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, body, dict(self.headers)))
        if body.get("stream"):
            raw = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                   b'data: {"choices":[],"usage":{"total_tokens":7}}\n\n'
                   b'data: [DONE]\n\n')
            content_type = "text/event-stream"
        elif body.get("user") == "no-usage":
            raw = b'{"choices":[{"message":{"content":"ok"}}]}'
            content_type = "application/json"
        else:
            raw = b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":7}}'
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name) / "usage.db"
        ProviderHandler.requests = []
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        self.addCleanup(self.provider.server_close)
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        self.addCleanup(self.provider.shutdown)
        policy = Policy(model="local-model",
                        upstream_base_url=f"http://127.0.0.1:{self.provider.server_port}/v1",
                        timezone="Asia/Seoul", organization_tokens=34,
                        default_channel_tokens=27, personal_dm_tokens=27,
                        context_window_tokens=20, max_output_tokens=5)
        self.secret = b"s" * 32
        self.proxy = ModelProxy(("127.0.0.1", 0), policy=policy,
                                secret=self.secret, ledger=self.ledger)
        self.addCleanup(self.proxy.server_close)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.addCleanup(self.proxy.shutdown)
        self.url = f"http://127.0.0.1:{self.proxy.server_port}/v1/chat/completions"

    def request(self, *, token=None, stream=False, user=None):
        body = {"model": "local-model", "messages": [{"role": "user", "content": "hi"}],
                "stream": stream}
        if user:
            body["user"] = user
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Pilot-Origin"] = token
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def credential(self, *, place="T0AAAAAAAAA:C0AAAAAAAAA", kind="service_slack",
                   profile="service"):
        return sign(self.secret, kind=kind, profile=profile, place=place,
                    session_id="session_1")

    def charges(self):
        with sqlite3.connect(self.ledger) as db:
            return dict(db.execute(
                "SELECT scope,SUM(COALESCE(settled_units,units)) FROM reservations GROUP BY scope"))

    def test_unsigned_and_tampered_calls_never_reach_provider(self):
        self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request(token=self.credential() + "x")[0], 403)
        self.assertEqual(ProviderHandler.requests, [])

    def test_channel_limit_denies_before_provider_and_reconciles_actual_use(self):
        token = self.credential()
        self.assertEqual(self.request(token=token)[0], 200)
        self.assertEqual(self.request(token=token)[0], 200)
        status, body, _ = self.request(token=token)
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)["error"]["code"], "usage_limit_reached")
        self.assertEqual(len(ProviderHandler.requests), 2)
        self.assertEqual(self.charges(), {
            "organization": 14,
            "channel:T0AAAAAAAAA:C0AAAAAAAAA": 14})
        with sqlite3.connect(self.ledger) as db:
            self.assertEqual(db.execute(
                "SELECT DISTINCT session_id,origin_kind FROM reservations"
            ).fetchall(), [("session_1", "service_slack")])
        self.assertTrue(all(path == "/v1/chat/completions" for path, _, _ in ProviderHandler.requests))
        self.assertTrue(all("X-Pilot-Origin" not in headers
                            for _, _, headers in ProviderHandler.requests))
        self.assertTrue(all("max_tokens" not in body for _, body, _ in ProviderHandler.requests))

    def test_stream_usage_and_missing_usage_reservation(self):
        token = self.credential()
        status, data, headers = self.request(token=token, stream=True)
        self.assertEqual(status, 200)
        self.assertIn(b"data: [DONE]", data)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertTrue(ProviderHandler.requests[0][1]["stream_options"]["include_usage"])
        self.assertEqual(self.charges()["organization"], 7)
        self.assertEqual(self.request(token=token, user="no-usage")[0], 200)
        self.assertEqual(self.charges()["organization"], 27)
        self.assertEqual(self.request(token=token)[0], 429)
        self.assertEqual(len(ProviderHandler.requests), 2)

    def test_personal_scope_and_origin_validation(self):
        token = self.credential(kind="personal_dm", profile="person-u0bgk9e8btn",
                                place="D0AAAAAAAAA")
        self.assertEqual(self.request(token=token)[0], 200)
        self.assertEqual(self.charges(), {"personal:person-u0bgk9e8btn": 7})
        self.assertEqual(scopes("service_slack", "service",
                                "T0AAAAAAAAA:C0AAAAAAAAA")[0], "organization")
        with self.assertRaises(ValueError):
            scopes("personal_dm", "service", "D0AAAAAAAAA")
        expired = sign(self.secret, kind="service_slack", profile="service",
                       place="T0AAAAAAAAA:C0AAAAAAAAA", session_id="session_1", at=10)
        with self.assertRaises(ValueError):
            verify(self.secret, expired, now=131)

    def test_policy_requires_explicit_enabled_values(self):
        path = Path(self.directory.name) / "policy.json"
        path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        with self.assertRaises(ValueError):
            Policy.load(path)
        with self.assertRaises(ValueError):
            Policy(model="m", upstream_base_url="http://127.0.0.1:bad/v1",
                   timezone="UTC", organization_tokens=20, default_channel_tokens=20,
                   personal_dm_tokens=20, context_window_tokens=20,
                   max_output_tokens=5).validate()
        with self.assertRaisesRegex(ValueError, "full request reservation"):
            Policy(model="m", upstream_base_url="http://127.0.0.1:9000/v1",
                   timezone="UTC", organization_tokens=19, default_channel_tokens=20,
                   personal_dm_tokens=20, context_window_tokens=20,
                   max_output_tokens=5).validate()


if __name__ == "__main__":
    unittest.main()
