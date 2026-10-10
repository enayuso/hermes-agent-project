"""Claude Tag's per-thread mute, for the Pilot Agent (docs: claude.com/docs/claude-tag/users/commands).

- The 👎 feedback button on a reply mutes that thread and posts a notice starting
  ":mute: … is muted in this thread". A 👎 *emoji reaction* does nothing.
- `@agent !mute` in a thread mutes it; at a channel's top level it posts a hint instead
  (muting is per thread). `@agent !unmute` unmutes.
- In a muted thread the agent ignores replies; an @-mention with a request unmutes the thread
  and is answered. Other threads, the top level and routine posts are unaffected.

Pure decision logic over Slack event/action payloads, so it can be tested without Slack.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

AGENT = "Pilot Agent"
MUTED_NOTICE = (f":mute: {AGENT} is muted in this thread. To bring it back, reply `@{AGENT} !unmute` "
                f"or @-mention it with your next request; to get a corrected answer, @-mention it and "
                f"say what was wrong.")
UNMUTED_NOTICE = f":sound: {AGENT} is unmuted in this thread."
TOP_LEVEL_HINT = (f":mute: Muting works per thread — reply `@{AGENT} !mute` (or `!unmute`) inside "
                  f"the thread you mean.")
ALREADY_UNMUTED = f"{AGENT} isn't muted in this thread."

_COMMAND = re.compile(r"^\s*!(mute|unmute)\s*$", re.IGNORECASE)


class MuteStore:
    """Muted threads, persisted as JSON: {"<channel>:<thread_ts>": {"by": user, "at": iso}}."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        try:
            self._data = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1))
        tmp.replace(self._path)

    def is_muted(self, channel: str, thread: str) -> bool:
        return f"{channel}:{thread}" in self._data

    def mute(self, channel: str, thread: str, by: str) -> bool:
        with self._lock:
            key = f"{channel}:{thread}"
            if key in self._data:
                return False
            self._data[key] = {"by": by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._save()
            return True

    def unmute(self, channel: str, thread: str) -> bool:
        with self._lock:
            if self._data.pop(f"{channel}:{thread}", None) is None:
                return False
            self._save()
            return True


def decide(body: dict, store: MuteStore) -> tuple[bool, list[dict]]:
    """Return (forward_to_agent, posts). Each post is {"channel", "thread_ts", "text"}."""
    if body.get("type") == "block_actions":
        for action in body.get("actions") or []:
            if action.get("action_id") == "hermes_feedback" and action.get("value") == "negative":
                channel = (body.get("channel") or {}).get("id", "")
                msg = body.get("message") or {}
                thread = msg.get("thread_ts") or msg.get("ts", "")
                user = (body.get("user") or {}).get("id", "")
                if channel and thread and store.mute(channel, thread, user):
                    return True, [{"channel": channel, "thread_ts": thread, "text": MUTED_NOTICE}]
        return True, []

    if body.get("type") != "event_callback":
        return True, []
    ev = body.get("event") or {}
    if ev.get("type") not in ("message", "app_mention") or ev.get("bot_id") or ev.get("subtype"):
        return True, []

    bot_user = ((body.get("authorizations") or [{}])[0] or {}).get("user_id", "")
    text = ev.get("text") or ""
    mention = f"<@{bot_user}>" if bot_user else None
    mentioned = bool(mention and mention in text)
    is_dm = ev.get("channel_type") == "im"
    channel, thread = ev.get("channel", ""), ev.get("thread_ts") or ""

    # Commands: `@agent !mute` / `!unmute` (no mention needed in a DM). Slack sends both a
    # `message` and an `app_mention` for one mention in a channel, so act on `message` only and
    # swallow the `app_mention` twin.
    stripped = text.replace(mention, "", 1) if mentioned else text
    cmd = _COMMAND.match(stripped)
    if cmd and (mentioned or is_dm):
        if ev.get("type") == "app_mention":
            return False, []
        if not thread:
            return False, [{"channel": channel, "thread_ts": ev.get("ts", ""), "text": TOP_LEVEL_HINT}]
        if cmd.group(1).lower() == "mute":
            store.mute(channel, thread, ev.get("user", ""))
            return False, [{"channel": channel, "thread_ts": thread, "text": MUTED_NOTICE}]
        changed = store.unmute(channel, thread)
        return False, [{"channel": channel, "thread_ts": thread,
                        "text": UNMUTED_NOTICE if changed else ALREADY_UNMUTED}]

    if thread and store.is_muted(channel, thread):
        if mentioned:
            store.unmute(channel, thread)  # an @-mention with a request unmutes and is answered
            return True, []
        return False, []
    return True, []
