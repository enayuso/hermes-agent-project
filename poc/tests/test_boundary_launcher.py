"""The optional boundary launcher must stop before gateway start on drift."""

import importlib.util
import io
import json
import subprocess
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run-gateway-with-boundary.py"
spec = importlib.util.spec_from_file_location("boundary_launcher", SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class BoundaryLauncherTests(unittest.TestCase):
    def test_failed_check_never_execs_gateway(self):
        with patch.object(launcher.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(launcher.os, "execv") as start:
            with self.assertRaisesRegex(RuntimeError, "gateway not started"):
                launcher.check(Path("/opt/data"))
            start.assert_not_called()

    def test_wrong_file_root_stops_before_broker_or_gateway(self):
        with patch.dict(os.environ, {"PILOT_SANDBOX_BROKER_URL": "http://127.0.0.1:8787",
                                    "PILOT_SANDBOX_TOKEN_FILE": "/run/token",
                                    "HERMES_WRITE_SAFE_ROOT": "/opt/data"}), \
             patch.object(launcher.urllib.request, "build_opener") as opener, \
             patch.object(launcher.os, "execv") as start:
            with self.assertRaisesRegex(RuntimeError, "file write root"):
                launcher.main()
            opener.assert_not_called()
            start.assert_not_called()

    def test_partial_quota_configuration_fails_before_gateway(self):
        with patch.dict(os.environ, {"PILOT_QUOTA_POLICY_FILE": "/missing"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                launcher.check_model_quota([Path("/opt/data")])

    def test_quota_cutover_checks_each_profile_and_signed_proxy_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "service"
            home.mkdir()
            config = home / "config.yaml"
            config.write_text("""model:
  provider: custom
  base_url: http://127.0.0.1:8765/v1
  default: local-model
  context_length: 20
plugins:
  enabled: [pilot-usage]
""")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "enabled": True, "model": "local-model",
                "upstream_base_url": "http://127.0.0.1:9000/v1",
                "timezone": "UTC", "organization_tokens": 100,
                "default_channel_tokens": 100, "personal_dm_tokens": 100,
                "context_window_tokens": 20, "max_output_tokens": 5,
            }))
            key = root / "key"
            key.write_bytes(b"k" * 32)
            env = {"PILOT_QUOTA_POLICY_FILE": str(policy),
                   "PILOT_QUOTA_LEDGER_FILE": str(root / "ledger.db"),
                   "PILOT_QUOTA_LIB_DIR": str(SOURCE.parents[1]),
                   "PILOT_QUOTA_SIGNING_KEY_FILE": str(key),
                   "PILOT_QUOTA_PROXY_BASE_URL": "http://127.0.0.1:8765/v1"}
            error = urllib.error.HTTPError(
                "http://127.0.0.1:8765/v1/chat/completions", 400, "bad request", {},
                io.BytesIO(b'{"error":{"code":"invalid_request"}}'))
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(launcher.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                launcher.check_model_quota([home])
                opener.return_value.open.assert_called_once()
                config.write_text(config.read_text().replace(
                    "http://127.0.0.1:8765/v1", "http://127.0.0.1:9999/v1"))
                with self.assertRaisesRegex(RuntimeError, "route mismatch"):
                    launcher.check_model_quota([home])

    def test_quota_proxy_and_gateway_share_one_supervised_lifecycle(self):
        instances = []

        class FakeProxy:
            def __init__(self, address, **kwargs):
                self.address = address
                self.stopped = threading.Event()
                instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def serve_forever(self, **kwargs):
                self.stopped.wait(2)

            def shutdown(self):
                self.stopped.set()

        fake_usage = types.ModuleType("usage")
        fake_usage.__path__ = []
        fake_proxy = types.ModuleType("usage.model_proxy")
        fake_proxy.ModelProxy = FakeProxy
        fake_policy = types.ModuleType("usage.policy")
        fake_policy.Policy = types.SimpleNamespace(load=lambda path: object())
        modules = {"usage": fake_usage, "usage.model_proxy": fake_proxy,
                   "usage.policy": fake_policy}
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_bytes(b"x" * 32)
            env = {"PILOT_QUOTA_POLICY_FILE": str(Path(directory) / "policy.json"),
                   "PILOT_QUOTA_LEDGER_FILE": str(Path(directory) / "ledger.db"),
                   "PILOT_QUOTA_LIB_DIR": str(Path(directory)),
                   "PILOT_QUOTA_SIGNING_KEY_FILE": str(key),
                   "PILOT_QUOTA_PROXY_BASE_URL": "http://127.0.0.1:8765/v1"}
            usage = Path(directory) / "usage"
            usage.mkdir()
            (usage / "model_proxy.py").touch()
            process = types.SimpleNamespace(returncode=0, poll=lambda: 0)
            with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules), \
                 patch.object(launcher, "check_model_quota"), \
                 patch.object(launcher.subprocess, "Popen", return_value=process) as spawn:
                self.assertEqual(launcher.run_with_model_quota([Path(directory)]), 0)
                spawn.assert_called_once_with([launcher.HERMES, "gateway", "run"])
        self.assertEqual(instances[0].address, ("127.0.0.1", 8765))
        self.assertTrue(instances[0].stopped.is_set())


if __name__ == "__main__":
    unittest.main()
