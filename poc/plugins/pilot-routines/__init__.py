"""Scope Hermes cronjob access to the Slack place recorded in each job's origin.

Claude Tag routines run with a channel's access and are managed from that channel. Hermes
captures origin on chat-created jobs, but its built-in list is profile-wide. The hook blocks
unscoped reads and cross-place changes; routines_list exposes only matching jobs.
"""

from __future__ import annotations

import json
import logging

from .scope import decision, same_place

logger = logging.getLogger(__name__)


def _current() -> dict:
    try:
        from gateway.session_context import get_session_env
        if get_session_env("HERMES_SESSION_PLATFORM", "").lower() != "slack":
            return {}
        return {"chat_id": get_session_env("HERMES_SESSION_CHAT_ID", ""),
                "scope_id": get_session_env("HERMES_SESSION_SCOPE_ID", "")}
    except Exception:  # internal Hermes accessor changed: deny access
        logger.warning("pilot-routines: Hermes session context unavailable")
        return {}


def _pre_tool_call(tool_name, args, **kwargs):
    del kwargs
    if tool_name not in ("cronjob", "cronjob_manage"):
        return None
    args = args if isinstance(args, dict) else {}
    current = _current()
    job = None
    lookup = None
    try:
        from cron.jobs import get_job, resolve_job_ref
        lookup = get_job
    except Exception:
        pass
    if args.get("job_id"):
        try:
            job = resolve_job_ref(str(args["job_id"]))
        except Exception:
            pass  # unknown or ambiguous job must fail the scope check
    reason = decision(tool_name, args, current, job, lookup)
    if reason:
        logger.info("pilot-routines: blocked %s %s", tool_name, args.get("action"))
        return {"action": "block", "message": reason}
    return None


def _list_routines(args: dict, **kwargs) -> str:
    del args, kwargs
    current = _current()
    if not current.get("chat_id"):
        return json.dumps({"error": "Routines are available only from a Slack conversation."})
    from cron.jobs import list_jobs
    jobs = list_jobs(include_disabled=True)
    visible = [{"id": j["id"], "name": j["name"], "schedule": j.get("schedule_display"),
                "prompt": j.get("prompt"), "enabled": j.get("enabled"),
                "next_run_at": j.get("next_run_at")}
               for j in jobs if same_place(j.get("origin"), current)]
    return json.dumps({"place": current["chat_id"], "count": len(visible), "routines": visible})


_SCHEMA = {"name": "routines_list",
           "description": "List only the scheduled routines belonging to this Slack channel or DM. "
                          "Use this before changing a routine; never guess a job ID.",
           "parameters": {"type": "object", "properties": {}}}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_tool("routines_list", "pilot-routines", _SCHEMA, _list_routines)
