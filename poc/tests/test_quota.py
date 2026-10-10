"""Quota reservations must be atomic across organization and place scopes."""

import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "usage" / "quota.py"
spec = importlib.util.spec_from_file_location("pilot_quota", SOURCE)
quota = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = quota
spec.loader.exec_module(quota)


class QuotaTests(unittest.TestCase):
    def test_monthly_window_uses_configured_timezone_and_year_rollover(self):
        window = quota.monthly_window(datetime(2026, 12, 31, 16, tzinfo=timezone.utc),
                                      "Asia/Seoul")
        self.assertEqual(datetime.fromtimestamp(window.start_utc, timezone.utc).isoformat(),
                         "2026-12-31T15:00:00+00:00")
        self.assertEqual(datetime.fromtimestamp(window.end_utc, timezone.utc).isoformat(),
                         "2027-01-31T15:00:00+00:00")
        dst = quota.monthly_window(datetime(2026, 3, 15, tzinfo=timezone.utc),
                                   "America/New_York")
        self.assertEqual(datetime.fromtimestamp(dst.start_utc, timezone.utc).isoformat(),
                         "2026-03-01T05:00:00+00:00")
        self.assertEqual(datetime.fromtimestamp(dst.end_utc, timezone.utc).isoformat(),
                         "2026-04-01T04:00:00+00:00")

    def test_denial_commits_no_partial_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            window = quota.Window(100, 200)
            scopes = ("org", "channel:C1")
            limits = {"org": 100, "channel:C1": 50}
            self.assertTrue(quota.reserve(path, request_key="one", scopes=scopes,
                                          units=40, limits=limits, window=window).accepted)
            denied = quota.reserve(path, request_key="two", scopes=scopes,
                                   units=20, limits=limits, window=window)
            self.assertFalse(denied.accepted)
            self.assertEqual((denied.denied_scope, denied.remaining, denied.reset_at_utc),
                             ("channel:C1", 10, 200))
            quota.finalize(path, request_key="one", period_start=100, actual_units=25)
            self.assertTrue(quota.reserve(path, request_key="two", scopes=scopes,
                                          units=20, limits=limits, window=window).accepted)
            self.assertTrue(quota.reserve(path, request_key="two", scopes=scopes,
                                          units=20, limits=limits, window=window).duplicate)
            with self.assertRaisesRegex(ValueError, "reservation units"):
                quota.reserve(path, request_key="two", scopes=scopes,
                              units=1, limits=limits, window=window)
            with self.assertRaises(ValueError):
                quota.reserve(path, request_key="two", scopes=("org",), units=20,
                              limits=limits, window=window)
            self.assertTrue(quota.reserve(path, request_key="three", scopes=scopes,
                                          units=50, limits=limits,
                                          window=quota.Window(200, 300)).accepted)

    def test_verified_origin_metadata_survives_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            window = quota.Window(100, 200)
            self.assertTrue(quota.reserve(path, request_key="cron-call",
                                          scopes=("org", "channel:C1"), units=20,
                                          limits={"org": 100, "channel:C1": 100},
                                          window=window, session_id="cron_abc_20261008_180000",
                                          origin_kind="scheduled_slack").accepted)
            quota.finalize(path, request_key="cron-call", period_start=100, actual_units=7)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute(
                    "SELECT DISTINCT session_id,origin_kind,settled_units FROM reservations"
                ).fetchall(), [("cron_abc_20261008_180000", "scheduled_slack", 7)])
            with self.assertRaisesRegex(ValueError, "origin metadata"):
                quota.reserve(path, request_key="cron-call", scopes=("org", "channel:C1"),
                              units=20, limits={"org": 100, "channel:C1": 100},
                              window=window, session_id="other")

    def test_finalization_cannot_exceed_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            window = quota.Window(100, 200)
            self.assertTrue(quota.reserve(path, request_key="one", scopes=("org",),
                                          units=10, limits={"org": 15},
                                          window=window).accepted)
            with self.assertRaisesRegex(ValueError, "exceeds reserved"):
                quota.finalize(path, request_key="one", period_start=100,
                               actual_units=11)
            self.assertFalse(quota.reserve(path, request_key="two", scopes=("org",),
                                           units=6, limits={"org": 15},
                                           window=window).accepted)
            quota.finalize(path, request_key="one", period_start=100, actual_units=4)
            self.assertTrue(quota.reserve(path, request_key="one", scopes=("org",),
                                          units=10, limits={"org": 15},
                                          window=window).duplicate)
            with self.assertRaisesRegex(ValueError, "reservation units"):
                quota.reserve(path, request_key="one", scopes=("org",),
                              units=4, limits={"org": 15}, window=window)
            self.assertTrue(quota.reserve(path, request_key="two", scopes=("org",),
                                          units=6, limits={"org": 15},
                                          window=window).accepted)

    def test_concurrent_requests_cannot_both_cross_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            gate = threading.Barrier(2)
            results = []

            def attempt(key):
                gate.wait()
                results.append(quota.reserve(path, request_key=key,
                    scopes=("org", "channel:C1"), units=60,
                    limits={"org": 100, "channel:C1": 100},
                    window=quota.Window(100, 200)).accepted)

            threads = [threading.Thread(target=attempt, args=(key,)) for key in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertCountEqual(results, [True, False])

    def test_advisory_check_matches_reserved_and_final_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            scopes = ("organization", "channel:T1:C1")
            limits = {"organization": 50, "channel:T1:C1": 21}
            window = quota.Window(100, 200)
            self.assertTrue(quota.check_available(path, scopes=scopes, units=20,
                                                  limits=limits, window=window).accepted)
            quota.reserve(path, request_key="one", scopes=scopes, units=20,
                          limits=limits, window=window)
            self.assertEqual(quota.check_available(path, scopes=scopes, units=20,
                             limits=limits, window=window).denied_scope, "channel:T1:C1")
            quota.finalize(path, request_key="one", period_start=100, actual_units=1)
            self.assertTrue(quota.check_available(path, scopes=scopes, units=20,
                                                  limits=limits, window=window).accepted)

    def test_older_offline_ledger_migrates_without_losing_final_charge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.db"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE reservations (
                    request_key TEXT NOT NULL, scope TEXT NOT NULL,
                    period_start INTEGER NOT NULL, units INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (request_key, scope, period_start))""")
                db.execute("INSERT INTO reservations VALUES ('old','org',100,4,'final')")
            result = quota.reserve(path, request_key="new", scopes=("org",),
                                   units=6, limits={"org": 10},
                                   window=quota.Window(100, 200))
            self.assertTrue(result.accepted)
            self.assertFalse(quota.reserve(path, request_key="third", scopes=("org",),
                                           units=1, limits={"org": 10},
                                           window=quota.Window(100, 200)).accepted)


if __name__ == "__main__":
    unittest.main()
