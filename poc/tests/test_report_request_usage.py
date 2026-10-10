"""Windowing and aggregation checks for the physical-request report."""

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts" / "report-request-usage.py"
spec = importlib.util.spec_from_file_location("report_request_usage", path)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


class ReportTest(unittest.TestCase):
    def test_window_and_unknown_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            default = data / "plugin-data/pilot-usage/requests.db"
            personal = data / "profiles/person-u1/plugin-data/pilot-usage/requests.db"
            for path in (default, personal):
                path.parent.mkdir(parents=True)
                with sqlite3.connect(path) as db:
                    db.execute("""CREATE TABLE requests (
                        request_key TEXT PRIMARY KEY, started_at REAL, session_id TEXT,
                        profile TEXT, kind TEXT, place TEXT, platform TEXT, model TEXT,
                        input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
                        task TEXT NOT NULL DEFAULT '')""")
            with sqlite3.connect(default) as db:
                db.executemany("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                    ("a", 100, "s1", "service", "service_slack", "T1:C1", "slack", "m", 10, 2, 12, ""),
                    ("b", 110, "s1", "service", "service_slack", "T1:C1", "slack", "m", None, None, None, "title_generation"),
                    ("c", 50, "s2", "service", "api", "", "api_server", "m", 30, 4, 34, "")])
            with sqlite3.connect(personal) as db:
                db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           ("d", 120, "s3", "person-u1", "personal_dm", "D1", "slack", "m", 7, 1, 8, ""))
            rows = reporter.report(data, 90)
            self.assertEqual(len(rows), 2)
            service = next(row for row in rows if row["kind"] == "service_slack")
            self.assertEqual((service["calls"], service["input_tokens"], service["total_tokens"],
                              service["unknown_usage_calls"], service["auxiliary_calls"]), (2, 10, 12, 1, 1))
            self.assertEqual(len(reporter.report(data, None)), 3)


if __name__ == "__main__":
    unittest.main()
