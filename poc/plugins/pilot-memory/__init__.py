"""pilot-memory: Hermes memory provider with Claude Tag's per-place memory (see scoped_memory.py).

Activated by ``memory.provider: pilot-memory`` with Hermes' built-in MEMORY.md / USER.md turned
off (``memory.memory_enabled: false``, ``memory.user_profile_enabled: false``): the built-in
store is one notebook injected into every conversation, which leaks across channels and DMs.

Hermes builds one agent per session and calls ``initialize`` with that session's gateway
identity (platform, chat_id, chat_type, thread_id, user_id, agent_context), so each session
gets exactly its place's notes in its (frozen, cache-friendly) system prompt block.
``past_sessions`` replaces the unscoped built-in ``session_search`` (blocked in
pilot-rules.yaml).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.secret_scope import get_secret
from hermes_constants import get_hermes_home
from agent.memory_provider import MemoryProvider

from .scoped_memory import (NOTE_CHAR_LIMIT, WORKSPACE_CHAR_LIMIT, NoteStore, list_past_sessions,
                            read_past_session, render_block, resolve_scope)

logger = logging.getLogger(__name__)

_SCHEMAS = [
    {"name": "memory_notes",
     "description": ("Read or change your saved notes for the place you are in (this channel, group "
                     "DM or direct message), or the workspace notes shared by every channel. Use it "
                     "when someone asks you to remember, forget or correct something, asks what you "
                     "remember, or when you learn a stable fact, decision or instruction that will "
                     "matter later here."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "add", "replace", "remove"]},
         "where": {"type": "string", "enum": ["here", "workspace"],
                   "description": "'here' (default) = this place; 'workspace' = shared with all "
                                  "channels, writable only from a public channel."},
         "content": {"type": "string", "description": "The note text (add / replace)."},
         "old_text": {"type": "string",
                      "description": "Part of the existing note to replace or remove."}},
         "required": ["action"]}},
    {"name": "past_sessions",
     "description": ("Look back at earlier conversations in the place you are in: 'list' them "
                     "(newest first) or 'read' one transcript by session_id. Only this place's "
                     "conversations are available."),
     "parameters": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "read"]},
         "session_id": {"type": "string"},
         "limit": {"type": "integer", "description": "How many to list (default 20)."}},
         "required": ["action"]}},
]


def _slack_info(chat_id: str) -> dict | None:
    token = get_secret("SLACK_BOT_TOKEN")
    if not token or not chat_id or chat_id.startswith("D"):
        return {"is_im": True} if chat_id.startswith("D") else None
    try:
        from slack_sdk import WebClient
        return WebClient(token=token, timeout=10).conversations_info(channel=chat_id)["channel"]
    except Exception as exc:  # noqa: BLE001 — unknown visibility → treated as private (fail closed)
        logger.warning("pilot-memory: conversations.info failed for %s: %s", chat_id, exc)
        return None


def _scheduled_origin(session_id: str) -> dict | None:
    """Hermes cron sessions are cron_<job id>_<fire time>; jobs store the original chat.

    Do not set HERMES_SESSION_* from that chat: Hermes deliberately clears those variables so
    scheduled tools cannot act as the sender. This lookup is only for read-only memory.
    """
    if not session_id.startswith("cron_"):
        return None
    job_id = session_id.split("_", 2)[1]
    if len(job_id) != 12 or any(c not in "0123456789abcdef" for c in job_id):
        return None
    try:
        from cron.jobs import get_job
        job = get_job(job_id)
    except Exception as exc:  # lookup failure must not grant a place's memory
        logger.warning("pilot-memory: cron origin lookup failed for %s: %s", job_id, exc)
        return None
    origin = job.get("origin") if job else None
    if not isinstance(origin, dict) or origin.get("platform") != "slack":
        return None
    chat_id = origin.get("chat_id")
    return origin if isinstance(chat_id, str) and chat_id else None


class PilotMemoryProvider(MemoryProvider):
    def __init__(self):
        self._scope = None
        self._store = None
        self._home = None
        self._session_id = ""
        self._author = ""

    @property
    def name(self) -> str:
        return "pilot-memory"

    def is_available(self) -> bool:
        return True

    def get_config_schema(self):
        return []

    def save_config(self, values, hermes_home):
        del values, hermes_home

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._home = Path(kwargs.get("hermes_home") or get_hermes_home())
        self._store = NoteStore(self._home / "pilot-memory")
        platform = kwargs.get("platform", "")
        chat_id = kwargs.get("chat_id", "") or ""
        origin = _scheduled_origin(session_id) if platform == "cron" else None
        info_id = (origin or {}).get("chat_id", "") if platform == "cron" else chat_id
        info = _slack_info(info_id) if (platform == "slack" or
                                   (origin or {}).get("platform") == "slack") else None
        self._scope = resolve_scope(platform, chat_id, kwargs.get("agent_context", "primary"),
                                    info, origin)
        self._author = kwargs.get("user_name") or kwargs.get("user_id") or ""
        logger.info("pilot-memory: session %s → %s (%s)", session_id, self._scope.key or "-",
                    self._scope.kind)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        del turn_number, message
        self._author = kwargs.get("author_name") or kwargs.get("author_id") or self._author

    def system_prompt_block(self) -> str:
        if not self._scope:
            return ""
        here = self._store.read(self._scope.key) if self._scope.key else []
        ws = self._store.read("workspace:workspace") if self._scope.reads_workspace else []
        block = render_block(self._scope, here, ws)
        logger.info("pilot-memory: prompt block for %s: %d notes here, %d workspace, %d chars",
                    self._scope.key or self._scope.kind, len(here), len(ws), len(block))
        return block

    def get_tool_schemas(self):
        return _SCHEMAS

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        del kwargs
        try:
            if tool_name == "memory_notes":
                return json.dumps(self._notes(args or {}), ensure_ascii=False)
            if tool_name == "past_sessions":
                return json.dumps(self._past(args or {}), ensure_ascii=False)
            return json.dumps({"error": f"unknown tool {tool_name}"})
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

    def _notes(self, args: dict) -> dict:
        s = self._scope
        action, where = args.get("action", "list"), args.get("where") or "here"
        if where == "workspace":
            if not s.reads_workspace:
                raise ValueError("workspace notes are not available in direct messages or group DMs")
            key, limit = "workspace:workspace", WORKSPACE_CHAR_LIMIT
            if action != "list" and not s.writes_workspace:
                raise ValueError("workspace notes can only be changed from a public channel; "
                                 "save it to this place instead")
        else:
            if not s.key:
                raise ValueError("this scheduled run has no verified place")
            key, limit = s.key, NOTE_CHAR_LIMIT
        if action == "list":
            return {"place": s.label if where == "here" else "workspace",
                    "notes": [n["text"] for n in self._store.read(key)]}
        if not s.writable:
            raise ValueError("memory is read-only in a scheduled run")
        if action == "add":
            result = self._store.add(key, args.get("content", ""), self._author, limit)
        elif action == "replace":
            result = self._store.replace(key, args.get("old_text", ""), args.get("content", ""),
                                         self._author, limit)
        elif action == "remove":
            result = self._store.remove(key, args.get("old_text", ""))
        else:
            raise ValueError(f"unknown action {action}")
        logger.info("pilot-memory: %s %s in %s", action, where, s.key)
        return {"result": result, "place": s.label if where == "here" else "workspace"}

    def _past(self, args: dict) -> dict:
        s = self._scope
        if not s.chat_id:
            raise ValueError("earlier conversations are only available inside a Slack channel or DM")
        db = self._home / "state.db"
        if args.get("action") == "read":
            return read_past_session(db, s.chat_id, args.get("session_id", ""))
        return {"place": s.label,
                "sessions": list_past_sessions(db, s.chat_id, self._session_id, args.get("limit") or 20)}


def register(ctx) -> None:
    ctx.register_memory_provider(PilotMemoryProvider())
