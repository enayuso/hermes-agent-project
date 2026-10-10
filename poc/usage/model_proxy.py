"""Fail-closed OpenAI chat-completion proxy with place-scoped token admission.

This is staged until an operator chooses numeric limits and routes every main
and auxiliary model client through it. No prompts or replies enter the ledger.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .origin import scopes, verify
from .policy import Policy
from .quota import finalize, monthly_window, reserve

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class ModelProxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], *, policy: Policy, secret: bytes,
                 ledger: Path, upstream_api_key: str = ""):
        policy.validate()
        if address[0] != "127.0.0.1" or len(secret) < 32:
            raise ValueError("model proxy requires loopback and a strong secret")
        self.policy = policy
        self.secret = secret
        self.ledger = ledger
        self.upstream_api_key = upstream_api_key
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        super().__init__(address, ModelProxyHandler)


class ModelProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ModelProxy

    def log_message(self, format, *args):
        # Request paths and headers may carry sensitive data; report only a status.
        return

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, code: str) -> None:
        self._json(status, {"error": {"message": message, "type": code, "code": code}})

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ready"})
        else:
            self._error(404, "Unsupported path", "not_found")

    def _request_body(self) -> tuple[dict, bytes]:
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError("JSON is required")
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdecimal() or not 0 < int(raw_length) <= MAX_BODY_BYTES:
            raise ValueError("Invalid request size")
        raw = self.rfile.read(int(raw_length))
        if len(raw) != int(raw_length):
            raise ValueError("Incomplete request")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("model") != self.server.policy.model:
            raise ValueError("Only the configured model is allowed")
        if data.get("n", 1) != 1 or data.get("best_of", 1) != 1:
            raise ValueError("Multiple completions are unavailable")
        output = data.get("max_completion_tokens", data.get("max_tokens"))
        if output is not None and (
                type(output) is not int or not 0 < output <= self.server.policy.max_output_tokens):
            raise ValueError("Completion token bound exceeded")
        if (data.get("max_completion_tokens") is not None and data.get("max_tokens") is not None
                and data["max_completion_tokens"] != data["max_tokens"]):
            raise ValueError("Conflicting completion token bounds")
        if data.get("stream") is True:
            options = data.get("stream_options") or {}
            if not isinstance(options, dict):
                raise ValueError("Invalid stream options")
            data["stream_options"] = {**options, "include_usage": True}
        elif data.get("stream") not in (None, False):
            raise ValueError("Invalid stream value")
        return data, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _actual_usage(usage: object) -> int | None:
        if not isinstance(usage, dict):
            return None
        total = usage.get("total_tokens")
        return total if type(total) is int and total >= 0 else None

    def _settle(self, request_key: str, start: int, actual: int | None) -> None:
        if actual is None:
            logger.warning("model proxy: upstream omitted usage; full reservation retained")
            return
        try:
            finalize(self.server.ledger, request_key=request_key,
                     period_start=start, actual_units=actual)
        except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
            logger.error("model proxy: metering settlement failed (%s); reservation retained",
                         type(exc).__name__)

    def _send_stream(self, response, request_key: str, period_start: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        actual = None
        complete = False
        total_bytes = 0
        try:
            for line in response:
                total_bytes += len(line)
                if total_bytes > MAX_RESPONSE_BYTES:
                    raise ValueError("upstream response too large")
                if line.startswith(b"data:"):
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        complete = True
                    else:
                        try:
                            value = json.loads(payload)
                            measured = self._actual_usage(value.get("usage"))
                            if measured is not None:
                                actual = measured
                        except (ValueError, AttributeError):
                            pass
                self.wfile.write(f"{len(line):X}\r\n".encode("ascii") + line + b"\r\n")
                self.wfile.flush()
            if complete:
                self._settle(request_key, period_start, actual)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            logger.warning("model proxy: stream interrupted; full reservation retained")
            self.close_connection = True

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._error(404, "Unsupported model endpoint", "not_found")
            return
        try:
            identity = verify(self.server.secret, self.headers.get("X-Pilot-Origin", ""))
        except ValueError:
            self._error(403, "Verified model origin is required", "unattributed_request")
            return
        try:
            data, body = self._request_body()
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._error(400, "Invalid or unbounded model request", "invalid_request")
            return
        scope_names = scopes(identity["kind"], identity["profile"], identity["place"])
        window = monthly_window(datetime.now(timezone.utc), self.server.policy.timezone)
        request_key = uuid.uuid4().hex  # Every physical retry needs its own reservation.
        try:
            admitted = reserve(
                self.server.ledger, request_key=request_key, scopes=scope_names,
                units=self.server.policy.context_window_tokens,
                limits=self.server.policy.limits_for(scope_names), window=window,
                session_id=identity["session"], origin_kind=identity["kind"])
        except (OSError, ValueError, sqlite3.Error):
            self._error(503, "Usage admission is unavailable; request not started", "admission_unavailable")
            return
        if not admitted.accepted:
            reset = datetime.fromtimestamp(admitted.reset_at_utc, timezone.utc).isoformat()
            self._json(429, {"error": {
                "message": f"{admitted.denied_scope} allowance reached; resets {reset}",
                "type": "usage_limit_reached", "code": "usage_limit_reached",
                "resets_in_seconds": max(0, admitted.reset_at_utc - int(time.time())),
            }})
            return
        headers = {"Content-Type": "application/json"}
        if self.server.upstream_api_key:
            headers["Authorization"] = "Bearer " + self.server.upstream_api_key
        upstream_request = urllib.request.Request(
            self.server.policy.upstream_base_url.rstrip("/") + "/chat/completions",
            body, headers=headers, method="POST")
        try:
            with self.server.opener.open(upstream_request, timeout=600) as response:
                if response.status != 200:
                    self._error(502, "Model provider rejected the request", "upstream_error")
                    return
                if data.get("stream") is True:
                    self._send_stream(response, request_key, window.start_utc)
                    return
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("upstream response too large")
            parsed = json.loads(raw)
            actual = self._actual_usage(parsed.get("usage")) if isinstance(parsed, dict) else None
            self._settle(request_key, window.start_utc, actual)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            logger.warning("model proxy: upstream unavailable; full reservation retained")
            self._error(502, "Model provider is temporarily unavailable", "upstream_unavailable")
