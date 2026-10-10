#!/usr/bin/env python3
"""Move an existing Slack DM's Hermes history and notes into its personal profile.

Run with the gateway stopped, inside the Hermes container:
  python /opt/data/scripts/migrate-personal-dm-state.py PROFILE DM_CHAT_ID USER_ID
Uses Hermes' own session move and rekey methods, with SQLite snapshots for rollback.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hermes_cli.sessions_repair_profiles import Store, _Session

ROOT = Path("/opt/data")


def snapshot(db_path: Path, stamp: str) -> None:
    if not db_path.exists():
        return
    backup = ROOT / "backups" / "personal-dm-migration" / stamp / db_path.relative_to(ROOT)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as dest:
        source.backup(dest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("chat_id")
    parser.add_argument("user_id")
    args = parser.parse_args()
    home = ROOT / "profiles" / args.profile
    if (not home.is_dir() or home.parent != ROOT / "profiles" or
            not args.profile.startswith("person-") or not args.chat_id.startswith("D")):
        raise RuntimeError("invalid personal profile or DM chat")
    from gateway.status import resolve_gateway_liveness
    if resolve_gateway_liveness(profile_dir=ROOT, use_cache=False).running:
        raise RuntimeError("stop the gateway before moving sessions")
    with sqlite3.connect(f"file:{ROOT / 'state.db'}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT id, session_key FROM sessions WHERE chat_id=? AND source='slack' AND chat_type='dm' "
            "ORDER BY started_at, id", (args.chat_id,)).fetchall()
        if conn.execute("SELECT count(*) FROM sessions WHERE chat_id=? AND user_id<>?",
                        (args.chat_id, args.user_id)).fetchone()[0]:
            raise RuntimeError("DM history contains another user")
        keys = [key for _, key in rows]
        if keys:
            marks = ",".join("?" for _ in keys)
            pending = conn.execute(
                f"SELECT count(*) FROM delivery_obligations WHERE session_key IN ({marks}) "
                "AND state<>'delivered'", keys).fetchone()[0]
            if pending:
                raise RuntimeError(f"{pending} DM deliveries are unfinished; migration stopped")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot(ROOT / "state.db", stamp)
    snapshot(home / "state.db", stamp)
    src = Store("default", ROOT, routing=True)
    dst = Store(args.profile, home)
    handles = _Session(read_only=False)
    moved = 0
    try:
        src_db, dst_db = handles.db(src), handles.db(dst)
        for session_id, old_key in rows:
            payload = src_db.export_session_for_move(session_id)
            if payload is None or payload["session"].get("user_id") != args.user_id:
                raise RuntimeError(f"{session_id}: source row changed during migration")
            if not old_key.startswith("agent:main:slack:dm:"):
                raise RuntimeError(f"{session_id}: unexpected session namespace")
            dst_db.import_moved_session(payload, profile_name=args.profile)
            dst_db.rekey_legacy_main_sessions([session_id], args.profile)
            if dst_db.count_messages_all(session_id) < len(payload["messages"]):
                raise RuntimeError(f"{session_id}: target has fewer messages than source")
            if not src_db.delete_moved_session(session_id):
                raise RuntimeError(f"{session_id}: failed to remove source row")
            moved += 1
        routing = [(row["scope"], row["session_key"])
                   for row in src_db.list_gateway_routing_rows() if row["session_key"] in keys]
        if routing:
            src_db.delete_gateway_routing_rows(routing)
    finally:
        handles.close()
    if keys:
        with sqlite3.connect(ROOT / "state.db") as conn:
            marks = ",".join("?" for _ in keys)
            conn.execute(f"DELETE FROM delivery_obligations WHERE session_key IN ({marks}) "
                         "AND state='delivered'", keys)
    source_note = ROOT / "pilot-memory" / "dm" / f"{args.chat_id}.json"
    target_note = home / "pilot-memory" / "dm" / source_note.name
    if source_note.exists():
        target_note.parent.mkdir(parents=True, exist_ok=True)
        if target_note.exists() and target_note.read_bytes() != source_note.read_bytes():
            raise RuntimeError("personal DM note already differs; reconcile before removing source")
        if not target_note.exists():
            shutil.copy2(source_note, target_note)
        source_note.unlink()
        source_note.with_suffix(".lock").unlink(missing_ok=True)
    # Hermes still reads this legacy routing mirror on gateway startup. Leaving
    # agent:main DM keys here would reintroduce the old default-profile routes.
    mirror = ROOT / "sessions" / "sessions.json"
    removed_legacy = 0
    if mirror.exists():
        data = json.loads(mirror.read_text())
        if not isinstance(data, dict):
            raise RuntimeError("legacy sessions mirror is not a mapping")
        old = [key for key in data if key.startswith("agent:main:slack:dm:") and args.chat_id in key]
        if old:
            backup = ROOT / "backups" / "personal-dm-migration" / stamp / "sessions" / "sessions.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mirror, backup)
            for key in old:
                del data[key]
            temporary = mirror.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(mirror)
            removed_legacy = len(old)
    print(f"Moved {moved} sessions, DM note and {removed_legacy} legacy routes to {args.profile}; "
          f"snapshot {stamp}")


if __name__ == "__main__":
    main()
