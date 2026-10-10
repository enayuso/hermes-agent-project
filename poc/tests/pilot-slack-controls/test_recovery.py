"""Recovery preflight must notify only authorized human Slack turns."""

import importlib.util
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch


source = Path(__file__).resolve().parents[2] / "plugins/pilot-slack-controls/recovery.py"
spec = importlib.util.spec_from_file_location("pilot_recovery", source)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class Adapter:
    _bot_user_id = "UBOT"

    def _early_reject_unauthorized(self, user, channel, is_dm):
        return user != "U1"

    async def _should_wake_on_unmentioned_message(self, parent, channel, user, is_reply,
                                                   team_id, chat_type):
        return parent == "99.0"


def event(**changes):
    message = {"type": "message", "channel": "C1", "channel_type": "channel",
               "user": "U1", "ts": "100.1", "text": "<@UBOT> help"}
    message.update(changes)
    return {"type": "event_callback", "team_id": "T1", "event": message}


async def routing_checks():
    target = recovery.recovery_target
    assert (await target(event(), Adapter()))["thread_ts"] == "100.1"
    assert (await target(event(thread_ts="99.0", text="continue"), Adapter()))["thread_ts"] == "99.0"
    assert "thread_ts" not in await target(event(channel="D1", channel_type="im"), Adapter())
    assert await target(event(text="hey everyone"), Adapter()) is None
    assert await target(event(text="hey everyone", _hermes_force_process=True), Adapter()) is None
    assert await target(event(thread_ts="88.0", text="continue"), Adapter()) is None
    assert await target(event(user="U2"), Adapter()) is None
    assert await target(event(bot_id="B1"), Adapter()) is None
    assert await target(event(subtype="message_changed"), Adapter()) is None
    assert await target(event(type="reaction_added"), Adapter()) is None
    assert await target({"type": "url_verification", "event": event()["event"]}, Adapter()) is None


asyncio.run(routing_checks())

with tempfile.TemporaryDirectory() as directory:
    token = Path(directory) / "token"
    token.write_text("test-token")
    with patch.dict("os.environ", {"PILOT_SANDBOX_BROKER_URL": "http://127.0.0.1:1",
                               "PILOT_SANDBOX_TOKEN_FILE": str(token)}):
        assert recovery.broker_ready(timeout=0.1) is False
    with patch.dict("os.environ", {"PILOT_SANDBOX_BROKER_URL": "",
                               "PILOT_SANDBOX_TOKEN_FILE": str(token)}):
        assert recovery.broker_ready() is False

print("recovery routing and fail-closed health checks passed")
