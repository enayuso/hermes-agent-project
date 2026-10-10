"""Early, user-visible Slack refusal when the isolated execution broker is down."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path


NOTICE = ("Pilot Agent's execution service is temporarily unavailable, so this "
          "request was not started. Please send it again in a moment.")


def broker_ready(timeout: float = 0.7) -> bool:
    """Check the authenticated broker without inheriting proxy settings."""
    url = os.environ.get("PILOT_SANDBOX_BROKER_URL", "").rstrip("/")
    token_path = os.environ.get("PILOT_SANDBOX_TOKEN_FILE", "")
    if not url or not token_path:
        return False
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
        if not token:
            return False
        request = urllib.request.Request(url + "/health",
                                         headers={"Authorization": "Bearer " + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


async def recovery_target(body: dict, adapter) -> dict | None:
    """Choose only a turn Hermes would wake for; ambient posts stay silent."""
    if body.get("type") != "event_callback":
        return None
    event = body.get("event") or {}
    if event.get("type") not in ("message", "app_mention") or event.get("subtype") or event.get("bot_id"):
        return None
    channel, user = event.get("channel"), event.get("user")
    if not channel or not user or not event.get("ts"):
        return None
    is_dm = event.get("channel_type") == "im"
    if adapter._early_reject_unauthorized(user, channel, is_dm):
        return None
    if event.get("_hermes_force_process"):
        return None  # An unmentioned ambient post should not produce an outage notice.
    team = str(body.get("team_id") or event.get("team") or "")
    bot_ids = getattr(adapter, "_team_bot_user_ids", {}) or {}
    bot = bot_ids.get(team) or getattr(adapter, "_bot_user_id", "") or ""
    text = str(event.get("text") or "")
    mentioned = event.get("type") == "app_mention" or bool(bot and
                 (f"<@{bot}>" in text or f"<@{bot}|" in text))
    if not is_dm and not mentioned:
        parent = event.get("thread_ts")
        if not parent or parent == event.get("ts"):
            return None
        try:
            joined = await adapter._should_wake_on_unmentioned_message(
                parent, channel, user, True, team_id=team,
                chat_type="group" if event.get("channel_type") in ("channel", "group") else "dm")
        except Exception:
            return None
        if not joined:
            return None
    thread = event.get("thread_ts") or event["ts"]
    return {"channel": channel, "text": NOTICE,
            **({"thread_ts": thread} if not is_dm or event.get("thread_ts") else {})}
