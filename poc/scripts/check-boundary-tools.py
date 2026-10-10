#!/usr/bin/env python3
"""Check the pinned Hermes model-tool catalog before a sandbox cutover.

Run inside the Hermes image, separately for the service and every personal
profile. --candidate evaluates the versioned policy without editing config.
The default mode checks that the active config has adopted it.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import yaml


def check_policy(config: dict, policy: dict, *, candidate: bool, names_for) -> list[str]:
    errors: list[str] = []
    platforms = policy["platform_toolsets"]
    disabled = policy["disabled_toolsets"]
    known_plugins = policy["known_plugin_toolsets"]
    if candidate:
        config = copy.deepcopy(config)
        config["platform_toolsets"] = platforms
        config["known_plugin_toolsets"] = known_plugins
        config.setdefault("agent", {})["disabled_toolsets"] = disabled
        config.setdefault("plugins", {})["enabled"] = policy["enabled_plugins"]
    else:
        if config.get("platform_toolsets") != platforms:
            errors.append("active platform_toolsets differ from the boundary policy")
        if config.get("known_plugin_toolsets") != known_plugins:
            errors.append("active known_plugin_toolsets differ from the boundary policy")
        if (config.get("agent") or {}).get("disabled_toolsets") != disabled:
            errors.append("active disabled_toolsets differ from the boundary policy")
        if (config.get("terminal") or {}).get("backend") != "pilot_sandbox":
            errors.append("terminal.backend is not pilot_sandbox")
        if (config.get("terminal") or {}).get("container_persistent") is not False:
            errors.append("terminal.container_persistent must be false")
        if "pilot-sandbox" not in ((config.get("plugins") or {}).get("enabled") or []):
            errors.append("pilot-sandbox plugin is not enabled")
    if (config.get("plugins") or {}).get("enabled") != policy["enabled_plugins"]:
        errors.append("enabled plugins differ from the boundary policy")
    if ((config.get("tools") or {}).get("tool_search") or {}).get("enabled") != "off":
        errors.append("tool_search must remain off for this measured catalog")
    if (config.get("memory") or {}).get("provider") != "pilot-memory":
        errors.append("scoped pilot-memory provider is not selected")
    if any((v or {}).get("enabled") for v in (config.get("mcp_servers") or {}).values()
           if isinstance(v, dict)):
        errors.append("an MCP server is enabled but not yet in this boundary policy")

    for platform in platforms:
        actual_toolsets, actual_tools = names_for(config, platform)
        expected_toolsets = set(platforms[platform])
        if actual_toolsets != expected_toolsets:
            errors.append(f"{platform}: toolsets differ: extra "
                          f"{sorted(actual_toolsets - expected_toolsets)}, missing "
                          f"{sorted(expected_toolsets - actual_toolsets)}")
        allowed = set(policy["allowed_tools"][platform])
        required = set(policy["required_tools"][platform])
        if not actual_tools <= allowed:
            errors.append(f"{platform}: extra tools: {sorted(actual_tools - allowed)}")
        if not required <= actual_tools:
            errors.append(f"{platform}: missing required tools: {sorted(required - actual_tools)}")
        print(f"{platform}: {len(actual_tools)} allowed tools; "
              f"{len(actual_toolsets)} toolsets", flush=True)
    return errors


def check_cron_overrides(home: Path, allowed_toolsets: set[str]) -> list[str]:
    path = home / "cron" / "jobs.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        jobs = raw.get("jobs", []) if isinstance(raw, dict) else raw
        if not isinstance(jobs, list):
            raise ValueError("jobs is not a list")
    except (OSError, ValueError) as exc:
        return [f"cannot verify cron tool overrides: {type(exc).__name__}"]
    errors = []
    for job in jobs:
        if not isinstance(job, dict):
            errors.append("invalid cron job entry")
            continue
        override = job.get("enabled_toolsets")
        if override and (not isinstance(override, list) or
                         not set(override) <= allowed_toolsets):
            errors.append(f"cron job {job.get('id', '?')} requests tools outside the policy")
        if job.get("enabled"):
            if any(job.get(field) for field in
                   ("script", "no_agent", "workdir", "monitor", "skills")):
                errors.append(f"active cron job {job.get('id', '?')} has an unreviewed execution route")
            origin = job.get("origin") or {}
            if (not isinstance(origin, dict) or origin.get("platform") != "slack" or
                    not origin.get("chat_id")):
                errors.append(f"active cron job {job.get('id', '?')} has no Slack origin")
    return errors


def hermes_names(config: dict, platform: str) -> tuple[set[str], set[str]]:
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions

    toolsets = set(_get_platform_tools(config, platform))
    tools = {entry["function"]["name"] for entry in get_tool_definitions(
        enabled_toolsets=sorted(toolsets),
        disabled_toolsets=(config.get("agent") or {}).get("disabled_toolsets"),
        quiet_mode=True, skip_tool_search_assembly=True)}
    return toolsets, tools


def register_sandbox_tools(plugin_dir: Path) -> None:
    """Load the versioned sandbox schemas in this standalone checker process."""
    from tools.registry import registry
    if registry.get_entry("web_fetch") is not None:
        return
    name = "pilot_sandbox_boundary_check"
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
    if spec is None or spec.loader is None:
        raise RuntimeError("sandbox plugin cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    class CandidateContext:
        def register_terminal_environment_provider(self, provider):
            pass

        def register_tool(self, **kwargs):
            registry.register(**kwargs)

    module.register(CandidateContext())


def memory_provider_names(plugin_dir: Path) -> set[str]:
    """Hermes appends these after get_tool_definitions; inspect their real schemas."""
    name = "pilot_memory_boundary_check"
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
    if spec is None or spec.loader is None:
        raise RuntimeError("scoped memory provider cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return {schema["name"] for schema in module.PilotMemoryProvider().get_tool_schemas()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="store_true",
                        help="simulate the versioned policy without editing active config")
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).resolve().parents[1] / "config" /
                        "boundary-tool-policy.yaml")
    parser.add_argument("--plugin-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "plugins" /
                        "pilot-sandbox")
    parser.add_argument("--memory-plugin-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "plugins" /
                        "pilot-memory")
    args = parser.parse_args()
    from hermes_cli.config import load_config
    from hermes_constants import get_hermes_home

    policy = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    if policy.get("version") != 1:
        print("unsupported boundary policy version", file=sys.stderr)
        return 2
    config = load_config()
    if args.candidate:
        register_sandbox_tools(args.plugin_dir)
    memory_names = memory_provider_names(args.memory_plugin_dir)

    def complete_names(cfg, platform):
        toolsets, names = hermes_names(cfg, platform)
        if "memory" in toolsets:
            names.update(memory_names)
        return toolsets, names

    errors = check_policy(config, policy, candidate=args.candidate, names_for=complete_names)
    from hermes_cli.tools_config import _get_plugin_toolset_keys
    discovered_plugins = _get_plugin_toolset_keys()
    for platform, known in policy["known_plugin_toolsets"].items():
        if not discovered_plugins <= set(known):
            errors.append(f"{platform}: unreviewed plugin toolsets: "
                          f"{sorted(discovered_plugins - set(known))}")
    errors += check_cron_overrides(Path(get_hermes_home()),
                                   set(policy["platform_toolsets"]["cron"]))
    for error in errors:
        print("FAIL: " + error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
