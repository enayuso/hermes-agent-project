"""Atomic monthly token reservations for a future model-request admission proxy.

No quota is active merely by importing this module. The caller must identify
the verified origin and pass explicit limits and a worst-case request charge.
Prompts, responses, and credentials never enter this ledger.
"""

from __future__ import annotations

import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    start_utc: int
    end_utc: int


@dataclass(frozen=True)
class Admission:
    accepted: bool
    denied_scope: str = ""
    remaining: int = 0
    reset_at_utc: int = 0
    duplicate: bool = False


def monthly_window(at: datetime, timezone_name: str) -> Window:
    if at.tzinfo is None:
        raise ValueError("admission time must be timezone-aware")
    local = at.astimezone(ZoneInfo(timezone_name))
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return Window(int(start.astimezone(timezone.utc).timestamp()),
                  int(end.astimezone(timezone.utc).timestamp()))


def _schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS reservations (
        request_key TEXT NOT NULL,
        scope TEXT NOT NULL,
        period_start INTEGER NOT NULL,
        units INTEGER NOT NULL CHECK(units >= 0),
        status TEXT NOT NULL CHECK(status IN ('reserved', 'final')),
        settled_units INTEGER CHECK(settled_units >= 0),
        session_id TEXT NOT NULL DEFAULT '',
        origin_kind TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (request_key, scope, period_start))""")
    # Offline prototype ledgers created before settled_units stored the final
    # charge in units. COALESCE below preserves their existing accounting.
    columns = {row[1] for row in db.execute("PRAGMA table_info(reservations)")}
    if "settled_units" not in columns:
        db.execute("ALTER TABLE reservations ADD COLUMN settled_units "
                   "INTEGER CHECK(settled_units >= 0)")
    if "session_id" not in columns:
        db.execute("ALTER TABLE reservations ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
    if "origin_kind" not in columns:
        db.execute("ALTER TABLE reservations ADD COLUMN origin_kind TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE INDEX IF NOT EXISTS reservations_scope_period
                  ON reservations(scope, period_start)""")


def reserve(path: Path, *, request_key: str, scopes: tuple[str, ...], units: int,
            limits: dict[str, int], window: Window, session_id: str = "",
            origin_kind: str = "") -> Admission:
    """Reserve worst-case units for every scope in one SQLite write transaction."""
    if not request_key or not scopes or len(scopes) != len(set(scopes)):
        raise ValueError("request key and distinct scopes are required")
    if type(units) is not int or units <= 0:
        raise ValueError("reservation units must be a positive integer")
    if any(scope not in limits or type(limits[scope]) is not int or limits[scope] <= 0
           for scope in scopes):
        raise ValueError("every scope requires a positive integer limit")
    if window.end_utc <= window.start_utc:
        raise ValueError("invalid quota window")
    if (not isinstance(session_id, str) or len(session_id) > 300
            or not isinstance(origin_kind, str) or len(origin_kind) > 40):
        raise ValueError("invalid reservation origin metadata")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("quota ledger must not be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.close(fd)
    with sqlite3.connect(path, timeout=10, isolation_level=None) as db:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("BEGIN IMMEDIATE")
        try:
            _schema(db)
            existing = db.execute(
                "SELECT scope, units, status, session_id, origin_kind FROM reservations "
                "WHERE request_key=? AND period_start=?",
                (request_key, window.start_utc)).fetchall()
            if existing:
                if {row[0] for row in existing} != set(scopes):
                    raise ValueError("request key reused with different scopes")
                if any(row[1] != units for row in existing):
                    raise ValueError("request key reused with different reservation units")
                if any(row[3:] != (session_id, origin_kind) for row in existing):
                    raise ValueError("request key reused with different origin metadata")
                result = Admission(True, reset_at_utc=window.end_utc, duplicate=True)
                db.execute("COMMIT")
                return result
            for scope in scopes:
                used = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(settled_units, units)),0) "
                    "FROM reservations "
                    "WHERE scope=? AND period_start=?",
                    (scope, window.start_utc)).fetchone()[0]
                remaining = max(0, limits[scope] - used)
                if units > remaining:
                    result = Admission(False, scope, remaining, window.end_utc)
                    db.execute("COMMIT")
                    return result
            for scope in scopes:
                db.execute("INSERT INTO reservations "
                           "(request_key, scope, period_start, units, status, session_id, origin_kind) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (request_key, scope, window.start_utc, units, "reserved",
                            session_id, origin_kind))
            db.execute("COMMIT")
            return Admission(True, reset_at_utc=window.end_utc)
        except BaseException:
            db.execute("ROLLBACK")
            raise


def check_available(path: Path, *, scopes: tuple[str, ...], units: int,
                    limits: dict[str, int], window: Window) -> Admission:
    """Advisory read for a human-facing refusal; the proxy still reserves atomically.

    A concurrent request may consume the remaining allowance after this check.
    Never use this result alone as the enforcement boundary.
    """
    if not scopes or len(scopes) != len(set(scopes)) or type(units) is not int or units <= 0:
        raise ValueError("valid scopes and reservation units are required")
    if any(scope not in limits or type(limits[scope]) is not int or limits[scope] <= 0
           for scope in scopes):
        raise ValueError("every scope requires a positive integer limit")
    if path.is_symlink():
        raise ValueError("quota ledger must not be a symlink")
    if not path.exists():
        return next((Admission(False, scope, limits[scope], window.end_utc)
                     for scope in scopes if units > limits[scope]),
                    Admission(True, reset_at_utc=window.end_utc))
    with sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        for scope in scopes:
            try:
                used = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(settled_units, units)),0) "
                    "FROM reservations WHERE scope=? AND period_start=?",
                    (scope, window.start_utc)).fetchone()[0]
            except sqlite3.OperationalError as exc:
                if "no such table: reservations" not in str(exc):
                    raise
                used = 0
            remaining = max(0, limits[scope] - used)
            if units > remaining:
                return Admission(False, scope, remaining, window.end_utc)
    return Admission(True, reset_at_utc=window.end_utc)


def finalize(path: Path, *, request_key: str, period_start: int, actual_units: int) -> None:
    """Commit metered use; a missing usage report retains the full reservation."""
    if type(actual_units) is not int or actual_units < 0:
        raise ValueError("actual units must be a nonnegative integer")
    with sqlite3.connect(path, timeout=10, isolation_level=None) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute(
                "SELECT scope, units, status, settled_units FROM reservations "
                "WHERE request_key=? AND period_start=?",
                (request_key, period_start)).fetchall()
            if not rows:
                raise KeyError("reservation not found")
            if any(status == "reserved" and actual_units > reserved
                   for _, reserved, status, _ in rows):
                raise ValueError("actual use exceeds reserved units")
            if any(status == "final" for _, _, status, _ in rows):
                if not all(status == "final" and
                           (settled if settled is not None else units) == actual_units
                           for _, units, status, settled in rows):
                    raise ValueError("request already finalized differently")
            else:
                db.execute("UPDATE reservations SET settled_units=?, status='final' "
                           "WHERE request_key=? AND period_start=?",
                           (actual_units, request_key, period_start))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
