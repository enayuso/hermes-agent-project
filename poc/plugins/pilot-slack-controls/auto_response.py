"""Per-channel Claude Tag-style automatic reply setting.

The setting is on for a newly joined channel and off where a member disables it.
Threads already joined by Pilot continue independently of this setting.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"^[A-Z0-9]{3,}$")


class AmbientCandidates:
    """One-shot, short-lived markers for top-level posts admitted by auto mode."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str, str], float] = {}

    def remember(self, team: str, channel: str, ts: str) -> None:
        if not ts:
            return
        now = time.monotonic()
        with self._lock:
            self._pending = {k: t for k, t in self._pending.items() if now - t < 300}
            while len(self._pending) >= 4096:
                self._pending.pop(next(iter(self._pending)))
            self._pending[(team, channel, ts)] = now

    def take(self, team: str, channel: str, ts: str) -> bool:
        with self._lock:
            at = self._pending.pop((team, channel, ts), None)
        return at is not None and time.monotonic() - at < 300


ambient_candidates = AmbientCandidates()


def ambient_turn_context(team: str, channel: str, thread: str) -> str | None:
    if not ambient_candidates.take(team, channel, thread):
        return None
    return ("[Pilot channel context] This is an unmentioned top-level channel post. "
            "Claude Tag usually stays quiet on such posts. A casual greeting or conversation "
            "between people is not addressed to you: return exactly [SILENT]. Reply only if "
            "there is a concrete need you can meet from the channel's context or memory, or "
            "a standing channel instruction assigns you this kind of work. Keep any reply in "
            "the sender's language.")


class ChannelResponseStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._corrupt = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("channel setting store must be a mapping")
            self._data = data
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError):
            self._data = {}
            self._corrupt = True

    @staticmethod
    def _key(team: str, channel: str) -> str:
        if not _ID.fullmatch(team) or not _ID.fullmatch(channel) or not channel.startswith(("C", "G")):
            raise ValueError("invalid Slack workspace or channel")
        return f"{team}:{channel}"

    def enabled(self, team: str, channel: str) -> bool:
        key = self._key(team, channel)
        with self._lock:
            if self._corrupt:
                return False  # A damaged store must not re-enable an off channel.
            item = self._data.get(key)
            if item is None:
                return True
            return item.get("enabled") is True if isinstance(item, dict) else False

    def set(self, team: str, channel: str, enabled: bool, by: str) -> bool:
        key = self._key(team, channel)
        if not _ID.fullmatch(by) or not isinstance(enabled, bool):
            raise ValueError("invalid setting change")
        with self._lock:
            previous = self._data.get(key)
            changed = not isinstance(previous, dict) or previous.get("enabled") is not enabled
            updated = dict(self._data)
            updated[key] = {"enabled": enabled, "by": by,
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8")
            temp = self.path.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
            self._data = updated
            self._corrupt = False
            return changed


def route_ambient(body: dict, adapter, store: ChannelResponseStore) -> bool:
    """Return False to swallow an untagged channel root; mark on-mode roots for Hermes.

    Pinned Hermes v2026.9.24 recognizes _hermes_force_process for an otherwise
    mention-gated event. Its own allowlist, authorization, bot and guest checks still run.
    Other event types and joined-thread replies keep their native routing.
    """
    if body.get("type") != "event_callback":
        return True
    event = body.get("event") or {}
    if (event.get("type") != "message" or event.get("subtype") or event.get("bot_id")
            or event.get("_hermes_force_process")
            or event.get("channel_type") not in ("channel", "group")):
        return True
    if event.get("thread_ts") and event["thread_ts"] != event.get("ts"):
        return True
    team = str(body.get("team_id") or event.get("team") or "")
    channel = str(event.get("channel") or "")
    if not team or not channel:
        return True  # Hermes handles malformed events itself.
    known_bots = getattr(adapter, "_team_bot_user_ids", {}) or {}
    bot = known_bots.get(team) or getattr(adapter, "_bot_user_id", "") or ""
    text = event.get("text") or ""
    def block_mention(node, quoted=False):
        if isinstance(node, list):
            return any(block_mention(part, quoted) for part in node)
        if not isinstance(node, dict):
            return False
        quoted = quoted or node.get("type") == "rich_text_quote"
        if not quoted and node.get("type") == "user" and node.get("user_id") == bot:
            return True
        return any(block_mention(node.get(key), quoted) for key in ("elements", "element"))

    if bot and (f"<@{bot}>" in text or f"<@{bot}|" in text
                or block_mention(event.get("blocks"))):
        return True
    if not store.enabled(team, channel):
        return False
    event["_hermes_force_process"] = True
    ambient_candidates.remember(team, channel, str(event.get("ts") or ""))
    return True
