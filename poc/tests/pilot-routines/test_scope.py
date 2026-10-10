"""Check that routine management cannot cross Slack places or change delivery."""

import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).resolve().parents[2] / "plugins/pilot-routines/scope.py"
spec = importlib.util.spec_from_file_location("pilot_routine_scope", module_path)
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)

here = {"chat_id": "C1", "scope_id": "T1"}
local = {"origin": {"platform": "slack", "chat_id": "C1", "scope_id": "T1"}}
other = {"origin": {"platform": "slack", "chat_id": "C2", "scope_id": "T1"}}
other_workspace = {"origin": {"platform": "slack", "chat_id": "C1", "scope_id": "T2"}}
legacy = {"origin": None}
lookup = {"local": local, "other": other}.get

checks = {
    "local job visible": scope.same_place(local["origin"], here),
    "other channel hidden": not scope.same_place(other["origin"], here),
    "other workspace hidden": not scope.same_place(other_workspace["origin"], here),
    "legacy job hidden": not scope.same_place(legacy["origin"], here),
    "list redirected": bool(scope.decision("cronjob_manage", {"action": "list"}, here)),
    "local update allowed": scope.decision("cronjob_manage", {"action": "update"}, here, local) is None,
    "other update denied": bool(scope.decision("cronjob_manage", {"action": "update"}, here, other)),
    "unknown id denied": bool(scope.decision("cronjob_manage", {"action": "run"}, here, None)),
    "create at origin allowed": scope.decision("cronjob_manage", {"action": "create", "deliver": "origin"}, here) is None,
    "cross delivery denied": bool(scope.decision("cronjob_manage", {"action": "create", "deliver": "slack:C2"}, here)),
    "cross failure delivery denied": bool(scope.decision("cronjob_manage", {"action": "update", "failure_deliver": "all"}, here, local)),
    "API management denied": bool(scope.decision("cronjob_manage", {"action": "create"}, {})),
    "local prior output allowed": scope.decision("cronjob_manage", {"action": "create", "context_from": ["local"]}, here, lookup=lookup) is None,
    "foreign prior output denied": bool(scope.decision("cronjob_manage", {"action": "create", "context_from": ["other"]}, here, lookup=lookup)),
    "foreign prior output denied on update": bool(scope.decision("cronjob_manage", {"action": "update", "context_from": ["other"]}, here, local, lookup)),
    "tool override denied": bool(scope.decision("cronjob_manage", {"action": "create", "enabled_toolsets": ["a2a"]}, here)),
    "script job denied": bool(scope.decision("cronjob_manage", {"action": "create", "script": "run.sh"}, here)),
    "no-agent job denied": bool(scope.decision("cronjob_manage", {"action": "create", "no_agent": True}, here)),
    "workdir denied": bool(scope.decision("cronjob_manage", {"action": "create", "workdir": "/opt/data"}, here)),
    "monitor denied": bool(scope.decision("cronjob_manage", {"action": "create", "monitor": "https://example.org"}, here)),
    "skill injection denied": bool(scope.decision("cronjob_manage", {"action": "create", "skills": ["unsafe"]}, here)),
    "existing unsafe job denied": bool(scope.decision("cronjob_manage", {"action": "run"}, here,
                                                       {**local, "enabled_toolsets": ["a2a"]})),
}

for name, passed in checks.items():
    print(("ok  " if passed else "BAD ") + name)
print(f"{sum(checks.values())}/{len(checks)} passed")
sys.exit(0 if all(checks.values()) else 1)
