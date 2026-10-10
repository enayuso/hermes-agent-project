#!/usr/bin/env python3
"""Apply or restore the reviewed execution policy across all gateway profiles.

Run only while the gateway container is stopped. Each profile keeps a private
byte-for-byte backup; a second apply refuses to overwrite that rollback point.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import yaml


POC = Path(__file__).resolve().parents[1]
BACKUP_SUFFIX = ".before-boundary"


def profiles(data: Path) -> list[Path]:
    return [data, *sorted(p for p in (data / "profiles").glob("person-*") if p.is_dir())]


def config_path(home: Path) -> Path:
    return home / "config.yaml"


def backup_path(home: Path) -> Path:
    return home / ("config.yaml" + BACKUP_SUFFIX)


def atomic_private(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def candidate(raw: bytes, policy: dict) -> bytes:
    config = yaml.safe_load(raw) or {}
    if not isinstance(config, dict):
        raise ValueError("profile config is not a mapping")
    config.setdefault("terminal", {}).update({
        "backend": "pilot_sandbox", "cwd": "/workspace",
        "container_persistent": False})
    config.setdefault("agent", {})["disabled_toolsets"] = policy["disabled_toolsets"]
    config.setdefault("plugins", {})["enabled"] = policy["enabled_plugins"]
    config["platform_toolsets"] = policy["platform_toolsets"]
    config["known_plugin_toolsets"] = policy["known_plugin_toolsets"]
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")


def apply(data: Path, policy: dict) -> list[Path]:
    homes = profiles(data)
    originals = {}
    for home in homes:
        path = config_path(home)
        if not path.is_file():
            raise FileNotFoundError(path)
        if backup_path(home).exists():
            raise FileExistsError(f"rollback copy already exists for {home.name}")
        originals[home] = path.read_bytes()
    prepared = {home: candidate(raw, policy) for home, raw in originals.items()}
    try:
        for home, raw in originals.items():
            atomic_private(backup_path(home), raw)
        for home, raw in prepared.items():
            atomic_private(config_path(home), raw)
    except BaseException:
        for home, raw in originals.items():
            if backup_path(home).exists():
                atomic_private(config_path(home), raw)
        raise
    return homes


def restore(data: Path) -> list[Path]:
    homes = profiles(data)
    for home in homes:
        if not backup_path(home).is_file():
            raise FileNotFoundError(f"rollback copy missing for {home.name}")
    for home in homes:
        atomic_private(config_path(home), backup_path(home).read_bytes())
    for home in homes:
        backup_path(home).unlink()
    return homes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "restore"))
    parser.add_argument("--data", type=Path, default=POC / "data")
    parser.add_argument("--policy", type=Path,
                        default=POC / "config" / "boundary-tool-policy.yaml")
    args = parser.parse_args()
    policy = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    if policy.get("version") != 1:
        raise SystemExit("unsupported boundary policy")
    homes = apply(args.data, policy) if args.operation == "apply" else restore(args.data)
    print(f"{args.operation} completed for {len(homes)} profiles; no config values displayed.")


if __name__ == "__main__":
    main()
