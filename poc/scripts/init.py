#!/usr/bin/env python3
"""Portable first-run setup for the Pilot Agent PoC."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


POC = Path(__file__).resolve().parents[1]
DATA = POC / "data"
CONFIG = POC / "config"
SCRIPTS = POC / "scripts"


def sync_tree(source: Path, target: Path) -> None:
    """Make a code-only destination match its versioned source."""
    target.mkdir(parents=True, exist_ok=True)
    wanted: set[Path] = set()
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        wanted.add(relative)
        destination = target / relative
        if item.is_symlink():
            raise RuntimeError(f"unsupported plugin/test source entry: {relative}")
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not item.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
        else:
            raise RuntimeError(f"unsupported plugin/test source entry: {relative}")
    for item in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.relative_to(target) not in wanted:
            if item.is_dir() and not item.is_symlink():
                item.rmdir()
            else:
                item.unlink()


def replace_keys(text: str, values: dict[str, str]) -> str:
    kept = [line for line in text.splitlines()
            if not ("=" in line and line.split("=", 1)[0] in values)]
    return "\n".join(kept + [f"{key}={value}" for key, value in values.items()]) + "\n"


def write_private(path: Path, text: str) -> None:
    # On Windows, the installer must provide a user-private directory/ACL.
    fd, name = tempfile.mkstemp(prefix=".pilot-env-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        if os.name != "nt":
            os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare_env() -> None:
    path = POC / ".env"
    if not path.exists():
        template = (POC / ".env.example").read_text(encoding="utf-8")
        write_private(path, replace_keys(template, {"API_SERVER_KEY": secrets.token_hex(32)}))
        print("Created .env with a generated API_SERVER_KEY.")
    text = path.read_text(encoding="utf-8")
    settings = dict(line.split("=", 1) for line in text.splitlines()
                    if "=" in line and not line.startswith("#"))
    updates: dict[str, str] = {}
    if not settings.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"):
        updates.update({
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME": "admin",
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": secrets.token_hex(16),
            "HERMES_DASHBOARD_BASIC_AUTH_SECRET": secrets.token_hex(32),
        })
    if not settings.get("HERMES_UID"):
        uid = str(os.getuid()) if hasattr(os, "getuid") else "10000"
        gid = str(os.getgid()) if hasattr(os, "getgid") else "10000"
        updates.update({"HERMES_UID": uid, "HERMES_GID": gid})
    if updates:
        write_private(path, replace_keys(text, updates))
        if "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD" in updates:
            print("Generated dashboard login in .env.")


def main() -> None:
    (DATA / "workspace").mkdir(parents=True, exist_ok=True)
    operator = DATA / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    for source, target in ((CONFIG / "boundary-tool-policy.yaml",
                            operator / "boundary-tool-policy.yaml"),
                           (SCRIPTS / "check-boundary-tools.py",
                            operator / "check-boundary-tools.py"),
                           (SCRIPTS / "run-gateway-with-boundary.py",
                            operator / "run-gateway-with-boundary.py")):
        shutil.copy2(source, target)
    for source, target in ((CONFIG / "config.yaml", DATA / "config.yaml"),
                           (CONFIG / "pilot-rules.yaml", DATA / "pilot-rules.yaml")):
        if not target.exists():
            shutil.copy2(source, target)
    sync_tree(POC / "plugins", DATA / "plugins")
    sync_tree(POC / "tests", DATA / "tests")
    shutil.copy2(CONFIG / "SOUL.md", DATA / "SOUL.md")
    for profile in (DATA / "profiles").glob("person-*"):
        if profile.is_dir():
            sync_tree(POC / "plugins", profile / "plugins")
            shutil.copy2(CONFIG / "SOUL.md", profile / "SOUL.md")
    prepare_env()
    subprocess.run([sys.executable, str(SCRIPTS / "detect-timezone.py")], check=True)
    subprocess.run([sys.executable, str(SCRIPTS / "sync-default-profile-env.py")], check=True)
    print("Ready. Next: docker compose up -d")


if __name__ == "__main__":
    main()
