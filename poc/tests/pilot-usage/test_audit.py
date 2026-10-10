"""One-request accounting and place attribution checks; run as a plain Python script."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

module_path = Path(__file__).resolve().parents[2] / "plugins/pilot-usage/audit.py"
spec = importlib.util.spec_from_file_location("pilot_usage_audit", module_path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

jobs = {"abc123": {"origin": {"platform": "slack", "chat_id": "C12345",
                               "scope_id": "T12345"}}}
lookup = jobs.get
assert audit.classify("cron_abc123_20261008_180000", "cron", {}, lookup) == (
    "scheduled_slack", "T12345:C12345")
assert audit.classify("cron_abc123_20261008_180000", "cron", {}, lambda _: {
    "origin": {"platform": "slack", "chat_id": "C12345"}}) == (
    "scheduled_unattributed", "")
assert audit.classify("cron_abc123_20261008_180000", "cron", {}, lambda _: {
    "origin": {"platform": "slack", "chat_id": "D12345"}}) == (
    "scheduled_personal_dm", "D12345")
assert audit.classify("cron_unknown_20261008_180000", "cron", {}, lookup) == (
    "scheduled_unattributed", "")
assert audit.classify("session-1", "slack", {
    "platform": "slack", "chat_id": "D12345", "chat_type": "dm", "session_id": "session-1"}, lookup) == (
    "personal_dm", "D12345")
assert audit.classify("session-1", "slack", {
    "platform": "slack", "chat_id": "C12345", "chat_type": "group", "scope_id": "T12345",
    "session_id": "different"}, lookup) == ("unattributed", "")
assert audit.classify("session-1", "api_server", {}, lookup) == ("api", "")

with tempfile.TemporaryDirectory() as temp:
    db_path = Path(temp) / "usage.db"
    args = {"request_id": "turn-1:api:1", "session_id": "session-1", "profile": "service",
            "kind": "service_slack", "place": "T1:C1", "platform": "slack", "model": "test-model",
            "started_at": 100.0, "usage": {"prompt_tokens": 20, "completion_tokens": 3}}
    assert audit.record(db_path, **args)
    assert not audit.record(db_path, **args)
    assert audit.record(db_path, **(args | {"request_id": "aux-1", "usage": None,
                                            "task": "title_generation"}))
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT input_tokens, output_tokens, total_tokens, task FROM requests ORDER BY request_key").fetchall()
        count = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 2
    assert (20, 3, 23, "") in rows
    assert (None, None, None, "title_generation") in rows
    assert db_path.stat().st_mode & 0o777 == 0o600

print("usage audit checks passed")
