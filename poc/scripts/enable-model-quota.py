#!/usr/bin/env python3
"""Enable the approved pilot token caps with a saved gateway/config rollback.

The current container is stopped and retained as pilot-agent-pre-quota. The
original pre-sandbox rollback container is untouched. No secrets are printed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import sys
from pathlib import Path


POC = Path(__file__).resolve().parents[1]
DATA = POC / "data"
OPERATOR = DATA / "operator"
BACKUP = OPERATOR / "backup-quota-cutover-20261008"
POLICY = OPERATOR / "model-quota-policy.json"
KEY = OPERATOR / "model-quota-signing.key"
LEDGER = OPERATOR / "model-quota-ledger.db"
LIBRARY = OPERATOR / "quota-lib"
PROXY_URL = "http://127.0.0.1:8791/v1"
PREVIOUS = "pilot-agent-pre-quota"
MODEL = "Inferact/Qwen3.8-Flash-Next-NVFP4"
UPSTREAM = "http://203.0.113.20:11434/v1"
CAPS = {"organization_tokens": 20_000_000,
        "default_channel_tokens": 8_000_000,
        "personal_dm_tokens": 3_000_000}


def pilot():
    source = POC / "scripts" / "pilot-boundary.py"
    spec = importlib.util.spec_from_file_location("pilot_boundary", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def homes() -> list[Path]:
    return [DATA, *sorted(p for p in (DATA / "profiles").glob("person-*") if p.is_dir())]


def config_paths() -> list[Path]:
    return [home / "config.yaml" for home in homes()]


def _private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_configs() -> None:
    import yaml
    for path in config_paths():
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        model = cfg.get("model") or {}
        if (model.get("provider") != "custom" or model.get("default") != MODEL
                or str(model.get("base_url") or "").rstrip("/") != UPSTREAM
                or model.get("context_length") != 262144
                or "pilot-usage" not in ((cfg.get("plugins") or {}).get("enabled") or [])):
            raise RuntimeError(f"unexpected active model configuration in {path.parent.name}")


def _prepare() -> None:
    sys.path.insert(0, str(POC))
    from usage.policy import Policy as QuotaPolicy

    _validate_configs()
    if BACKUP.exists() or POLICY.exists() or KEY.exists() or LEDGER.exists():
        raise RuntimeError("quota cutover artifacts already exist; inspect before retrying")
    BACKUP.mkdir(parents=True)
    for path in config_paths():
        relative = path.relative_to(DATA)
        copy = BACKUP / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy)
    payload = {"enabled": True, "model": MODEL, "upstream_base_url": UPSTREAM,
               "timezone": "UTC", "context_window_tokens": 262144,
               "max_output_tokens": 262144, **CAPS}
    _private_write(POLICY, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    QuotaPolicy.load(POLICY)
    _private_write(KEY, secrets.token_bytes(48))
    target = LIBRARY / "usage"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "origin.py", "policy.py", "quota.py", "model_proxy.py"):
        shutil.copy2(POC / "usage" / name, target / name)
    shutil.copy2(POC / "scripts" / "run-gateway-with-boundary.py",
                 OPERATOR / "run-gateway-with-boundary.py")


def _quota_command() -> list[str]:
    p = pilot()
    command = p.pilot_command()
    env = {
        "PILOT_QUOTA_POLICY_FILE": "/opt/data/operator/model-quota-policy.json",
        "PILOT_QUOTA_LEDGER_FILE": "/opt/data/operator/model-quota-ledger.db",
        "PILOT_QUOTA_LIB_DIR": "/opt/data/operator/quota-lib",
        "PILOT_QUOTA_SIGNING_KEY_FILE": "/opt/data/operator/model-quota-signing.key",
        "PILOT_QUOTA_PROXY_BASE_URL": PROXY_URL,
    }
    offset = command.index(p.IMAGE)
    extra = [item for pair in env.items() for item in ("-e", f"{pair[0]}={pair[1]}")]
    return command[:offset] + extra + command[offset:]


def _restore_configs() -> None:
    for path in config_paths():
        copy = BACKUP / path.relative_to(DATA)
        if copy.is_file():
            shutil.copy2(copy, path)


def rollback() -> None:
    p = pilot()
    if not p.container_exists(PREVIOUS):
        raise RuntimeError("saved pre-quota gateway is missing")
    if p.container_exists(p.NEW):
        p.docker("stop", p.NEW, allow_failure=True)
        p.docker("rm", "-f", p.NEW)
    _restore_configs()
    p.docker("rename", PREVIOUS, p.NEW)
    p.docker("start", p.NEW)
    if not p.wait_health(p.HEALTH):
        raise RuntimeError("pre-quota gateway was restored but did not become healthy")
    print("Pre-quota gateway and profile configs restored; health HTTP 200.")


def apply() -> None:
    p = pilot()
    if not p.container_exists(p.NEW) or p.container_exists(PREVIOUS):
        raise RuntimeError("unexpected gateway container state")
    if not p.health(p.HEALTH):
        raise RuntimeError("current gateway is not healthy")
    _prepare()
    switched = False
    try:
        p.docker("stop", p.NEW)
        p.docker("rename", p.NEW, PREVIOUS)
        switched = True
        for path in config_paths():
            value = path.read_text(encoding="utf-8")
            expected = f"  base_url: {UPSTREAM}"
            if value.count(expected) != 1:
                raise RuntimeError(f"ambiguous model base URL in {path.parent.name}")
            path.write_text(value.replace(expected, f"  base_url: {PROXY_URL}"), encoding="utf-8")
        p.docker(*_quota_command())
        if not p.wait_health(p.HEALTH):
            raise RuntimeError("quota gateway did not become healthy")
    except BaseException:
        if switched:
            rollback()
        raise
    print("Quota gateway healthy; current caps 20M organization / 8M channel / 3M personal DM.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "rollback"))
    args = parser.parse_args()
    apply() if args.operation == "apply" else rollback()


if __name__ == "__main__":
    main()
