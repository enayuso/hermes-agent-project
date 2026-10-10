"""Operator routines receive a verified Slack workspace before quota attribution."""

import os
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "provision-routine-origins.py"


class RoutineOriginTests(unittest.TestCase):
    def run_script(self, jobs, team="T12345"):
        updates = []
        cron = types.ModuleType("cron")
        cron.__path__ = []
        jobmod = types.ModuleType("cron.jobs")
        jobmod.list_jobs = lambda include_disabled: jobs
        jobmod.update_job = lambda ident, change: updates.append((ident, change))
        scheduler = types.ModuleType("cron.scheduler")
        scheduler._resolve_delivery_target = lambda job: {
            "platform": "slack", "chat_id": "C12345"}
        sdk = types.ModuleType("slack_sdk")
        sdk.WebClient = lambda token: types.SimpleNamespace(
            auth_test=lambda: {"team_id": team})
        modules = {"cron": cron, "cron.jobs": jobmod, "cron.scheduler": scheduler,
                   "slack_sdk": sdk}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {
                "SLACK_HOME_CHANNEL": "C12345", "SLACK_BOT_TOKEN": "test-only"}):
            runpy.run_path(str(SOURCE))
        return updates

    def test_adds_verified_workspace_to_existing_origin(self):
        jobs = [{"id": "one", "name": "channel-watch",
                 "origin": {"platform": "slack", "chat_id": "C12345"}}]
        updates = self.run_script(jobs)
        self.assertEqual(updates[0][1]["origin"]["scope_id"], "T12345")

    def test_workspace_mismatch_rejects_all_changes(self):
        jobs = [{"id": "one", "name": "channel-watch",
                 "origin": {"platform": "slack", "chat_id": "C12345"}},
                {"id": "two", "name": "weekly-agent-landscape",
                 "origin": {"platform": "slack", "chat_id": "C12345",
                            "scope_id": "TOTHER"}}]
        with self.assertRaises(SystemExit):
            self.run_script(jobs)


if __name__ == "__main__":
    unittest.main()
