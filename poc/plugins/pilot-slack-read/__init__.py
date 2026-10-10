"""Pilot Agent: read-only Slack tools (channel history, threads, pins, readable channels).

Closes Claude Tag's baseline gap: Hermes' Slack adapter only gives the agent the current thread.
Access rules live in ``slack_read.py``. The current conversation comes from Hermes' gateway
session context (``gateway.session_context.get_session_env``, the same accessor Hermes' own
tools use). That module is internal, not part of the documented plugin contract: if the import
ever fails, the plugin treats every call as having no current conversation, so only public
channels stay readable (fail closed), and logs a warning.
"""

from __future__ import annotations

import json
import logging
import os
from agent.secret_scope import get_secret

from .slack_read import AccessDenied, SlackReader

logger = logging.getLogger(__name__)

try:
    from gateway.session_context import get_session_env as _get_session_env
except Exception:  # noqa: BLE001
    _get_session_env = None
    logger.warning("pilot-slack-read: gateway session context unavailable; "
                   "only public channels will be readable")


def _current() -> dict:
    if _get_session_env is None or _get_session_env("HERMES_SESSION_PLATFORM", "").lower() != "slack":
        return {"channel": "", "thread": ""}
    return {"channel": _get_session_env("HERMES_SESSION_CHAT_ID", ""),
            "thread": _get_session_env("HERMES_SESSION_THREAD_ID", "")}


_reader: SlackReader | None = None


def _get_reader() -> SlackReader:
    global _reader
    if _reader is None:
        from slack_sdk import WebClient
        token = get_secret("SLACK_BOT_TOKEN")
        if not token:
            raise RuntimeError("Slack reading is unavailable in this profile")
        _reader = SlackReader(WebClient(token=token), _current)
    return _reader


def sender_time_context(user_id: str, agent_tz: str, reader, now=None) -> str | None:
    """One line telling the model who is asking, their timezone and local time (from their Slack
    profile), and the timezone the agent's own schedules run in. Claude Tag does the same: with no
    timezone named, it schedules in the sender's Slack profile timezone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    info = reader.user_timezone(user_id) if user_id else None
    if not info:
        return None
    line = f"[Sender context] {info['name']}."
    if info.get("tz"):
        try:
            local = (now or datetime.now(ZoneInfo(info["tz"]))).astimezone(ZoneInfo(info["tz"]))
            line += (f" Their timezone is {info['tz']} ({info['tz_label']}); their local "
                     f"time is {local:%Y-%m-%d %H:%M} ({local:%a}).")
        except Exception:  # noqa: BLE001 — preserve locale if zone data is unavailable
            pass
    if info.get("locale"):
        line += f" Their Slack language setting is {info['locale']}."
    if agent_tz and info.get("tz") and agent_tz != info["tz"]:
        line += (f" Your schedules run in {agent_tz}: convert any time they give you from "
                 f"{info['tz']} before creating a schedule, and state times back in their timezone.")
    return line


def _pre_llm_call(**kwargs):
    del kwargs
    if _get_session_env is None or not _available():
        return None
    if _get_session_env("HERMES_SESSION_PLATFORM", "").lower() != "slack":
        return None
    try:
        import hermes_time
        agent_tz = str(getattr(hermes_time.now().tzinfo, "key", "") or "")
    except Exception:  # noqa: BLE001
        agent_tz = os.environ.get("HERMES_TIMEZONE", "")
    return sender_time_context(_get_session_env("HERMES_SESSION_USER_ID", ""), agent_tz, _get_reader())


def _available() -> bool:
    return bool(get_secret("SLACK_BOT_TOKEN"))


def _run(name: str, fn, args: dict) -> str:
    try:
        result = fn(**args)
        logger.info("pilot-slack-read: %s ok (%s)", name,
                    {k: v for k, v in args.items() if v not in (None, "")})
        return json.dumps(result, ensure_ascii=False)
    except AccessDenied as exc:
        logger.info("pilot-slack-read: %s denied: %s", name, exc)
        return json.dumps({"error": "access_denied", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 — Slack API errors go back to the model as data
        code = getattr(getattr(exc, "response", None), "data", {}) or {}
        code = code.get("error") if isinstance(code, dict) else None
        hint = {"missing_scope": "The Slack app lacks a permission for this; ask an admin to "
                                 "update the app manifest and reinstall it.",
                "not_in_channel": "The agent is not in that channel; invite it with /invite.",
                "channel_not_found": "No such channel, or the agent cannot see it.",
                "ratelimited": "Slack rate limit hit; try again in a minute."}.get(code or "")
        logger.warning("pilot-slack-read: %s failed: %s", name, code or exc)
        return json.dumps({"error": code or "slack_error", "message": hint or str(exc)[:300]})


_CHANNEL = {"type": "string",
            "description": "Channel ID (C…/G…/D…) or #name. Omit for the conversation you are in."}

SCHEMAS = {
    "slack_read_channel": {
        "name": "slack_read_channel",
        "description": (
            "Read recent top-level messages of a Slack channel (oldest first). Use it to catch up on a "
            "channel, summarize discussion, find open questions, or check what was decided. Thread "
            "replies are not included: use slack_read_thread for messages that show thread_replies. "
            "Readable: the conversation you are in, and public channels you are a member of."),
        "parameters": {"type": "object", "properties": {
            "channel": _CHANNEL,
            "limit": {"type": "integer", "description": "Max messages, 1-200 (default 30)."},
            "since_hours": {"type": "number", "description": "Only messages from the last N hours."},
        }},
    },
    "slack_read_thread": {
        "name": "slack_read_thread",
        "description": (
            "Read all replies in a Slack thread. thread_ts is the parent message's ts from "
            "slack_read_channel. Omit both arguments to re-read the thread you are in."),
        "parameters": {"type": "object", "properties": {
            "thread_ts": {"type": "string", "description": "Parent message ts, e.g. 1791341110.929799."},
            "channel": _CHANNEL,
            "limit": {"type": "integer", "description": "Max messages, 1-200 (default 200)."},
        }},
    },
    "slack_list_pins": {
        "name": "slack_list_pins",
        "description": "List the pinned messages and files of a Slack channel (often its key references).",
        "parameters": {"type": "object", "properties": {"channel": _CHANNEL}},
    },
    "slack_list_channels": {
        "name": "slack_list_channels",
        "description": ("List the Slack channels you can read from anywhere (public channels you are a "
                        "member of), with their topics."),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _handler(name: str, method: str, allowed: tuple):
    def handle(args, **kwargs):
        del kwargs
        args = {k: v for k, v in (args or {}).items() if k in allowed}
        return _run(name, getattr(_get_reader(), method), args)
    return handle


def register(ctx):
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    for name, method, allowed in (
        ("slack_read_channel", "read_channel", ("channel", "limit", "since_hours")),
        ("slack_read_thread", "read_thread", ("thread_ts", "channel", "limit")),
        ("slack_list_pins", "list_pins", ("channel",)),
        ("slack_list_channels", "list_channels", ()),
    ):
        ctx.register_tool(name=name, toolset="pilot_slack_read", schema=SCHEMAS[name],
                          handler=_handler(name, method, allowed), check_fn=_available)
