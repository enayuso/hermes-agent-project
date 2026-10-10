"""Unit tests for controls.py (Claude Tag per-thread mute). Run inside the agent container:

    docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-controls/test_controls.py
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

DATA = Path(__file__).resolve().parents[2]
PLUGIN = DATA / "plugins" / "pilot-slack-controls"

spec = importlib.util.spec_from_file_location("controls", PLUGIN / "controls.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

BOT = "UBOT"
failures = 0


def check(label, cond):
    global failures
    failures += not cond
    print(("ok  " if cond else "BAD ") + label)


def msg(text, thread=None, etype="message", channel="C1", channel_type="channel", **extra):
    ev = {"type": etype, "text": text, "channel": channel, "channel_type": channel_type,
          "user": "UHUMAN", "ts": "1791000000.000200"}
    if thread:
        ev["thread_ts"] = thread
    ev.update(extra)
    return {"type": "event_callback", "authorizations": [{"user_id": BOT}], "event": ev}


def thumbs(value, thread="1791000000.000100", reply_ts="1791000000.000150"):
    return {"type": "block_actions", "channel": {"id": "C1"}, "user": {"id": "UHUMAN"},
            "message": {"ts": reply_ts, "thread_ts": thread},
            "actions": [{"action_id": "hermes_feedback", "value": value}]}


T = "1791000000.000100"
store = c.MuteStore(Path(tempfile.mkdtemp()) / "muted.json")

# --- 👎 button -----------------------------------------------------------------------------
fwd, posts = c.decide(thumbs("positive"), store)
check("👍 button: forwarded, nothing muted", fwd and not posts and not store.is_muted("C1", T))
fwd, posts = c.decide(thumbs("negative"), store)
check("👎 button: mutes the reply's thread", store.is_muted("C1", T))
check("👎 button: posts the mute notice in that thread",
      posts and posts[0]["thread_ts"] == T and posts[0]["text"].startswith(":mute:"))
check("👎 button: still forwarded so Hermes logs the click", fwd)
fwd, posts = c.decide(thumbs("negative"), store)
check("second 👎 on a muted thread: no duplicate notice", not posts)
top = c.decide(thumbs("negative", thread=None, reply_ts="1791000000.000900"), store)
check("👎 on a top-level reply mutes the thread under that reply", store.is_muted("C1", "1791000000.000900"))

# --- muted thread behaviour ------------------------------------------------------------------
fwd, posts = c.decide(msg("thanks", thread=T), store)
check("muted thread: plain reply is ignored", not fwd and not posts)
fwd, _ = c.decide(msg("hello", thread="1791000000.000555"), store)
check("other threads unaffected", fwd)
fwd, _ = c.decide(msg("top level"), store)
check("channel top level unaffected", fwd)
fwd, _ = c.decide(msg("routine", thread=T, bot_id="B1"), store)
check("bot / app posts pass through", fwd)
fwd, posts = c.decide(msg(f"<@{BOT}> try again, shorter", thread=T), store)
check("@-mention with a request unmutes and is answered", fwd and not store.is_muted("C1", T))

# --- commands ----------------------------------------------------------------------------
fwd, posts = c.decide(msg(f"<@{BOT}> !mute", thread=T), store)
check("@agent !mute mutes the thread, not forwarded", not fwd and store.is_muted("C1", T)
      and posts[0]["text"].startswith(":mute:"))
fwd, posts = c.decide(msg(f"<@{BOT}> !mute", thread=T, etype="app_mention"), store)
check("app_mention twin of a command is swallowed silently", not fwd and not posts)
fwd, posts = c.decide(msg(f"<@{BOT}> !unmute", thread=T), store)
check("@agent !unmute unmutes", not fwd and not store.is_muted("C1", T) and posts[0]["text"].startswith(":sound:"))
fwd, posts = c.decide(msg(f"<@{BOT}> !unmute", thread=T), store)
check("!unmute when not muted says so", posts and "isn't muted" in posts[0]["text"])
fwd, posts = c.decide(msg(f"<@{BOT}> !mute"), store)
check("!mute at the top level posts the per-thread hint", not fwd and "per thread" in posts[0]["text"])
fwd, _ = c.decide(msg("!mute", thread=T), store)
check("'!mute' without a mention in a channel is ordinary text", fwd and not store.is_muted("C1", T))
fwd, _ = c.decide(msg("!mute", thread=T, channel="D1", channel_type="im"), store)
check("'!mute' in a DM thread works without a mention", not fwd and store.is_muted("D1", T))

# --- persistence -------------------------------------------------------------------------
again = c.MuteStore(store._path)
check("mutes survive a restart", again.is_muted("D1", T))

print(f"\n{'FAILED' if failures else 'all passed'} ({failures} failures)")
sys.exit(1 if failures else 0)
