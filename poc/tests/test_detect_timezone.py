#!/usr/bin/env python3
"""Portable timezone setup preserves explicit choices and UTF-8 settings."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "detect-timezone.py"
spec = importlib.util.spec_from_file_location("pilot_detect_timezone", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class TimezoneTests(unittest.TestCase):
    def test_explicit_zone_avoids_network_and_host_detection(self):
        with patch.object(module, "slack_owner_zone", side_effect=AssertionError("network called")), \
             patch.object(module, "host_zone", side_effect=AssertionError("host called")):
            self.assertEqual(module.select_zone({"AGENT_TIMEZONE": "UTC"}),
                             ("UTC", "AGENT_TIMEZONE override"))

    def test_owner_zone_takes_precedence_over_host(self):
        with patch.object(module, "slack_owner_zone", return_value="Asia/Tokyo"), \
             patch.object(module, "host_zone", side_effect=AssertionError("host called")):
            self.assertEqual(module.select_zone({"AGENT_TIMEZONE": "auto"}),
                             ("Asia/Tokyo", "owner's Slack profile"))

    def test_replacement_preserves_unicode_and_other_values(self):
        original = "# 설정\nSLACK_BOT_TOKEN=secret\nAGENT_TIMEZONE=auto\nTZ=UTC\nHERMES_TIMEZONE=UTC\n"
        changed = module.replace_zone_lines(original, "Europe/Paris")
        self.assertIn("# 설정", changed)
        self.assertIn("SLACK_BOT_TOKEN=secret", changed)
        self.assertEqual(changed.count("TZ="), 1)
        self.assertEqual(changed.count("HERMES_TIMEZONE="), 1)
        self.assertIn("TZ=Europe/Paris", changed)


if __name__ == "__main__":
    unittest.main()
