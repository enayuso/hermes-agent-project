"""Unit tests for slack_read.py against a fake Slack client. Run inside the agent container:

    docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-read/test_slack_read.py
"""

import importlib.util
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[2]
PLUGIN = DATA / "plugins" / "pilot-slack-read"

spec = importlib.util.spec_from_file_location("slack_read", PLUGIN / "slack_read.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

CHANNELS = {
    "CPUBLIC01": {"id": "CPUBLIC01", "name": "general", "is_member": True},
    "CPUBLIC02": {"id": "CPUBLIC02", "name": "not-joined", "is_member": False},
    "GPRIVATE1": {"id": "GPRIVATE1", "name": "secret", "is_private": True, "is_member": True},
    "CSHARED01": {"id": "CSHARED01", "name": "partner", "is_ext_shared": True, "is_member": True},
    "DDIRECT01": {"id": "DDIRECT01", "is_im": True, "is_member": True},
}


class FakeClient:
    def __init__(self):
        self.calls = []

    def conversations_info(self, channel):
        self.calls.append(("info", channel))
        return {"channel": CHANNELS[channel]}

    def conversations_list(self, types, exclude_archived, limit, cursor=None):
        self.calls.append(("list", types))
        chans = [c for c in CHANNELS.values() if c.get("name")
                 and ("private_channel" in types or not c.get("is_private"))]
        return {"channels": chans, "response_metadata": {"next_cursor": ""}}

    def users_info(self, user, include_locale=False):
        return {"user": {"name": user.lower(), "profile": {"display_name": "Alex" if user == "U1" else ""}}}

    def conversations_history(self, channel, limit, cursor=None, oldest=None):
        self.calls.append(("history", channel, limit, cursor))
        if cursor is None:   # newest first, two pages
            msgs = [{"ts": "1791341300.000003", "user": "U1", "text": "hi <@U2> see <#CPUBLIC01|general>",
                     "reply_count": 2},
                    {"ts": "1791341200.000002", "bot_profile": {"name": "CI"}, "text": "build &lt;ok&gt;"}]
            return {"messages": msgs[:limit], "has_more": True, "response_metadata": {"next_cursor": "p2"}}
        return {"messages": [{"ts": "1791341100.000001", "user": "U2", "text": "x" * 3000}],
                "has_more": False, "response_metadata": {"next_cursor": ""}}

    def conversations_replies(self, channel, ts, limit, cursor=None):
        return {"messages": [{"ts": ts, "user": "U1", "text": "parent"},
                             {"ts": "1791341400.000001", "user": "U2", "text": "reply"}], "has_more": False}

    def pins_list(self, channel):
        return {"items": [{"message": {"ts": "1791341000.000001", "user": "U1", "text": "runbook"}},
                          {"file": {"name": "plan.pdf"}}]}


failures = 0


def check(label, cond):
    global failures
    failures += not cond
    print(("ok  " if cond else "BAD ") + label)


def denied(fn):
    try:
        fn()
    except sr.AccessDenied as exc:
        return str(exc)
    return None


def reader(current_channel="", thread=""):
    return sr.SlackReader(FakeClient(), lambda: {"channel": current_channel, "thread": thread})


# --- access rule -------------------------------------------------------------------------
r = reader("GPRIVATE1")
check("current private channel is readable", r.check_access("GPRIVATE1")["name"] == "secret")
check("current DM is readable", reader("DDIRECT01").check_access("DDIRECT01")["is_im"])
check("public member channel readable from elsewhere", r.check_access("CPUBLIC01")["name"] == "general")
check("private channel denied from elsewhere", denied(lambda: reader("CPUBLIC01").check_access("GPRIVATE1")))
check("DM denied from elsewhere", denied(lambda: reader("CPUBLIC01").check_access("DDIRECT01")))
check("Slack Connect channel denied from elsewhere", denied(lambda: reader("CPUBLIC01").check_access("CSHARED01")))
check("non-member public channel denied", denied(lambda: reader("CPUBLIC01").check_access("CPUBLIC02")))
check("no context (cron/API): public readable", reader().check_access("CPUBLIC01")["name"] == "general")
check("no context (cron/API): private denied", denied(lambda: reader().check_access("GPRIVATE1")))
check("no context + no channel arg is denied", denied(lambda: reader().resolve(None)))

# --- resolution --------------------------------------------------------------------------
check("empty channel -> current conversation", reader("GPRIVATE1").resolve("") == "GPRIVATE1")
check("#name -> id", reader().resolve("#general") == "CPUBLIC01")
check("unknown #name denied", denied(lambda: reader().resolve("#nope")))

# --- read_channel ------------------------------------------------------------------------
out = reader("CPUBLIC01").read_channel(limit=10)
msgs = out["messages"]
check("pages until limit or end (3 messages over 2 pages)", len(msgs) == 3)
check("oldest first", msgs[0]["ts"] < msgs[-1]["ts"])
check("mentions resolved to names", "@u2" in msgs[-1]["text"] and "#general" in msgs[-1]["text"])
check("bot author named", msgs[1]["author"] == "CI")
check("HTML entities decoded", msgs[1]["text"] == "build <ok>")
check("long text truncated", msgs[0]["text"].endswith("[truncated]") and len(msgs[0]["text"]) < 2100)
check("thread reply count surfaced", msgs[-1]["thread_replies"] == 2)
check("untrusted-content note present", out["note"] == sr.UNTRUSTED_NOTE)
big = reader("CPUBLIC01")
big.read_channel(limit=999)
check("limit capped at 200 per request", all(c[2] <= 200 for c in big._client.calls if c[0] == "history"))
check("read_channel enforces access", denied(lambda: reader("CPUBLIC01").read_channel("GPRIVATE1")))

# --- threads, pins, channel list --------------------------------------------------------
t = reader("CPUBLIC01", "1791341300.000003").read_thread()
check("read current thread with no args", [m["text"] for m in t["messages"]] == ["parent", "reply"])
check("bad thread_ts rejected", denied(lambda: reader("CPUBLIC01").read_thread("drop table")))
check("no thread and no current thread denied", denied(lambda: reader("CPUBLIC01").read_thread()))
p = reader("CPUBLIC01").list_pins()
check("pins: messages and files", p["pins"][0]["text"] == "runbook" and p["pins"][1]["file"] == "plan.pdf")
names = [c["name"] for c in reader().list_channels()["public_channels"]]
check("channel list = public, member, not shared", names == ["#general"])

# --- sender timezone context (pre_llm_call) ----------------------------------------------
from datetime import datetime, timezone as dt_tz
# Load the package __init__ (relative import of slack_read) to reach sender_time_context.
spec2 = importlib.util.spec_from_file_location("ksr", PLUGIN / "__init__.py",
                                               submodule_search_locations=[str(PLUGIN)])
ksr = importlib.util.module_from_spec(spec2); sys.modules["ksr"] = ksr; spec2.loader.exec_module(ksr)


class TzClient(FakeClient):
    def users_info(self, user, include_locale=False):
        tz = {"UTOKYO": ("Asia/Tokyo", "Japan Standard Time"), "USEOUL": ("Asia/Seoul", "Korea Standard Time")}
        if user not in tz:
            if user == "UNOZONE":
                return {"user": {"name": "member", "locale": "fr-FR", "profile": {}}}
            return {"user": {"name": user.lower(), "profile": {}}}
        return {"user": {"name": user.lower(), "tz": tz[user][0], "tz_label": tz[user][1],
                         "locale": "ja-JP" if user == "UTOKYO" else "ko-KR",
                         "profile": {"display_name": user.title()}}}


fixed = datetime(2026, 10, 7, 0, 30, tzinfo=dt_tz.utc)   # 09:30 in Seoul and Tokyo
tzr = sr.SlackReader(TzClient(), lambda: {"channel": "", "thread": ""})
same = ksr.sender_time_context("USEOUL", "Asia/Seoul", tzr, now=fixed)
other = ksr.sender_time_context("UTOKYO", "America/New_York", tzr, now=fixed)
check("sender line names tz and local time", same and "Asia/Seoul" in same and "2026-10-07 09:30" in same)
check("same tz as agent: no conversion advice", same and "convert" not in same)
check("different tz: tells agent to convert to its schedule tz", other and "America/New_York" in other and "convert" in other)
check("sender line carries Slack language setting", other and "ja-JP" in other)
locale_only = ksr.sender_time_context("UNOZONE", "Asia/Seoul", tzr, now=fixed)
check("language setting survives missing timezone", locale_only and "fr-FR" in locale_only and "local time" not in locale_only)
check("no Slack tz on profile: no injection", ksr.sender_time_context("UNOTZ", "Asia/Seoul", tzr, now=fixed) is None)
check("no user id: no injection", ksr.sender_time_context("", "Asia/Seoul", tzr) is None)

print(f"\n{'FAILED' if failures else 'all passed'} ({failures} failures)")
sys.exit(1 if failures else 0)
