"""Claude Tag-style scoped memory for the Pilot Agent (pure logic, testable without Hermes).

Claude Tag (claude.com/docs/claude-tag/users/memory, concepts/how-it-works):
- Each channel, public or private, has its own notes, read while working in that channel.
- Workspace notes hold workspace-wide conventions: saved only from public channels, read in every
  channel (public and private). A private channel reads them but saves only to its own notes.
- A group DM reads and saves only its own notes.
- Each one-to-one DM keeps separate notes, outside all of the above.
- Memory is curated notes (not transcripts); anyone in the place can read and correct it.
- The agent can look back at earlier sessions in the same place (list + read transcripts),
  not full-text search across everything.
Deliberate additions for surfaces Claude Tag doesn't have: the Pilot app API gets its own notes
("app"). Scheduled runs read their verified origin's notes but never write (Hermes' cron rule).
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

NOTE_CHAR_LIMIT = 2200        # per place, same budget as Hermes' built-in MEMORY.md
WORKSPACE_CHAR_LIMIT = 2200
TRANSCRIPT_MESSAGE_CHARS = 1500
TRANSCRIPT_MAX_MESSAGES = 40


@dataclass(frozen=True)
class Scope:
    kind: str                 # public_channel | private_channel | group_dm | dm | app | scheduled | local
    key: str                  # storage key, e.g. "channel:C123", "dm:D123", "app"
    label: str                # human label for the prompt, e.g. "#general"
    chat_id: str = ""         # Slack conversation id, for past_sessions
    reads_workspace: bool = False
    writes_workspace: bool = False
    writable: bool = True


def resolve_scope(platform: str, chat_id: str, agent_context: str,
                  info: Optional[dict], scheduled_origin: Optional[dict] = None) -> Scope:
    """Map a Hermes session to a Claude Tag memory place. ``info`` is Slack conversations.info
    (``None`` if it could not be fetched: then a channel is treated as private — fail closed)."""
    platform = (platform or "").lower()
    if agent_context in ("cron", "flush") or platform == "cron":
        origin = scheduled_origin or {}
        if origin.get("platform") == "slack" and origin.get("chat_id"):
            place = resolve_scope("slack", str(origin["chat_id"]), "primary", info)
            return Scope(place.kind, place.key, place.label, place.chat_id,
                         place.reads_workspace, False, False)
        # A CLI-created or malformed job has no trustworthy place. Give it no place's notes.
        return Scope("scheduled", "", "an unscoped scheduled run", writable=False)
    if platform == "api_server":
        return Scope("app", "app", "the Pilot app")
    if platform != "slack" or not chat_id:
        return Scope("local", "local", "a local session")
    info = info or {}
    name = info.get("name") or chat_id
    if info.get("is_im") or chat_id.startswith("D"):
        return Scope("dm", f"dm:{chat_id}", "this direct message", chat_id)
    if info.get("is_mpim"):
        return Scope("group_dm", f"group:{chat_id}", "this group DM", chat_id)
    public = (bool(info) and not info.get("is_private")
              and not (info.get("is_ext_shared") or info.get("is_org_shared")))
    if public:
        return Scope("public_channel", f"channel:{chat_id}", f"#{name}", chat_id,
                     reads_workspace=True, writes_workspace=True)
    return Scope("private_channel", f"channel:{chat_id}", f"#{name}" if info else "this channel",
                 chat_id, reads_workspace=True)


class NoteStore:
    """Curated notes per storage key, one JSON file each, under <root>/<kind>/<id>.json."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        kind, _, ident = key.partition(":")
        safe = "".join(c for c in (ident or kind) if c.isalnum() or c in "-_")
        return self.root / kind / f"{safe or 'default'}.json"

    def read(self, key: str) -> list[dict]:
        try:
            return json.loads(self._path(key).read_text())
        except (FileNotFoundError, ValueError):
            return []

    def _write(self, key: str, fn):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            notes = self.read(key)
            result = fn(notes)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1))
            os.replace(tmp, path)
            return result

    def add(self, key: str, text: str, by: str, limit: int) -> str:
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("content is empty")
        _check_note_content(text)

        def op(notes):
            used = sum(len(n["text"]) for n in notes)
            if used + len(text) > limit:
                raise ValueError(f"notes are full ({used}/{limit} chars): replace or remove "
                                 f"outdated entries first")
            if any(n["text"] == text for n in notes):
                return "already saved"
            notes.append({"text": text, "by": by,
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            return "saved"
        return self._write(key, op)

    def replace(self, key: str, old_text: str, text: str, by: str, limit: int) -> str:
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("content is empty")
        _check_note_content(text)

        def op(notes):
            idx = _find(notes, old_text)
            used = sum(len(n["text"]) for i, n in enumerate(notes) if i != idx)
            if used + len(text) > limit:
                raise ValueError(f"notes would exceed {limit} chars")
            notes[idx] = {"text": text, "by": by,
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            return "replaced"
        return self._write(key, op)

    def remove(self, key: str, old_text: str) -> str:
        def op(notes):
            notes.pop(_find(notes, old_text))
            return "removed"
        return self._write(key, op)


def _check_note_content(text: str) -> None:
    """Apply Hermes' own strict memory scanner before a persistent note enters a prompt."""
    try:
        from tools.memory_tool_store import _scan_memory_content
    except ImportError as exc:
        raise ValueError("memory safety scanner is unavailable; note was not saved") from exc
    error = _scan_memory_content(text)
    if error:
        raise ValueError(error)


def _find(notes: list[dict], old_text: str) -> int:
    needle = " ".join((old_text or "").split()).lower()
    if not needle:
        raise ValueError("old_text is required to pick the entry")
    hits = [i for i, n in enumerate(notes) if needle in n["text"].lower()]
    if not hits:
        raise ValueError("no saved entry matches old_text")
    if len(hits) > 1:
        raise ValueError("old_text matches several entries; quote more of the one you mean")
    return hits[0]


def render_block(scope: Scope, here: list[dict], workspace: list[dict]) -> str:
    def bullets(notes):
        return "\n".join(f"- {n['text']}" for n in notes) if notes else "(none yet)"

    lines = [f"## Memory — {scope.label}",
             "You keep curated notes per place (channel, group DM, direct message), the way Claude "
             "Tag does. Only this place's notes are loaded here. What was said or saved in other "
             "channels or in anyone's direct messages is not available in this place: never guess "
             "it, repeat it or bring it up here."]
    if scope.key:
        lines += ["", f"Notes for {scope.label}:", bullets(here)]
    if scope.reads_workspace:
        lines += ["", "Workspace notes (shared with every channel in the workspace):", bullets(workspace)]
    if scope.writable:
        if scope.writes_workspace:
            where = ("Use `where=\"workspace\"` for conventions that hold across the workspace "
                     "(only a public channel can save them).")
        elif scope.reads_workspace:
            where = "Workspace notes can only be saved from a public channel, so save to this place."
        else:
            where = ""
        lines += ["", "Use `memory_notes` to save, correct or remove stable facts, decisions and "
                  "instructions when someone asks you to remember something or when it will clearly "
                  f"matter later in this place; keep entries short. {where} Anyone here may ask what "
                  "you remember about this place: list the notes. Use `past_sessions` to look back "
                  "at earlier conversations in this place."]
    else:
        lines += ["", "This is a scheduled run: memory is read-only here."]
    return "\n".join(lines)


def list_past_sessions(db_path: Path, chat_id: str, exclude: str, limit: int = 20) -> list[dict]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        rows = db.execute(
            "select id, title, started_at, message_count, thread_id from sessions "
            "where chat_id = ? and id != ? and coalesce(hidden, 0) = 0 "
            "order by started_at desc limit ?", (chat_id, exclude or "", int(limit))).fetchall()
    return [{"session_id": r[0], "title": r[1] or "(untitled)",
             "started_utc": datetime.fromtimestamp(float(r[2] or 0), timezone.utc).strftime("%Y-%m-%d %H:%M"),
             "messages": r[3], "thread": r[4]} for r in rows]


def read_past_session(db_path: Path, chat_id: str, session_id: str) -> dict:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        owner = db.execute("select chat_id, title from sessions where id = ?", (session_id,)).fetchone()
        if not owner or owner[0] != chat_id:
            raise ValueError("no earlier session with that id in this place")
        rows = db.execute(
            "select role, content from messages where session_id = ? and role in ('user','assistant') "
            "and coalesce(content, '') != '' order by id", (session_id,)).fetchall()
    rows = rows[-TRANSCRIPT_MAX_MESSAGES:]
    return {"session_id": session_id, "title": owner[1],
            "messages": [{"role": r, "text": (c[:TRANSCRIPT_MESSAGE_CHARS] + " …[truncated]")
                          if len(c) > TRANSCRIPT_MESSAGE_CHARS else c} for r, c in rows]}
