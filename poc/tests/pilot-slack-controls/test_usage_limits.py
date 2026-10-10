"""Slack allowance refusals stay in the requester's thread and use verified scopes."""

import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "pilot-slack-controls"
sys.path.insert(0, str(ROOT))
package = types.ModuleType("pilot_controls_usage_test")
package.__path__ = [str(PLUGIN)]
sys.modules[package.__name__] = package
limits = importlib.import_module("pilot_controls_usage_test.usage_limits")
from usage.quota import Window, finalize, monthly_window, reserve
from datetime import datetime, timezone


class Adapter:
    _bot_user_id = "UBOT"

    def __init__(self, home):
        self.gateway_runner = types.SimpleNamespace(
            _resolve_profile_home_for_source=lambda source: home)

    def _early_reject_unauthorized(self, user, channel, is_dm):
        return user != "UOWNER"

    def build_source(self, **kwargs):
        return types.SimpleNamespace(profile_route_rejected=False)

    async def _should_wake_on_unmentioned_message(self, parent, channel, user, is_reply,
                                                   team_id, chat_type):
        return parent == "99.0"


def event(*, channel="C12345", channel_type="channel", thread=None, text="<@UBOT> help"):
    message = {"type": "message", "channel": channel, "channel_type": channel_type,
               "user": "UOWNER", "ts": "100.1", "text": text}
    if thread:
        message["thread_ts"] = thread
    return {"type": "event_callback", "team_id": "T12345", "event": message}


class UsageLimitReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ledger = root / "quota.db"
        self.policy = root / "policy.json"
        self.policy.write_text(json.dumps({
            "enabled": True, "model": "local", "upstream_base_url": "http://127.0.0.1:8001/v1",
            "timezone": "Asia/Seoul", "organization_tokens": 50,
            "default_channel_tokens": 21, "personal_dm_tokens": 21,
            "context_window_tokens": 20, "max_output_tokens": 5,
        }))
        self.env = patch.dict(os.environ, {
            "PILOT_QUOTA_POLICY_FILE": str(self.policy),
            "PILOT_QUOTA_LEDGER_FILE": str(self.ledger),
            "PILOT_QUOTA_LIB_DIR": str(ROOT),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.adapter = Adapter(root / "profiles" / "person-uowner")
        self.window = monthly_window(datetime.now(timezone.utc), "Asia/Seoul")

    def charge(self, scope):
        scopes = ("organization", scope) if scope.startswith("channel:") else (scope,)
        reserve(self.ledger, request_key=scope, scopes=scopes,
                units=20, limits={"organization": 50, scope: 21}, window=self.window)
        finalize(self.ledger, request_key=scope, period_start=self.window.start_utc,
                 actual_units=7)

    def decide(self, body):
        return asyncio.run(limits.limit_decision(body, self.adapter))

    def test_channel_denial_posts_to_original_thread(self):
        self.charge("channel:T12345:C12345")
        refused, target = self.decide(event(thread="99.0", text="continue"))
        self.assertTrue(refused)
        self.assertEqual(target["thread_ts"], "99.0")
        self.assertIn("this channel's monthly token allowance", target["text"])
        self.assertIn("Asia/Seoul", target["text"])
        self.assertNotIn("channel:T12345", target["text"])

    def test_personal_dm_denial_and_no_unmentioned_channel_spam(self):
        self.charge("personal:person-uowner")
        refused, target = self.decide(event(channel="D12345", channel_type="im",
                                            text="hello"))
        self.assertTrue(refused)
        self.assertNotIn("thread_ts", target)
        self.assertIn("your personal DM monthly token allowance", target["text"])
        self.assertEqual(self.decide(event(text="hello everyone")), (False, None))

    def test_incomplete_configuration_refuses_with_generic_message(self):
        os.environ.pop("PILOT_QUOTA_LEDGER_FILE")
        refused, target = self.decide(event())
        self.assertTrue(refused)
        self.assertIn("can't check its usage allowance", target["text"])


if __name__ == "__main__":
    unittest.main()
