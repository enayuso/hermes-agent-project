"""Check lifetime usage attribution and avoid counting model rows as sessions."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts/report-usage.py"
SPEC = importlib.util.spec_from_file_location("pilot_report_usage", PATH)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def make_db(path: Path, sessions: list[tuple], usage: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, chat_type TEXT,
                                   chat_id TEXT, origin_json TEXT);
            CREATE TABLE session_model_usage (session_id TEXT, api_call_count INTEGER,
                input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                cache_write_tokens INTEGER, reasoning_tokens INTEGER);
        """)
        con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?)", sessions)
        con.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?)", usage)


class UsageReportTest(unittest.TestCase):
    def test_place_attribution_and_session_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            origin = json.dumps({"scope_id": "T1"})
            make_db(data / "state.db", [
                ("s1", "slack", "group", "C1", origin),
                ("s2", "cron", None, None, None),
                ("s3", "api_server", None, None, None),
            ], [
                ("s1", 1, 100, 10, 0, 0, 0),
                ("s1", 2, 200, 20, 5, 0, 0),
                ("s2", 1, 50, 5, 0, 0, 0),
                ("s3", 1, 75, 7, 0, 0, 0),
            ])
            make_db(data / "profiles/person-u1/state.db", [
                ("s4", "slack", "dm", "D1", origin),
            ], [("s4", 1, 80, 8, 0, 0, 0)])
            rows = REPORT.collect(data)
            by_place = {(r["profile"], r["place"]): r for r in rows}
            channel = by_place[("service", "Slack channel T1:C1")]
            self.assertEqual((channel["sessions"], channel["calls"], channel["input_tokens"]),
                             (1, 3, 300))
            self.assertEqual(by_place[("service", "Scheduled runs (unattributed to channel)")]["input_tokens"], 50)
            self.assertEqual(by_place[("service", "Pilot API")]["output_tokens"], 7)
            self.assertEqual(by_place[("person-u1", "DM (person-u1)")]["input_tokens"], 80)


if __name__ == "__main__":
    unittest.main()
