"""Behavior checks for fixed Slack commands and private feedback storage."""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

module_path = Path(__file__).resolve().parents[2] / "plugins/pilot-slack-controls/commands.py"
spec = importlib.util.spec_from_file_location("pilot_commands", module_path)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

checks = {}


def body(text, event_type="message", channel_type="channel", thread=None):
    event = {"type": event_type, "text": text, "user": "U1", "channel": "C1",
             "channel_type": channel_type, "ts": "100.2"}
    if thread:
        event["thread_ts"] = thread
    return {"type": "event_callback", "team_id": "T1", "authorizations": [{"user_id": "UBOT"}], "event": event}


checks["exact status"] = c.parse(body("<@UBOT> !status")) == ("status", "")
checks["status with words is a prompt"] = c.parse(body("<@UBOT> !status now")) is None
checks["one channel target"] = c.parse(body("<@UBOT> !routines <#C2|elsewhere>")) == ("routines", "<#C2|elsewhere>")
checks["two routine words are a prompt"] = c.parse(body("<@UBOT> !routines this channel")) is None
checks["invalid routine target gets usage"] = c.parse(body("<@UBOT> !routines banana")) == ("routines_usage", "")
checks["feedback keeps draft"] = c.parse(body("<@UBOT> !feedback missed a thread")) == ("feedback", "missed a thread")
checks["configure exact command"] = c.parse(body("<@UBOT> !configure")) == ("configure", "")
checks["configure with prose is ordinary prompt"] = c.parse(body("<@UBOT> !configure this")) is None
checks["channel requires mention"] = c.parse(body("!feedback")) is None
checks["ambient channel accepts bare command"] = c.parse(body("!routines"), allow_bare=True) == ("routines", "")
checks["adapter bot id works without envelope authorization"] = c.parse(
    body("<@UBOT> !status") | {"authorizations": []}, bot_user_id="UBOT") == ("status", "")
checks["DM accepts bare command"] = c.parse(body("!feedback", channel_type="im")) == ("feedback", "")
checks["app mention twin recognized"] = c.parse(body("<@UBOT> !status", event_type="app_mention")) == ("status", "")

os.environ["HERMES_TIMEZONE"] = "Asia/Seoul"
weekly = {"name": "weekly-agent-landscape", "schedule": {"kind": "cron", "expr": "0 9 * * 1"},
          "next_run_at": "2026-10-12T09:00:00+09:00", "enabled": True}
weekday = {"name": "channel-watch", "schedule": {"kind": "cron", "expr": "0 18 * * 1-5"},
           "next_run_at": "2026-10-08T18:00:00+09:00", "enabled": True}
formatted = c._format_jobs([weekly, weekday], "this channel")
checks["routine list names its channel"] = formatted.startswith("*Routines in this channel* (2)")
checks["weekly schedule is readable"] = "Every Monday at 09:00 Asia/Seoul" in formatted and "0 9 * * 1" not in formatted
checks["weekday schedule is readable"] = "Every weekday at 18:00 Asia/Seoul" in formatted and "0 18 * * 1-5" not in formatted
checks["string cron schedule is readable"] = "Every Monday" in c._readable_schedule({"schedule": "0 9 * * 1"})
checks["next run shows local and UTC time"] = "09:00 KST (00:00 UTC)" in formatted and "18:00 KST (09:00 UTC)" in formatted
checks["cross-day UTC date is explicit"] = "Sun 11 Oct 2026, 15:30 UTC" in c._next_run("2026-10-12T00:30:00+09:00")
checks["paused routine has no next run"] = "Next:" not in c._format_jobs([weekday | {"enabled": False}], "this channel")
checks["Slack interaction URL accepted"] = c.valid_slack_response_url(
    "https://hooks.slack.com/services/T1/B1/secret")
checks["Slack button URL accepted"] = c.valid_slack_response_url(
    "https://hooks.slack.com/actions/A1/B1/secret")
checks["other feedback destination rejected"] = not c.valid_slack_response_url(
    "https://hooks.slack.com.evil.example/services/T1/B1/secret")
checks["non-Slack path rejected"] = not c.valid_slack_response_url(
    "https://hooks.slack.com/not-an-interaction")
checks["malformed port rejected"] = not c.valid_slack_response_url(
    "https://hooks.slack.com:bad/services/T1/B1/secret")


class Runner:
    _running_agents = {"agent:main:slack:group:T1:C1:100.0": object()}
    _running_agents_ts = {"agent:main:slack:group:T1:C1:100.0": 100.0}


class Adapter:
    gateway_runner = Runner()
    _bot_user_id = "UBOT"

    def _early_reject_unauthorized(self, user, channel, is_dm):
        return user != "U1"

    def _slack_disable_dms(self):
        return False

    def _slack_allowed_channels(self):
        return set()

    def _slack_free_response_channels(self):
        return {"C1"}

    def _slack_require_mention_channels(self):
        return set()

    def _slack_strict_mention(self):
        return False

    def build_source(self, **kwargs):
        return kwargs

    def _source_session_key(self, source):
        return f"agent:main:slack:{source['chat_type']}:T1:{source['chat_id']}" + (
            f":{source['thread_id']}" if source["thread_id"] else "")


class MentionOnlyAdapter(Adapter):
    def _slack_require_mention_channels(self):
        return {"C1"}


class Client:
    def __init__(self):
        self.ephemeral = []
        self.posts = []

    async def chat_postEphemeral(self, **kwargs):
        self.ephemeral.append(kwargs)

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)

    async def conversations_info(self, **kwargs):
        return {"channel": {"id": kwargs["channel"], "is_channel": True,
                            "is_member": True, "is_ext_shared": False, "is_org_shared": False}}


class Mutes:
    def is_muted(self, channel, thread):
        return True


class Responses:
    def enabled(self, team, channel):
        return False


async def exercise():
    client = Client()
    handled = await c.handle_message(body("!status"), Adapter(), client, Mutes())
    checks["ambient status bypasses model"] = handled and len(client.ephemeral) == 1
    client = Client()
    handled = await c.handle_message(body("<@UBOT> !configure"), Adapter(), client,
                                     Mutes(), response_store=Responses())
    checks["configure gives private channel switch"] = (handled and len(client.ephemeral) == 1
        and "*OFF*" in client.ephemeral[0]["blocks"][0]["text"]["text"]
        and client.ephemeral[0]["blocks"][2]["elements"][0]["action_id"] == "pilot_channel_config_open")
    client = Client()
    handled = await c.handle_message(body("!status"), MentionOnlyAdapter(), client, Mutes())
    checks["mention-only override ignores bare channel command"] = not handled and not client.ephemeral
    client = Client()
    handled = await c.handle_message(body("<@UBOT> !status", thread="100.0"), Adapter(), client, Mutes())
    checks["status bypasses model and is private"] = handled and not client.posts and len(client.ephemeral) == 1
    checks["status reflects active thread and mute"] = "still working" in client.ephemeral[0]["text"] and "Muted: yes" in client.ephemeral[0]["text"]
    client = Client()
    handled = await c.handle_message(body("<@UBOT> !feedback draft"), Adapter(), client, Mutes())
    blocks = client.ephemeral[0]["blocks"] if client.ephemeral else []
    checks["feedback starts with private button"] = handled and not client.posts and blocks[2]["elements"][0]["action_id"] == "pilot_feedback_open"
    checks["feedback explains report context"] = "Slack user ID" in blocks[1]["elements"][0]["text"]
    client = Client()
    handled = await c.handle_message(body("<@UBOT> !status", event_type="app_mention"), Adapter(), client, Mutes())
    checks["app mention twin swallowed"] = handled and not client.posts and not client.ephemeral
    client = Client()
    await c.handle_message(body("<@UBOT> !status", thread="100.0") | {"event": body("<@UBOT> !status", thread="100.0")["event"] | {"user": "U2"}}, Adapter(), client, Mutes())
    checks["unauthorized user gets nothing"] = not client.posts and not client.ephemeral

    with tempfile.TemporaryDirectory() as temp:
        event_store = c.CommandEventStore(Path(temp) / "events.db")
        delivered = body("!feedback") | {"event_id": "Ev1"}
        first, second = Client(), Client()
        await c.handle_message(delivered, Adapter(), first, Mutes(), event_store)
        await c.handle_message(delivered, Adapter(), second, Mutes(), event_store)
        checks["retried feedback posts only one card"] = len(first.ephemeral) == 1 and not second.ephemeral
        reopened = c.CommandEventStore(Path(temp) / "events.db")
        third = Client()
        await c.handle_message(delivered, Adapter(), third, Mutes(), reopened)
        checks["retry after restart is still suppressed"] = not third.ephemeral
        fourth = Client()
        await c.handle_message(body("!feedback") | {"event_id": "Ev2"}, Adapter(), fourth, Mutes(), reopened)
        checks["new command still posts a card"] = len(fourth.ephemeral) == 1
        checks["event ledger is private"] = (Path(temp) / "events.db").stat().st_mode & 0o777 == 0o600


asyncio.run(exercise())

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "feedback.jsonl"
    c.save_feedback(path, {"user": "U1", "channel": "C1", "thread": "100.0"}, "a report")
    row = json.loads(path.read_text().strip())
    checks["feedback saved with context"] = row["text"] == "a report" and row["thread"] == "100.0"
    checks["feedback file is private"] = path.stat().st_mode & 0o777 == 0o600

for label, ok in checks.items():
    print(("ok  " if ok else "BAD ") + label)
print(f"{sum(checks.values())}/{len(checks)} passed")
sys.exit(0 if all(checks.values()) else 1)
