"""Model tool for multilingual, in-channel requests to change automatic replies."""

from __future__ import annotations

import json


SCHEMA = {
    "name": "channel_response_setting",
    "description": (
        "Read or change whether Pilot may answer unmentioned top-level messages in the "
        "current Slack channel. Use when someone asks in any language to turn automatic "
        "replies on/off or asks for this channel's reply setting. This affects only the "
        "current channel; joined threads and DMs are unchanged. Never claim a change "
        "succeeded unless this tool confirms it."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["get", "on", "off"],
                   "description": "Read, enable, or disable automatic replies in this channel."},
    }, "required": ["action"]},
}


def current_context() -> dict:
    try:
        from gateway.session_context import get_session_env
        return {key: get_session_env(name, "") for key, name in {
            "platform": "HERMES_SESSION_PLATFORM",
            "chat_type": "HERMES_SESSION_CHAT_TYPE",
            "channel": "HERMES_SESSION_CHAT_ID",
            "team": "HERMES_SESSION_SCOPE_ID",
            "user": "HERMES_SESSION_USER_ID",
        }.items()}
    except Exception:
        return {}


def _is_member(client, channel: str, user: str) -> bool:
    cursor = None
    while True:
        response = client.conversations_members(channel=channel, limit=200, cursor=cursor)
        if user in (response.get("members") or []):
            return True
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return False


def apply_setting(action: str, current: dict, store, *, client, allowed_users: set[str]) -> dict:
    if action not in ("get", "on", "off"):
        return {"error": "invalid_action"}
    team, channel, user = (current.get(k) or "" for k in ("team", "channel", "user"))
    if (current.get("platform", "").lower() != "slack" or
            current.get("chat_type", "").lower() != "group" or
            not channel.startswith(("C", "G")) or not team or user not in allowed_users):
        return {"error": "unavailable", "message": "Use this from an authorized Slack channel."}
    try:
        info = (client.conversations_info(channel=channel).get("channel") or {})
        if (not (info.get("is_channel") or info.get("is_group")) or
                info.get("is_member") is False or info.get("is_ext_shared") or
                info.get("is_org_shared") or not _is_member(client, channel, user)):
            return {"error": "access_denied", "message": "Current channel membership or scope is not verified."}
        changed = store.set(team, channel, action == "on", user) if action != "get" else False
        return {"channel": channel, "respond_automatically": store.enabled(team, channel),
                "changed": changed}
    except Exception:
        return {"error": "unavailable", "message": "Could not verify or save the channel setting."}


def tool_handler(args: dict, store, **kwargs) -> str:
    del kwargs
    from agent.secret_scope import get_secret
    from slack_sdk import WebClient
    allowed = {u.strip() for u in (get_secret("SLACK_ALLOWED_USERS") or "").split(",") if u.strip()}
    token = get_secret("SLACK_BOT_TOKEN")
    if not token or not allowed:
        return json.dumps({"error": "unavailable", "message": "Slack access is not configured."})
    result = apply_setting((args or {}).get("action", ""), current_context(), store,
                           client=WebClient(token=token), allowed_users=allowed)
    return json.dumps(result)
