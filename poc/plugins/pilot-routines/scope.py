"""Pure access checks for Claude Tag-style routines per Slack place."""

from __future__ import annotations

from typing import Callable


def same_place(origin: dict | None, current: dict) -> bool:
    if not isinstance(origin, dict) or origin.get("platform") != "slack":
        return False
    if not current.get("chat_id") or origin.get("chat_id") != current["chat_id"]:
        return False
    # Hermes includes a Slack workspace id when available. A mismatch must never grant access.
    origin_workspace = origin.get("scope_id")
    return not origin_workspace or origin_workspace == current.get("scope_id")


def delivery_stays_here(value: object) -> bool:
    """Until cross-channel delivery has a separate membership check, use the origin only."""
    return value is None or isinstance(value, str) and value.strip().lower() in ("", "origin")


def _local_references(refs: object, current: dict,
                      lookup: Callable[[str], dict | None] | None) -> bool:
    if refs is None or refs == "" or refs == []:
        return True
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list):
        return False
    for ref in refs:
        if not isinstance(ref, str):
            return False
        if ref.strip().lower() == "self":
            continue
        if not lookup or not same_place((lookup(ref) or {}).get("origin"), current):
            return False
    return True


def decision(tool_name: str, args: dict, current: dict, job: dict | None = None,
             lookup: Callable[[str], dict | None] | None = None) -> str | None:
    if tool_name not in ("cronjob", "cronjob_manage"):
        return None
    if not current.get("chat_id"):
        return "Routines can only be managed from their Slack channel or direct message."
    action = str(args.get("action") or "").strip().lower()
    if action == "list":
        return "Use routines_list to see only this conversation's routines."
    if action in ("create", "update"):
        # Hermes' per-job toolset override wins over platform_toolsets.cron.
        # Script-only jobs run without an agent; workdir can expose gateway
        # files and AGENTS.md. None of these is a safe routine capability.
        if args.get("enabled_toolsets") not in (None, []):
            return "Routine tool overrides are unavailable in this conversation."
        if args.get("script") not in (None, "") or args.get("no_agent") is True:
            return "Script routines are unavailable in this conversation."
        if args.get("workdir") not in (None, ""):
            return "Routine work directories are unavailable in this conversation."
        if args.get("monitor") not in (None, ""):
            return "Routine monitors need an administrator review."
        if args.get("skills") not in (None, []) or args.get("skill") not in (None, ""):
            return "Routine skills need an administrator review."
    if action == "create":
        if not delivery_stays_here(args.get("deliver")) or not delivery_stays_here(args.get("failure_deliver")):
            return "Create this routine in its destination conversation; delivery must stay at its origin."
        if not _local_references(args.get("context_from"), current, lookup):
            return "A routine can use previous output only from routines in this conversation."
        return None
    if not job or not same_place(job.get("origin"), current):
        return "This routine is not available in this conversation. Use routines_list to find a local routine."
    if (job.get("enabled_toolsets") or job.get("script") or job.get("no_agent") or
            job.get("workdir") or job.get("monitor") or job.get("skills")):
        return "This routine needs an administrator review before it can be managed here."
    if action == "update" and (not delivery_stays_here(args.get("deliver")) or
                               not delivery_stays_here(args.get("failure_deliver"))):
        return "Routine delivery must stay at its origin."
    refs = args.get("context_from") if args.get("context_from") is not None else job.get("context_from")
    if not _local_references(refs, current, lookup):
        return "A routine can use previous output only from routines in this conversation."
    return None
