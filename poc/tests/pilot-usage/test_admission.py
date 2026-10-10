"""Pinned-relay signer tests with isolated Hermes shims, no live model calls."""

import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "pilot-usage"


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secret = Path(self.temp.name) / "secret"
        self.secret.write_bytes(b"q" * 32)
        self.environ = patch.dict(os.environ, {
            "PILOT_QUOTA_SIGNING_KEY_FILE": str(self.secret),
            "PILOT_QUOTA_LIB_DIR": str(ROOT),
            "PILOT_QUOTA_PROXY_BASE_URL": "http://127.0.0.1:8765/v1",
        })
        self.environ.start()
        self.addCleanup(self.environ.stop)
        context = {
            "HERMES_SESSION_PLATFORM": "slack",
            "HERMES_SESSION_CHAT_ID": "C0AAAAAAAAA",
            "HERMES_SESSION_CHAT_TYPE": "group",
            "HERMES_SESSION_SCOPE_ID": "T0AAAAAAAAA",
            "HERMES_SESSION_ID": "session_1",
        }
        self.home = Path(self.temp.name) / "service"
        self.modules = {
            "hermes_constants": types.ModuleType("hermes_constants"),
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "gateway": types.ModuleType("gateway"),
            "gateway.session_context": types.ModuleType("gateway.session_context"),
            "agent": types.ModuleType("agent"),
            "agent.auxiliary_hooks": types.ModuleType("agent.auxiliary_hooks"),
        }
        self.modules["hermes_constants"].get_hermes_home = lambda: str(self.home)
        self.modules["cron.jobs"].get_job = lambda name: None
        self.modules["gateway.session_context"].get_session_env = lambda name, default="": context.get(name, default)
        hooks = self.modules["agent.auxiliary_hooks"]
        hooks._parent_turn_identity = lambda: {"session_id": "session_1", "platform": "slack"}

        def sync_relay(call, *, aux_task, metadata, client, kwargs, provider, model,
                       api_mode, streaming=False):
            return call()

        async def async_relay(call, *, aux_task, metadata, client, kwargs, provider,
                              model, api_mode):
            return await call()

        hooks.run_with_aux_hooks = sync_relay
        hooks.arun_with_aux_hooks = async_relay
        self.modules["agent"].auxiliary_hooks = hooks
        self.modules["cron"].jobs = self.modules["cron.jobs"]
        self.modules["gateway"].session_context = self.modules["gateway.session_context"]
        self.patch_modules = patch.dict(sys.modules, self.modules)
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)
        spec = importlib.util.spec_from_file_location(
            "pilot_usage_staged", PLUGIN / "__init__.py",
            submodule_search_locations=[str(PLUGIN)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        self.addCleanup(lambda: [sys.modules.pop(key, None) for key in
                                 list(sys.modules) if key.startswith("pilot_usage_staged")])
        spec.loader.exec_module(package)
        self.admission = sys.modules["pilot_usage_staged.admission"]
        sys.path.insert(0, str(ROOT))
        self.addCleanup(lambda: sys.path.remove(str(ROOT)) if str(ROOT) in sys.path else None)
        from usage.origin import verify
        self.verify = verify

    def test_main_request_gets_verified_scope_without_changing_body(self):
        original = {"model": "local-model", "messages": [{"role": "user", "content": "hello"}]}
        result = self.admission.stamp_main_request(
            session_id="session_1", platform="slack",
            base_url="http://127.0.0.1:8765/v1/", request=original)
        self.assertEqual(original.get("extra_headers"), None)
        credential = result["request"]["extra_headers"]["X-Pilot-Origin"]
        identity = self.verify(b"q" * 32, credential)
        self.assertEqual((identity["kind"], identity["place"]),
                         ("service_slack", "T0AAAAAAAAA:C0AAAAAAAAA"))

    def test_auxiliary_sync_and_async_relay_sign_each_physical_attempt(self):
        self.admission.install_auxiliary_signer()
        hooks = self.modules["agent.auxiliary_hooks"]
        client = types.SimpleNamespace(base_url="http://127.0.0.1:8765/v1/")
        options = {"aux_task": "title_generation", "metadata": {},
                   "client": client, "provider": "custom", "model": "local-model",
                   "api_mode": "chat_completions"}
        kwargs = {"model": "local-model"}
        observed = hooks.run_with_aux_hooks(lambda: kwargs["extra_headers"]["X-Pilot-Origin"],
                                            kwargs=kwargs, **options)
        self.assertEqual(self.verify(b"q" * 32, observed)["place"],
                         "T0AAAAAAAAA:C0AAAAAAAAA")
        async_kwargs = {"model": "local-model"}

        async def call():
            return async_kwargs["extra_headers"]["X-Pilot-Origin"]

        observed_async = asyncio.run(hooks.arun_with_aux_hooks(call, kwargs=async_kwargs, **options))
        self.assertEqual(self.verify(b"q" * 32, observed_async)["kind"], "service_slack")
        self.assertNotEqual(observed, observed_async)

    def test_wrong_provider_and_unknown_origin_never_call_auxiliary_model(self):
        self.admission.install_auxiliary_signer()
        hooks = self.modules["agent.auxiliary_hooks"]
        calls = []
        options = {"aux_task": "compression", "metadata": {}, "provider": "custom",
                   "model": "local-model", "api_mode": "chat_completions"}
        with self.assertRaisesRegex(RuntimeError, "not routed"):
            hooks.run_with_aux_hooks(lambda: calls.append(True), kwargs={},
                                     client=types.SimpleNamespace(base_url="https://other.example/v1"),
                                     **options)
        self.assertEqual(calls, [])
        self.modules["agent.auxiliary_hooks"]._parent_turn_identity = lambda: {
            "session_id": "session_1", "platform": "api"}
        with self.assertRaises(ValueError):
            hooks.run_with_aux_hooks(lambda: calls.append(True), kwargs={},
                                     client=types.SimpleNamespace(base_url="http://127.0.0.1:8765/v1"),
                                     **options)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
