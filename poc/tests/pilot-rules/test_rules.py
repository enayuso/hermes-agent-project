"""Checks pilot-rules.yaml decisions for representative tools. Run inside the agent container:

    docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-rules/test_rules.py
"""

import importlib.util
import sys
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[2]
PLUGIN = DATA / "plugins" / "pilot-rules"
here = PLUGIN

spec = importlib.util.spec_from_file_location("pilot_rules", here / "__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

rules_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "pilot-rules.yaml"
raw = yaml.safe_load(rules_path.read_text())
rules = {"default": raw.get("default", "allow"), "allow": raw.get("allow") or [],
         "ask": raw.get("ask") or [], "block": raw.get("block") or []}

expected = {
    # read-only and drafting: allowed
    "mcp_outlook_list_mail_messages": "allow",
    "mcp_outlook_get_mail_message": "allow",
    "mcp_outlook_list_calendar_events": "allow",
    "mcp_outlook_get_calendar_view": "allow",
    "mcp_outlook_create_draft_email": "allow",
    "mcp_outlook_list_mail_rules": "allow",
    "mcp_outlook_create_reply_draft": "allow",
    # anything not explicitly allowed asks, including tools added later
    "mcp_outlook_cancel_calendar_event": "ask",
    "mcp_outlook_set_my_status_message": "ask",
    "mcp_outlook_some_future_tool": "ask",
    # speaks for the user or changes mailbox/calendar: ask
    "mcp_outlook_send_mail": "ask",
    "mcp_outlook_send_draft_message": "ask",
    "mcp_outlook_reply_mail_message": "ask",
    "mcp_outlook_reply_all_mail_message": "ask",
    "mcp_outlook_forward_mail_message": "ask",
    "mcp_outlook_create_calendar_event": "ask",
    "mcp_outlook_update_calendar_event": "ask",
    "mcp_outlook_accept_calendar_event": "ask",
    "mcp_outlook_move_mail_message": "ask",
    # irreversible / standing: block
    "mcp_outlook_delete_mail_message": "block",
    "mcp_outlook_delete_calendar_event": "block",
    "mcp_outlook_create_mail_rule": "block",
    "mcp_outlook_create_my_calendar_permission": "block",
    "mcp_outlook_logout": "block",
    "mcp_outlook_find_meeting_times": "allow",
    "mcp_outlook_download_bytes": "allow",
    "session_search": "block",
    "skill_manage": "ask",
    # built-ins fall through to the default
    "terminal": "allow",
    "web_search": "allow",
}

failures = 0
for tool, want in expected.items():
    got, pattern = mod.decide(tool, rules)
    ok = got == want
    failures += not ok
    print(f"{'ok ' if ok else 'BAD'} {tool:42} {got:6} ({pattern or 'default'})")

print(f"\n{len(expected) - failures}/{len(expected)} passed")
sys.exit(1 if failures else 0)
