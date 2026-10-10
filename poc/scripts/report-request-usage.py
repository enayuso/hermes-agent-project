#!/usr/bin/env python3
"""Read-only physical model-request usage from the Pilot audit ledgers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


def _ledgers(data_dir: Path) -> list[Path]:
    homes = [data_dir]
    profiles = data_dir / "profiles"
    if profiles.is_dir():
        homes.extend(p for p in sorted(profiles.iterdir()) if p.is_dir())
    return [home / "plugin-data" / "pilot-usage" / "requests.db" for home in homes]


def report(data_dir: Path, since: float | None) -> list[dict]:
    totals: dict[tuple[str, str, str], dict] = {}
    for path in _ledgers(data_dir):
        if not path.is_file():
            continue
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            has_task = any(row[1] == "task" for row in db.execute("PRAGMA table_info(requests)"))
            task_column = "task" if has_task else "''"
            for profile, kind, place, input_tokens, output_tokens, total_tokens, task in db.execute(
                    f"""SELECT profile, kind, place, input_tokens, output_tokens, total_tokens, {task_column}
                       FROM requests WHERE (? IS NULL OR started_at >= ?)""", (since, since)):
                key = (profile, kind, place)
                entry = totals.setdefault(key, {"profile": profile, "kind": kind, "place": place,
                                                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                "total_tokens": 0, "unknown_usage_calls": 0,
                                                "auxiliary_calls": 0})
                entry["calls"] += 1
                if task:
                    entry["auxiliary_calls"] += 1
                if total_tokens is None:
                    entry["unknown_usage_calls"] += 1
                entry["input_tokens"] += input_tokens or 0
                entry["output_tokens"] += output_tokens or 0
                entry["total_tokens"] += total_tokens or 0
    return [totals[key] for key in sorted(totals)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--hours", type=float, default=24, help="Lookback window; omit with --all")
    parser.add_argument("--all", action="store_true", help="Include every audited request")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()
    if not args.all and args.hours <= 0:
        parser.error("--hours must be positive")
    rows = report(args.data_dir, None if args.all else time.time() - args.hours * 3600)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(("All audited requests" if args.all else f"Last {args.hours:g} hours")
          + "; tokens and calls, not dollar cost")
    for row in rows:
        print(f"{row['profile']} | {row['kind']} | {row['place'] or '-'} | "
              f"{row['calls']} calls | {row['input_tokens']} input | {row['output_tokens']} output | "
              f"{row['auxiliary_calls']} auxiliary | {row['unknown_usage_calls']} unknown-usage calls")
    if not rows:
        print("No audited requests in this window.")


if __name__ == "__main__":
    main()
