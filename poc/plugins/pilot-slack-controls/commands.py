"""Fixed Slack commands modelled on Claude Tag, without starting a Hermes turn.

The native Slack adapter receives both message and app_mention for a mentioned command.
Its own message listener is registered before plugin listeners, so the controls middleware
handles the message and swallows the app_mention twin before either reaches Hermes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_COMMAND = re.compile(r"^!(status|routines|feedback|configure)(?:\s+([\s\S]*))?$", re.IGNORECASE)
_CHANNEL = re.compile(r"^(?:<#([CG][A-Z0-9]+)(?:\|[^>]*)?>|([CG][A-Z0-9]+))$")
_OPEN_ACTION = "pilot_feedback_open"
_SUBMIT_VIEW = "pilot_feedback_submit"
_WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


class CommandEventStore:
    """Claim Slack command events once, including Events API retries after a restart."""

    def __init__(self, path: Path):
        self.path = path

    def claim(self, body: dict) -> bool:
        event = body.get("event") or {}
        event_id = body.get("event_id")
        if not event_id:
            channel, ts = event.get("channel"), event.get("ts")
            if not channel or not ts:
                return True  # No stable identity; do not suppress another user's command.
            event_id = f"{body.get('team_id', '')}:{channel}:{ts}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_events (event_id TEXT PRIMARY KEY, seen_at REAL NOT NULL)")
            db.execute("DELETE FROM command_events WHERE seen_at < ?", (time.time() - 172800,))
            cursor = db.execute("INSERT OR IGNORE INTO command_events VALUES (?, ?)",
                                (str(event_id), time.time()))
            return cursor.rowcount == 1


def parse(body: dict, *, bot_user_id: str = "", allow_bare: bool = False) -> tuple[str, str] | None:
    """Recognize exact commands addressed to the bot or sent where it reads ambiently."""
    if body.get("type") != "event_callback":
        return None
    event = body.get("event") or {}
    if event.get("type") not in ("message", "app_mention") or event.get("bot_id") or event.get("subtype"):
        return None
    bot = bot_user_id or ((body.get("authorizations") or [{}])[0] or {}).get("user_id", "")
    raw = (event.get("text") or "").strip()
    mention = f"<@{bot}>" if bot else ""
    is_dm = event.get("channel_type") == "im"
    if mention and raw.startswith(mention):
        raw = raw[len(mention):].strip()
    elif not is_dm and not allow_bare:
        return None
    match = _COMMAND.fullmatch(raw)
    if not match:
        return None
    command, argument = match.group(1).lower(), (match.group(2) or "").strip()
    if command == "status" and argument:
        return None
    if command == "configure" and argument:
        return None
    if command == "routines" and (len(argument.split()) > 1 or
                                  (argument and not _CHANNEL.fullmatch(argument))):
        # One invalid word gets a usage note; multiple words are an ordinary prompt.
        return ("routines_usage", "") if len(argument.split()) <= 1 else None
    return command, argument


def _escape(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _source(adapter, channel: str, user: str, team: str, thread: str | None, is_dm: bool):
    return adapter.build_source(chat_id=channel, chat_type="dm" if is_dm else "group",
                                user_id=user, thread_id=thread, scope_id=team or None)


def _status(adapter, channel: str, user: str, team: str, thread: str | None,
            is_dm: bool, muted: bool) -> str:
    runner = getattr(adapter, "gateway_runner", None)
    if runner is None:
        return "I can't check live task status right now."
    try:
        source = _source(adapter, channel, user, team, thread, is_dm)
        key = adapter._source_session_key(source)
        running = getattr(runner, "_running_agents", {}) or {}
        started = getattr(runner, "_running_agents_ts", {}) or {}
        # In a DM, a top-level status checks every thread in that personal DM.
        keys = ([k for k in running if k == key or k.startswith(key + ":")]
                if is_dm and not thread else [key] if key in running else [])
        heading = "DM" if is_dm and not thread else "thread" if thread else "channel"
        if keys:
            elapsed = max(0, int(time.time() - min(float(started.get(k, time.time())) for k in keys)))
            progress = (f"I'm still working in this {heading} (started {elapsed // 60}m ago)."
                        if len(keys) == 1 else f"{len(keys)} tasks are still running in this {heading}.")
        else:
            progress = f"No task is running in this {heading} right now."
        note = f"*Pilot Agent in this {heading}*\n{progress}"
        if thread:
            note += f"\n• Muted: {'yes (unmute with `!unmute`)' if muted else 'no (mute with `!mute`)'}"
        return note
    except Exception:
        return "I can't check live task status right now."


def _matching_jobs(home: Path, chat_id: str, team: str) -> list[dict]:
    from cron.jobs import list_jobs, use_cron_store
    with use_cron_store(home):
        jobs = list_jobs(include_disabled=True)
    return [job for job in jobs if isinstance(job.get("origin"), dict)
            and job["origin"].get("platform") == "slack"
            and job["origin"].get("chat_id") == chat_id
            and (not job["origin"].get("scope_id") or job["origin"]["scope_id"] == team)]


def _readable_schedule(job: dict) -> str:
    """Describe the cron forms Hermes creates from simple weekday/time requests."""
    schedule = job.get("schedule") or {}
    expr = schedule.get("expr", "") if isinstance(schedule, dict) else schedule if isinstance(schedule, str) else ""
    fields = str(expr).split()
    if len(fields) == 5:
        minute, hour, day, month, weekday = fields
        if minute.isdigit() and hour.isdigit() and day == month == "*" and (
                0 <= int(minute) < 60 and 0 <= int(hour) < 24):
            if weekday == "*":
                days = "Every day"
            elif weekday == "1-5":
                days = "Every weekday"
            elif weekday in ("0,6", "6,0"):
                days = "Every weekend day"
            elif weekday in {str(i) for i in range(8)}:
                days = f"Every {_WEEKDAYS[int(weekday) % 7]}"
            else:
                days = ""
                parts = weekday.split(",")
                if len(parts) > 1 and all(p in {str(i) for i in range(8)} for p in parts):
                    days = "Every " + ", ".join(_WEEKDAYS[int(p) % 7] for p in parts)
            if days:
                zone = os.environ.get("HERMES_TIMEZONE") or os.environ.get("TZ") or "gateway time"
                return f"{days} at {int(hour):02d}:{int(minute):02d} {zone}"
    at = schedule.get("at") if isinstance(schedule, dict) else None
    return str(job.get("schedule_display") or expr or at or "Schedule unavailable")


def _next_run(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        next_at = datetime.fromisoformat(value)
        if next_at.tzinfo is None:
            return ""
        zone = os.environ.get("HERMES_TIMEZONE") or os.environ.get("TZ")
        try:
            local = next_at.astimezone(ZoneInfo(zone)) if zone else next_at
        except (ZoneInfoNotFoundError, ValueError):
            local = next_at
        utc = next_at.astimezone(timezone.utc)
        local_date = f"{local:%a} {local.day} {local:%b %Y}"
        utc_date = f"{utc:%a} {utc.day} {utc:%b %Y}"
        utc_label = (utc.strftime("%H:%M UTC") if local.date() == utc.date()
                     else f"{utc_date}, {utc:%H:%M} UTC")
        return f"Next: {local_date}, {local:%H:%M} {local.tzname() or 'local'} ({utc_label})"
    except ValueError:
        return ""


def _format_jobs(jobs: list[dict], place: str) -> str:
    if not jobs:
        return f"No routines are set up in {place}."
    lines = [f"*Routines in {place}* ({len(jobs)})"]
    for job in jobs[:30]:
        active = job.get("enabled", True)
        title = _escape(job.get("name") or job.get("id"))
        state = " _(paused)_" if not active else ""
        lines.append(f"• *{title}*{state} — {_escape(_readable_schedule(job))}")
        next_run = _next_run(job.get("next_run_at")) if active else ""
        if next_run:
            lines.append(f"  {_escape(next_run)}")
    if len(jobs) > 30:
        lines.append(f"…and {len(jobs) - 30} more.")
    return "\n".join(lines)[:3500]


async def _member(client, channel: str, user: str) -> bool:
    cursor = None
    while True:
        response = await client.conversations_members(channel=channel, limit=200, cursor=cursor)
        if user in (response.get("members") or []):
            return True
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return False


async def _routines(client, adapter, channel: str, user: str, team: str, argument: str,
                    is_dm: bool) -> tuple[str, bool]:
    target = (_CHANNEL.fullmatch(argument).group(1) or _CHANNEL.fullmatch(argument).group(2)) if argument else channel
    cross = target != channel
    if cross:
        try:
            info = (await client.conversations_info(channel=target)).get("channel") or {}
            # Claude Tag permits cross-channel listing only for a joined, internal channel.
            if not (info.get("is_channel") or info.get("is_group")) or info.get("is_ext_shared") or info.get("is_org_shared"):
                return "I can list routines only in an internal channel.", True
            bot = getattr(adapter, "_bot_user_id", "")
            if (info.get("is_member") is False or
                    (info.get("is_member") is None and (not bot or not await _member(client, target, bot))) or
                    not await _member(client, target, user)):
                return "I can list that channel's routines only if we are both members.", True
        except Exception:
            return "I couldn't verify access to that channel.", True
    try:
        runner = adapter.gateway_runner
        source = _source(adapter, target, user, team, None, is_dm and not cross)
        if getattr(source, "profile_route_rejected", False):
            return "That conversation's profile is unavailable.", cross
        home = runner._resolve_profile_home_for_source(source)
        jobs = _matching_jobs(Path(home), target, team)
        place = (f"<#{target}>" if cross else "this DM" if is_dm else "this channel")
        return _format_jobs(jobs, place), cross
    except Exception:
        return "I couldn't read routines right now.", cross


async def handle_message(body: dict, adapter, client, mute_store, event_store=None,
                         response_store=None) -> bool:
    """Handle a recognized command; return True when the event was consumed."""
    event = body.get("event") or {}
    channel = event.get("channel", "")
    try:
        # Match the gateway's free-response setting: this channel already routes bare
        # messages to Hermes, so exact bang commands must be intercepted there too.
        allow_bare = (channel in adapter._slack_free_response_channels()
                      and channel not in adapter._slack_require_mention_channels()
                      and not adapter._slack_strict_mention())
    except Exception:
        allow_bare = False
    parsed = parse(body, bot_user_id=getattr(adapter, "_bot_user_id", "") or "",
                   allow_bare=allow_bare)
    if parsed is None:
        return False
    channel, user = event.get("channel", ""), event.get("user", "")
    channel_type = event.get("channel_type")
    is_dm = channel_type == "im"
    is_any_dm = channel_type in ("im", "mpim")
    if not channel or not user or adapter._early_reject_unauthorized(user, channel, is_any_dm):
        return True
    if is_any_dm and adapter._slack_disable_dms():
        return True
    allowed = adapter._slack_allowed_channels()
    if not is_dm and allowed and channel not in allowed:
        return True
    if event.get("type") == "app_mention":
        return True  # The message event already handled this command.
    if event_store is not None and not event_store.claim(body):
        return True  # Slack retried a command already accepted for delivery.
    if not is_any_dm:
        try:
            info = (await client.conversations_info(channel=channel)).get("channel") or {}
            if info.get("is_ext_shared") or info.get("is_org_shared"):
                return True  # This bot has no paired external/Grid workspace.
        except Exception:
            return True  # Unknown channel scope fails closed.
    command, argument = parsed
    team = str(body.get("team_id") or event.get("team") or "")
    thread = event.get("thread_ts") or None
    kwargs = {"channel": channel, "user": user}
    if thread:
        kwargs["thread_ts"] = thread
    try:
        if command == "status":
            text = _status(adapter, channel, user, team, thread, is_dm,
                           bool(thread and mute_store.is_muted(channel, thread)))
            await client.chat_postEphemeral(text=text, **kwargs)
        elif command == "routines_usage":
            await client.chat_postEphemeral(
                text="Use `@Pilot Agent !routines` here, or `@Pilot Agent !routines #channel`.", **kwargs)
        elif command == "routines":
            text, private = await _routines(client, adapter, channel, user, team, argument, is_dm)
            if private:
                await client.chat_postEphemeral(text=text, **kwargs)
            else:
                await client.chat_postMessage(channel=channel, thread_ts=thread or event.get("ts"), text=text)
        elif command == "feedback":
            metadata = json.dumps({"channel": channel, "user": user, "thread": thread or "",
                                   "draft": argument[:1500]}, separators=(",", ":"))
            await client.chat_postEphemeral(
                text="Open the feedback form to review or edit your report before sending it.",
                blocks=[{"type": "section", "text": {"type": "mrkdwn",
                         "text": "*Feedback for Pilot Agent*\nOpen the form to review or edit your report before sending it."}},
                        {"type": "context", "elements": [{"type": "mrkdwn",
                         "text": "The report includes your Slack user ID and this conversation's ID."}]},
                        {"type": "actions", "elements": [{"type": "button", "action_id": _OPEN_ACTION,
                         "text": {"type": "plain_text", "text": "Open feedback form"},
                         "value": metadata}]}], **kwargs)
        elif command == "configure":
            if is_any_dm:
                await client.chat_postEphemeral(text="Respond automatically is a channel setting.", **kwargs)
            elif response_store is not None:
                metadata = {"channel": channel, "team": team, "user": user,
                            "thread": thread or ""}
                await client.chat_postEphemeral(
                    text="Configure Pilot Agent for this channel.",
                    blocks=configure_card(response_store.enabled(team, channel), metadata),
                    **kwargs)
    except Exception:
        # Never fall through to Hermes: a fixed command must not become a model turn.
        return True
    return True


def configure_card(enabled: bool, metadata: dict) -> list[dict]:
    state = "ON" if enabled else "OFF"
    return [{"type": "section", "text": {"type": "mrkdwn",
             "text": f"*Configure Pilot Agent for this channel*\nRespond automatically: *{state}*"}},
            {"type": "context", "elements": [{"type": "mrkdwn",
             "text": "When on, Pilot may answer untagged channel posts. Replies in threads it joined continue either way."}]},
            {"type": "actions", "elements": [{"type": "button",
             "action_id": "pilot_channel_config_open",
             "text": {"type": "plain_text", "text": "Change setting"},
             "value": json.dumps(metadata, separators=(",", ":"))}]}]


def configure_view(enabled: bool, metadata: dict) -> dict:
    options = [{"text": {"type": "plain_text", "text": "ON — may respond without a mention"}, "value": "on"},
               {"text": {"type": "plain_text", "text": "OFF — mention only"}, "value": "off"}]
    return {"type": "modal", "callback_id": "pilot_channel_config_submit",
            "private_metadata": json.dumps(metadata, separators=(",", ":")),
            "title": {"type": "plain_text", "text": "Channel replies"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [{"type": "input", "block_id": "auto_response",
                        "label": {"type": "plain_text", "text": "Respond automatically"},
                        "element": {"type": "static_select", "action_id": "choice",
                                    "options": options,
                                    "initial_option": options[0 if enabled else 1]}},
                       {"type": "context", "elements": [{"type": "mrkdwn",
                         "text": "This setting applies to this channel for everyone. Existing Pilot threads continue to receive replies."}]}]}


def feedback_view(draft: str, metadata: dict) -> dict:
    element = {"type": "plain_text_input", "action_id": "report", "multiline": True}
    if draft:
        element["initial_value"] = draft[:2000]
    return {"type": "modal", "callback_id": _SUBMIT_VIEW,
            "private_metadata": json.dumps(metadata, separators=(",", ":")),
            "title": {"type": "plain_text", "text": "Pilot Agent feedback"},
            "submit": {"type": "plain_text", "text": "Send"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [{"type": "input", "block_id": "feedback",
                        "label": {"type": "plain_text", "text": "What happened?"},
                        "element": element},
                       {"type": "context", "elements": [{"type": "mrkdwn",
                       "text": "Your report includes this text, your Slack user ID, and the conversation ID. It is stored for the Pilot Agent team to review."}]}]}


def valid_slack_response_url(value: str) -> bool:
    """Only send a feedback confirmation to Slack's interaction endpoint."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in {"hooks.slack.com", "hooks.slack-gov.com"}
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and (parsed.path.startswith("/actions/") or parsed.path.startswith("/services/"))
                and not parsed.fragment)
    except ValueError:
        return False


def save_feedback(path: Path, metadata: dict, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "user": metadata["user"], "channel": metadata["channel"],
              "thread": metadata.get("thread") or None, "text": text[:10000]}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(fd)
