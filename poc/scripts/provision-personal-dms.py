#!/usr/bin/env python3
"""Provision one Hermes profile for each allowed Slack user's known one-to-one DM.

Run from the host while the container exists, then stop the gateway, run
migrate-personal-dm-state.py in the container, and restart. This script never
clones a profile and never enables a second Slack adapter or Outlook connector.
"""

from __future__ import annotations

import copy
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
USER_RE = re.compile(r"^[UW][A-Z0-9]+$")
DM_RE = re.compile(r"^D[A-Z0-9]+$")


def env_values() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def open_dm(user: str) -> str:
    """Ask Slack for the bot's exact DM ID before this user sends a first message."""
    code = (
        "import os,sys; from slack_sdk import WebClient; "
        "response=WebClient(token=os.environ['SLACK_BOT_TOKEN']).conversations_open(users=sys.argv[1]); "
        "print(response['channel']['id'])"
    )
    result = subprocess.run(["docker", "exec", "-u", "hermes", "pilot-agent",
                             "/opt/hermes/.venv/bin/python", "-c", code, user],
                            capture_output=True, text=True)
    chat = result.stdout.strip()
    if result.returncode or not DM_RE.fullmatch(chat):
        raise RuntimeError(f"{user}: Slack could not provide a one-to-one DM ID; check im:write")
    return chat


def dm_routes(allowed: list[str]) -> list[dict[str, str]]:
    with sqlite3.connect(f"file:{DATA / 'state.db'}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT DISTINCT user_id, chat_id FROM sessions "
            "WHERE source='slack' AND chat_type='dm' AND chat_id LIKE 'D%'"
        ).fetchall()
    routes = []
    live = yaml.safe_load((DATA / "config.yaml").read_text()) or {}
    existing = {r.get("user_id"): r for r in live.get("profile_routes", [])
                if isinstance(r, dict) and str(r.get("name", "")).startswith("personal-dm-")}
    for user in allowed:
        chats = {chat for owner, chat in rows if owner == user and DM_RE.fullmatch(chat or "")}
        prior = existing.get(user, {})
        old_chat = prior.get("chat_id")
        if old_chat and DM_RE.fullmatch(old_chat):
            profile_db = DATA / "profiles" / f"person-{user.lower()}" / "state.db"
            if profile_db.exists():
                with sqlite3.connect(f"file:{profile_db}?mode=ro", uri=True) as db:
                    known = db.execute("SELECT count(*) FROM sessions WHERE chat_id=? AND user_id=?",
                                       (old_chat, user)).fetchone()[0]
                if known:
                    chats.add(old_chat)
        if not chats:
            chats.add(open_dm(user))
        if len(chats) != 1:
            raise RuntimeError(f"{user}: expected one verified DM in Hermes history; found {len(chats)}")
        chat = chats.pop()
        if any(owner != user and other_chat == chat for owner, other_chat in rows):
            raise RuntimeError(f"{user}: DM chat has another recorded sender; refusing route")
        routes.append({"name": f"personal-dm-{user.lower()}", "platform": "slack",
                       "user_id": user, "chat_id": chat, "profile": f"person-{user.lower()}"})
    return routes


def docker_hermes(*args: str) -> None:
    subprocess.run(["docker", "exec", "-u", "hermes", "pilot-agent",
                    "/opt/hermes/.venv/bin/hermes", *args], check=True)


def provision(route: dict[str, str], bot_token: str, template: dict) -> None:
    name = route["profile"]
    home = DATA / "profiles" / name
    if not home.exists():
        docker_hermes("profile", "create", name, "--no-alias", "--no-skills")
    if not home.is_dir():
        raise RuntimeError(f"profile {name} was not created")
    marker = home / ".pilot-personal-provisioned"
    config = (yaml.safe_load((home / "config.yaml").read_text()) if marker.exists()
              else copy.deepcopy(template))
    config.pop("gateway", None)
    config.pop("profile_routes", None)
    enabled_plugins = config.setdefault("plugins", {}).setdefault("enabled", [])
    if "pilot-usage" not in enabled_plugins:
        enabled_plugins.append("pilot-usage")
    config["terminal"]["cwd"] = f"/opt/data/profiles/{name}/workspace"
    config["platforms"]["slack"]["enabled"] = False
    config["platforms"]["api_server"] = {"enabled": False}
    if not marker.exists():
        config["mcp_servers"]["outlook"]["enabled"] = False
    config["mcp_servers"]["outlook"]["env"]["MS365_MCP_TOKEN_CACHE_PATH"] = (
        f"/opt/data/profiles/{name}/connectors/outlook/.token-cache.json")
    config["mcp_servers"]["outlook"]["env"]["MS365_MCP_SELECTED_ACCOUNT_PATH"] = (
        f"/opt/data/profiles/{name}/connectors/outlook/.selected-account.json")
    (home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    rules_file = home / "pilot-rules.yaml"
    if not rules_file.exists():
        rules_file.write_bytes((ROOT / "config/pilot-rules.yaml").read_bytes())
    if not marker.exists():
        (home / "SOUL.md").write_bytes((ROOT / "config/SOUL.md").read_bytes())
    # Scoped service credential for read-only Slack tools. The personal profile's
    # Slack adapter is explicitly disabled; the default bot alone receives events.
    secret_file = home / ".env"
    prior_lines = secret_file.read_text().splitlines() if secret_file.exists() else []
    kept = [line for line in prior_lines if not line.startswith("SLACK_BOT_TOKEN=")]
    kept.append(f"SLACK_BOT_TOKEN={bot_token}")
    secret_file.write_text("\n".join(kept) + "\n")
    secret_file.chmod(0o600)
    shutil.copytree(ROOT / "plugins", home / "plugins", dirs_exist_ok=True)
    (home / "workspace").mkdir(exist_ok=True)
    marker.write_text("Provisioned as a personal Slack DM profile.\n")
    print(f"Prepared {name}: DM route, profile config and workspace")


def main() -> None:
    env = env_values()
    allowed = [item.strip() for item in env.get("SLACK_ALLOWED_USERS", "").split(",") if item.strip()]
    if not allowed or len(set(allowed)) != len(allowed) or not all(USER_RE.fullmatch(x) for x in allowed):
        raise RuntimeError("SLACK_ALLOWED_USERS must contain distinct Slack user IDs")
    token = env.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is missing")
    routes = dm_routes(allowed)
    template = yaml.safe_load((ROOT / "config/config.yaml").read_text())
    for route in routes:
        provision(route, token, template)
    live_path = DATA / "config.yaml"
    live = yaml.safe_load(live_path.read_text())
    if not isinstance(live, dict):
        raise RuntimeError("live config is not a YAML mapping")
    existing = live.get("profile_routes") or []
    if any(not isinstance(r, dict) or not str(r.get("name", "")).startswith("personal-dm-") for r in existing):
        raise RuntimeError("live config has nonpersonal routes; reconcile manually")
    live.setdefault("gateway", {})["multiplex_profiles"] = True
    live["profile_routes"] = routes
    live_path.write_text(yaml.safe_dump(live, sort_keys=False))
    print(f"Configured {len(routes)} personal DM route(s); restart gateway after state migration")
    for route in routes:
        print(f"Migration: {route['profile']} {route['chat_id']} {route['user_id']}")


if __name__ == "__main__":
    main()
