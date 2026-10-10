"""Pilot Agent rules: Allow / Ask / Block on every tool call (OpenAI Dots "Custom Rules").

Rules live in $HERMES_HOME/pilot-rules.yaml and are re-read whenever the file changes, so
they can be edited without restarting the gateway. Matching is by tool name with shell-style
globs (``mcp_outlook_send_*``). Precedence is block > allow > ask, so a connector can be put on
``ask`` as a whole (``mcp_outlook_*``) with explicit ``allow`` exceptions for read-only tools,
and tools the connector adds later ask by default. A tool no rule names falls back to ``default``.

``ask`` returns Hermes' ``approve`` directive, which routes the call through the same human
approval gate as dangerous shell commands (buttons in Slack). Unattended runs (cron, API) follow
``approvals.cron_mode`` / ``approvals.unattended_mode``, so an ``ask`` tool never runs there
without a person. If the rules file is missing or invalid the plugin fails closed: every tool
not on the built-in safe list is escalated to ``ask``.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

import yaml
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Used only when the rules file cannot be loaded: read-only tools keep working, everything
# else asks first.
_FAILSAFE = {
    "default": "ask",
    "allow": ["read_file", "search_files", "web_search", "web_extract", "memory", "todo",
              "session_search", "clarify"],
    "ask": [],
    "block": [],
}

_cache: dict = {"path": None, "mtime": None, "rules": _FAILSAFE}


def _load_rules() -> dict:
    rules_file = Path(get_hermes_home()) / "pilot-rules.yaml"
    if _cache["path"] != rules_file:
        _cache.update(path=rules_file, mtime=None, rules=_FAILSAFE)
    try:
        mtime = rules_file.stat().st_mtime
    except FileNotFoundError:
        if _cache["mtime"] != "missing":
            logger.warning("pilot-rules: %s not found; using fail-safe rules", rules_file)
            _cache.update(mtime="missing", rules=_FAILSAFE)
        return _cache["rules"]
    if mtime == _cache["mtime"]:
        return _cache["rules"]
    try:
        data = yaml.safe_load(rules_file.read_text()) or {}
        rules = {
            "default": data.get("default", "allow"),
            "allow": list(data.get("allow") or []),
            "ask": list(data.get("ask") or []),
            "block": list(data.get("block") or []),
        }
        if rules["default"] not in ("allow", "ask", "block"):
            raise ValueError(f"default must be allow/ask/block, got {rules['default']!r}")
        logger.info("pilot-rules: loaded %s (default=%s, %d ask, %d block)", rules_file,
                    rules["default"], len(rules["ask"]), len(rules["block"]))
    except Exception as exc:  # noqa: BLE001 — any bad file must fail closed, not crash the hook
        logger.error("pilot-rules: invalid %s (%s); using fail-safe rules", rules_file, exc)
        rules = _FAILSAFE
    _cache.update(mtime=mtime, rules=rules)
    return rules


def _matches(tool_name: str, patterns: list) -> str | None:
    for pattern in patterns:
        if fnmatch.fnmatchcase(tool_name, str(pattern)):
            return str(pattern)
    return None


def decide(tool_name: str, rules: dict) -> tuple[str, str | None]:
    """Return (verdict, matched_pattern) for a tool name. Exposed for tests."""
    for verdict in ("block", "allow", "ask"):
        pattern = _matches(tool_name, rules[verdict])
        if pattern:
            return verdict, pattern
    return rules["default"], None


def _pre_tool_call(tool_name, args, task_id, **kwargs):
    del args, task_id, kwargs
    verdict, pattern = decide(tool_name, _load_rules())
    if verdict == "block":
        return {"action": "block",
                "message": f"BLOCKED by Pilot rules ({pattern or 'default'}): "
                           f"'{tool_name}' is not allowed for this agent. Do not retry; "
                           f"tell the user it is blocked by policy."}
    if verdict == "ask":
        return {"action": "approve",
                "message": f"Pilot rules require your OK before '{tool_name}' runs",
                "rule_key": f"pilot-rules:{tool_name}"}
    return None


def register(ctx):
    ctx.register_hook("pre_tool_call", _pre_tool_call)
