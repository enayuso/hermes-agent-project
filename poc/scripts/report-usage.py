#!/usr/bin/env python3
"""Read-only, lifetime token usage by Hermes profile and conversation place.

Hermes keeps cumulative model usage per session, so this report deliberately does
not claim a daily/monthly window or dollar spend. Scheduled runs without a stored
Slack place remain in a separate bucket rather than being charged to a channel.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path


def _profile_databases(data_dir: Path) -> list[tuple[str, Path]]:
    found = []
    root_db = data_dir / "state.db"
    if root_db.is_file():
        found.append(("service", root_db))
    profiles = data_dir / "profiles"
    if profiles.is_dir():
        for directory in sorted(profiles.iterdir()):
            db = directory / "state.db"
            if directory.is_dir() and db.is_file():
                found.append((directory.name, db))
    return found


def _place(profile: str, source: str, chat_type: str | None,
           chat_id: str | None, origin_json: str | None) -> str:
    if source == "slack" and chat_id:
        if chat_type == "dm":
            return f"DM ({profile})"
        scope = ""
        if origin_json:
            try:
                origin = json.loads(origin_json)
                scope = str(origin.get("scope_id") or "") if isinstance(origin, dict) else ""
            except (ValueError, TypeError):
                pass
        return f"Slack channel {scope + ':' if scope else ''}{chat_id}"
    if source == "cron":
        return "Scheduled runs (unattributed to channel)"
    if source == "api_server":
        return "Pilot API"
    return f"{source or 'unknown'} (unattributed)"


def collect(data_dir: Path) -> list[dict]:
    """Aggregate rows without reading messages, prompts, or credentials."""
    totals = defaultdict(lambda: {"sessions": set(), "calls": 0, "input_tokens": 0,
                                  "output_tokens": 0, "cache_read_tokens": 0,
                                  "cache_write_tokens": 0, "reasoning_tokens": 0})
    for profile, db in _profile_databases(data_dir):
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            rows = con.execute("""
                SELECT s.id, s.source, s.chat_type, s.chat_id, s.origin_json,
                       u.api_call_count, u.input_tokens, u.output_tokens,
                       u.cache_read_tokens, u.cache_write_tokens, u.reasoning_tokens
                FROM session_model_usage AS u
                JOIN sessions AS s ON s.id = u.session_id
            """)
            for (session_id, source, chat_type, chat_id, origin_json, calls,
                 input_tokens, output_tokens, cache_read, cache_write, reasoning) in rows:
                key = (profile, _place(profile, source, chat_type, chat_id, origin_json))
                entry = totals[key]
                entry["sessions"].add(session_id)
                for field, value in (("calls", calls), ("input_tokens", input_tokens),
                                     ("output_tokens", output_tokens),
                                     ("cache_read_tokens", cache_read),
                                     ("cache_write_tokens", cache_write),
                                     ("reasoning_tokens", reasoning)):
                    entry[field] += int(value or 0)
    return [{"profile": profile, "place": place, "sessions": len(value["sessions"]),
             **{key: value[key] for key in ("calls", "input_tokens", "output_tokens",
                                            "cache_read_tokens", "cache_write_tokens",
                                            "reasoning_tokens")}}
            for (profile, place), value in sorted(totals.items())]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--json", action="store_true", help="machine-readable rows")
    args = parser.parse_args()
    rows = collect(args.data_dir)
    if args.json:
        print(json.dumps({"scope": "lifetime", "cost": "unavailable", "rows": rows}, indent=2))
        return
    print("Lifetime Hermes usage; tokens and model calls, not dollar cost")
    for row in rows:
        print(f"{row['profile']} | {row['place']} | {row['sessions']} sessions | "
              f"{row['calls']} calls | {row['input_tokens']} input | {row['output_tokens']} output")


if __name__ == "__main__":
    main()
