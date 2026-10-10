"""Channel switch persistence, routing, and model-tool authorization checks."""

import importlib.util
import tempfile
from pathlib import Path

folder = Path(__file__).resolve().parents[2] / "plugins/pilot-slack-controls"


def load(name):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


a = load("auto_response")
s = load("settings")


class Adapter:
    _bot_user_id = "UBOT"
    _team_bot_user_ids = {"TTEAM": "UBOT"}


class Client:
    def conversations_info(self, **kwargs):
        return {"channel": {"is_channel": True, "is_member": True,
                            "is_ext_shared": False, "is_org_shared": False}}

    def conversations_members(self, **kwargs):
        return {"members": ["UHUMAN"]}


def event(text="hello", *, channel="CCHAN", thread=None, bot=False, blocks=None):
    ev = {"type": "message", "channel": channel, "channel_type": "channel",
          "text": text, "user": "UHUMAN", "ts": "100.2"}
    if thread:
        ev["thread_ts"] = thread
    if bot:
        ev["bot_id"] = "B1"
    if blocks is not None:
        ev["blocks"] = blocks
    return {"type": "event_callback", "team_id": "TTEAM", "event": ev}


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "channel-response.json"
    store = a.ChannelResponseStore(path)
    assert store.enabled("TTEAM", "CCHAN")  # Claude Tag default: on.
    candidate = event()
    assert a.route_ambient(candidate, Adapter(), store)
    assert candidate["event"]["_hermes_force_process"] is True
    assert "unmentioned top-level" in a.ambient_turn_context("TTEAM", "CCHAN", "100.2")
    assert a.ambient_turn_context("TTEAM", "CCHAN", "100.2") is None
    assert store.set("TTEAM", "CCHAN", False, "UHUMAN")
    assert not store.enabled("TTEAM", "CCHAN")
    assert a.ChannelResponseStore(path).enabled("TTEAM", "CCHAN") is False
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("broken JSON")
    assert a.ChannelResponseStore(path).enabled("TTEAM", "CCHAN") is False
    store.set("TTEAM", "CCHAN", False, "UHUMAN")  # restore the valid state
    assert store.enabled("TOTHER", "CCHAN")  # workspace scope
    assert not a.route_ambient(event(), Adapter(), store)
    assert a.route_ambient(event("<@UBOT> hello"), Adapter(), store)
    block_mention = [{"type": "rich_text", "elements": [{"type": "rich_text_section",
                      "elements": [{"type": "user", "user_id": "UBOT"}]}]}]
    assert a.route_ambient(event("hello", blocks=block_mention), Adapter(), store)
    assert a.route_ambient(event("follow up", thread="100.0"), Adapter(), store)
    assert a.route_ambient(event("status", bot=True), Adapter(), store)
    assert store.set("TTEAM", "CCHAN", True, "UHUMAN")
    assert a.route_ambient(event(), Adapter(), store)
    current = {"platform": "slack", "chat_type": "group", "team": "TTEAM",
               "channel": "CCHAN", "user": "UHUMAN"}
    assert s.apply_setting("off", current, store, client=Client(), allowed_users={"UHUMAN"})["respond_automatically"] is False
    assert s.apply_setting("on", current | {"user": "UOTHER"}, store,
                           client=Client(), allowed_users={"UHUMAN"})["error"] == "unavailable"
    assert s.apply_setting("on", current | {"chat_type": "dm"}, store,
                           client=Client(), allowed_users={"UHUMAN"})["error"] == "unavailable"
    assert store.enabled("TTEAM", "CCHAN") is False

print("automatic channel reply checks passed")
