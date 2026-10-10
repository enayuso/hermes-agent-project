"""Human-facing Slack refusal when a configured monthly allowance is exhausted."""

from __future__ import annotations

import os
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .recovery import recovery_target

UNAVAILABLE = ("Pilot Agent can't check its usage allowance right now, so this "
               "request was not started. Please try again later.")


def _check(body: dict, adapter) -> str | None:
    policy_file = os.environ.get("PILOT_QUOTA_POLICY_FILE")
    ledger_file = os.environ.get("PILOT_QUOTA_LEDGER_FILE")
    library_dir = os.environ.get("PILOT_QUOTA_LIB_DIR")
    if not any((policy_file, ledger_file, library_dir)):
        return None
    if not all((policy_file, ledger_file, library_dir)):
        raise RuntimeError("incomplete usage admission configuration")
    library = Path(library_dir)
    if not (library / "usage" / "quota.py").is_file():
        raise RuntimeError("usage admission library is missing")
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from usage.origin import scopes
    from usage.policy import Policy
    from usage.quota import check_available, monthly_window

    policy = Policy.load(Path(policy_file))
    event = body["event"]
    team = str(body.get("team_id") or event.get("team") or "")
    channel = str(event["channel"])
    if event.get("channel_type") == "im":
        source = adapter.build_source(chat_id=channel, chat_type="dm",
                                      user_id=event["user"], thread_id=event.get("thread_ts"),
                                      scope_id=team or None)
        if getattr(source, "profile_route_rejected", False):
            raise RuntimeError("personal profile route was rejected")
        home = Path(adapter.gateway_runner._resolve_profile_home_for_source(source))
        profile = home.name
        scope_names = scopes("personal_dm", profile, channel)
    else:
        scope_names = scopes("service_slack", "service", f"{team}:{channel}")
    window = monthly_window(datetime.now(timezone.utc), policy.timezone)
    result = check_available(
        Path(ledger_file), scopes=scope_names, units=policy.context_window_tokens,
        limits=policy.limits_for(scope_names), window=window)
    if result.accepted:
        return None
    label = ("organization's" if result.denied_scope == "organization"
             else "this channel's" if result.denied_scope.startswith("channel:")
             else "your personal DM")
    reset = datetime.fromtimestamp(result.reset_at_utc, ZoneInfo(policy.timezone))
    return (f"Pilot Agent can't start this request because {label} monthly token "
            f"allowance has been reached. It resets on {reset:%d %B %Y at %H:%M} "
            f"{policy.timezone}. Ask an admin to adjust the allowance, or try again "
            "after it resets.")


async def limit_decision(body: dict, adapter) -> tuple[bool, dict | None]:
    """Return (refuse, Slack target). Unknown or ambient turns get no public notice."""
    if not any(os.environ.get(name) for name in (
            "PILOT_QUOTA_POLICY_FILE", "PILOT_QUOTA_LEDGER_FILE",
            "PILOT_QUOTA_LIB_DIR")):
        return False, None
    target = await recovery_target(body, adapter)
    try:
        message = await asyncio.to_thread(_check, body, adapter)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, RuntimeError):
        message = UNAVAILABLE
    if message is None:
        return False, None
    if target is not None:
        target["text"] = message
    return True, target
