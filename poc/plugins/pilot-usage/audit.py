"""A minimal, private usage ledger. Never store prompts, replies, or credentials."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

_CRON_SESSION = re.compile(r"^cron_([a-zA-Z0-9-]+)_\d{8}_\d{6}$")
_TEAM = re.compile(r"^T[A-Z0-9]{3,}$")
_CHANNEL = re.compile(r"^[CG][A-Z0-9]{3,}$")
_DM = re.compile(r"^D[A-Z0-9]{3,}$")


def classify(session_id: str, platform: str, context: dict, job_lookup) -> tuple[str, str]:
    """Return (kind, place). Unknown or inconsistent origins remain unattributed."""
    cron = _CRON_SESSION.fullmatch(session_id)
    if cron:
        job = job_lookup(cron.group(1))
        origin = job.get("origin") if isinstance(job, dict) else None
        if isinstance(origin, dict) and origin.get("platform") == "slack":
            chat = str(origin.get("chat_id") or "")
            team = str(origin.get("scope_id") or "")
            if _TEAM.fullmatch(team) and _CHANNEL.fullmatch(chat):
                return "scheduled_slack", f"{team}:{chat}"
            if _DM.fullmatch(chat):
                return "scheduled_personal_dm", chat
        return "scheduled_unattributed", ""
    if platform == "slack" and context.get("platform") == "slack" and context.get("chat_id"):
        bound_id = context.get("session_id")
        if bound_id and bound_id != session_id:
            return "unattributed", ""
        chat_id = str(context["chat_id"])
        if context.get("chat_type") == "dm" and _DM.fullmatch(chat_id):
            return "personal_dm", chat_id
        team = str(context.get("scope_id") or "")
        if context.get("chat_type") in ("group", "channel") and (
                _TEAM.fullmatch(team) and _CHANNEL.fullmatch(chat_id)):
            return "service_slack", f"{team}:{chat_id}"
    if platform in ("api", "api_server"):
        return "api", ""
    return "unattributed", ""


def _count(usage: dict, primary: str, fallback: str) -> int | None:
    value = usage.get(primary)
    if value is None:
        value = usage.get(fallback)
    return value if type(value) is int and value >= 0 else None


def usage_counts(usage: object) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None
    input_tokens = _count(usage, "prompt_tokens", "input_tokens")
    output_tokens = _count(usage, "completion_tokens", "output_tokens")
    total_tokens = _count(usage, "total_tokens", "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def record(path: Path, *, request_id: str, session_id: str, profile: str, kind: str,
           place: str, platform: str, model: str, started_at: float, usage: object,
           task: str = "") -> bool:
    """Insert once per physical model request; return whether this request was new."""
    if not request_id:
        return False
    input_tokens, output_tokens, total_tokens = usage_counts(usage)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(path, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS requests (
            request_key TEXT PRIMARY KEY, started_at REAL NOT NULL,
            session_id TEXT NOT NULL, profile TEXT NOT NULL, kind TEXT NOT NULL,
            place TEXT NOT NULL, platform TEXT NOT NULL, model TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
            task TEXT NOT NULL DEFAULT '')""")
        if not any(row[1] == "task" for row in db.execute("PRAGMA table_info(requests)")):
            db.execute("ALTER TABLE requests ADD COLUMN task TEXT NOT NULL DEFAULT ''")
        result = db.execute(
            """INSERT OR IGNORE INTO requests
               (request_key, started_at, session_id, profile, kind, place, platform, model,
                input_tokens, output_tokens, total_tokens, task)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{session_id}:{request_id}", float(started_at or 0), session_id, profile, kind,
             place, platform[:40], model[:200], input_tokens, output_tokens, total_tokens, task[:80]))
        return result.rowcount == 1
