#!/usr/bin/env python3
"""Set the agent's IANA timezone from its owner or an explicit override.

This host setup step works on macOS, Linux, and Windows. For IANA validation on
Windows, install Python's tzdata package. No country or language is assumed.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


POC = Path(__file__).resolve().parents[1]
ENV_FILE = POC / ".env"


def read_env(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines()
                if "=" in line and not line.startswith("#"))


def valid_zone(value: str | None) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(value)
        return True
    except (ValueError, ZoneInfoNotFoundError):
        return False


def slack_owner_zone(env: dict[str, str]) -> str | None:
    token = env.get("SLACK_BOT_TOKEN", "")
    owner = env.get("SLACK_ALLOWED_USERS", "").split(",", 1)[0].strip()
    if not token or not owner:
        return None
    request = urllib.request.Request(
        "https://slack.com/api/users.info?user=" + urllib.parse.quote(owner),
        headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    return (data.get("user") or {}).get("tz") if data.get("ok") else None


def host_zone() -> str | None:
    if os.name == "nt":
        # Windows does not normally ship an IANA database or IANA host zone.
        # tzlocal maps its Windows zone to IANA when the optional package exists.
        try:
            from tzlocal import get_localzone_name
            return get_localzone_name()
        except (ImportError, LookupError, OSError):
            return None
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def select_zone(env: dict[str, str]) -> tuple[str, str]:
    override = env.get("AGENT_TIMEZONE", "").strip()
    if override.lower() == "auto":
        override = ""
    if valid_zone(override):
        return override, "AGENT_TIMEZONE override"
    owner_zone = slack_owner_zone(env)
    if valid_zone(owner_zone):
        return str(owner_zone), "owner's Slack profile"
    machine_zone = host_zone()
    if valid_zone(machine_zone):
        return str(machine_zone), "host machine"
    return "UTC", "fallback"


def replace_zone_lines(text: str, zone: str) -> str:
    lines = [line for line in text.splitlines()
             if not line.startswith(("TZ=", "HERMES_TIMEZONE="))]
    return "\n".join(lines) + f"\nTZ={zone}\nHERMES_TIMEZONE={zone}\n"


def main() -> None:
    original = ENV_FILE.read_text(encoding="utf-8")
    zone, source = select_zone(read_env(original))
    mode = stat.S_IMODE(ENV_FILE.stat().st_mode)
    fd, temp_name = tempfile.mkstemp(prefix=".env-timezone-", dir=POC, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(replace_zone_lines(original, zone))
        if os.name != "nt":
            os.chmod(temp_name, mode)
        os.replace(temp_name, ENV_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"Agent timezone: {zone} (from {source}). Recreate the container to apply.")


if __name__ == "__main__":
    main()
