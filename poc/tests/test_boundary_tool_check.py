"""The cutover gate must reject tool and cron-policy widening."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml


POC = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "boundary_tool_check", POC / "scripts" / "check-boundary-tools.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
POLICY = yaml.safe_load((POC / "config" / "boundary-tool-policy.yaml").read_text())


class BoundaryToolCheckTests(unittest.TestCase):
    def setUp(self):
        self.config = {"memory": {"provider": "pilot-memory"},
                       "tools": {"tool_search": {"enabled": "off"}},
                       "mcp_servers": {"outlook": {"enabled": False}}}

    def names_for(self, config, platform):
        return (set(POLICY["platform_toolsets"][platform]),
                set(POLICY["required_tools"][platform]))

    def test_candidate_rejects_unexpected_tool(self):
        def widened(config, platform):
            toolsets, tools = self.names_for(config, platform)
            if platform == "slack":
                tools.add("browser_exec")
            return toolsets, tools
        errors = checker.check_policy(self.config, POLICY, candidate=True,
                                      names_for=widened)
        self.assertTrue(any("browser_exec" in e for e in errors))

    def test_live_requires_adopted_config(self):
        errors = checker.check_policy(self.config, POLICY, candidate=False,
                                      names_for=self.names_for)
        self.assertTrue(any("active platform_toolsets" in e for e in errors))
        self.assertTrue(any("terminal.backend" in e for e in errors))

    def test_cron_override_cannot_widen(self):
        with tempfile.TemporaryDirectory() as directory:
            cron = Path(directory) / "cron"
            cron.mkdir()
            (cron / "jobs.json").write_text(json.dumps({"jobs": [
                {"id": "safe", "enabled_toolsets": ["terminal"]},
                {"id": "unsafe", "enabled_toolsets": ["browser"]}]}))
            errors = checker.check_cron_overrides(Path(directory),
                set(POLICY["platform_toolsets"]["cron"]))
            self.assertEqual(len(errors), 1)
            self.assertIn("unsafe", errors[0])

    def test_active_cron_needs_scoped_origin_and_no_side_route(self):
        with tempfile.TemporaryDirectory() as directory:
            cron = Path(directory) / "cron"
            cron.mkdir()
            (cron / "jobs.json").write_text(json.dumps({"jobs": [
                {"id": "safe", "enabled": True,
                 "origin": {"platform": "slack", "chat_id": "C1"}},
                {"id": "monitor", "enabled": True, "monitor": "https://example.org",
                 "origin": {"platform": "slack", "chat_id": "C1"}},
                {"id": "unscoped", "enabled": True},
            ]}))
            errors = checker.check_cron_overrides(Path(directory),
                set(POLICY["platform_toolsets"]["cron"]))
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("monitor" in error for error in errors))
            self.assertTrue(any("unscoped" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
