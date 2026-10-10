#!/usr/bin/env python3
"""Switch this PoC's Docker gateway to the reviewed sandbox pilot or roll back.

Uses Docker CLI because this host has no Compose plugin. Keeps the stopped old
container and byte-for-byte profile configs for rollback. Never prints env,
tokens, commands from user turns, or Docker logs.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


POC = Path(__file__).resolve().parents[1]
DATA = POC / "data"
TOKEN = POC / ".sandbox-broker-token"
POLICY = POC / "config" / "egress-policy.json"
IMAGE = "nousresearch/hermes-agent:v2026.9.24"
OLD = "pilot-agent-rollback"
NEW = "pilot-agent"
HEALTH = "http://127.0.0.1:8642/health"


def _config_module():
    source = Path(__file__).with_name("apply-boundary-config.py")
    spec = importlib.util.spec_from_file_location("boundary_config", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def docker(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], text=True, capture_output=True,
                            stdin=subprocess.DEVNULL, timeout=150, check=False)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"Docker {args[0]} failed (exit {result.returncode})")
    return result


def container_exists(name: str) -> bool:
    return docker("container", "inspect", "--format", "{{.Id}}", name,
                  allow_failure=True).returncode == 0


def health(url: str, *, token: str | None = None) -> bool:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def wait_health(url: str, *, token: str | None = None, seconds: int = 80) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if health(url, token=token):
            return True
        time.sleep(2)
    return False


def ensure_broker() -> None:
    if not TOKEN.is_file():
        raise RuntimeError("broker token file is missing; run start-sandbox-broker.py once")
    token = TOKEN.read_text(encoding="utf-8").strip()
    if health("http://127.0.0.1:8787/health", token=token):
        return
    log = DATA / "operator" / "broker.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab", buffering=0) as output:
        env = dict(os.environ, PILOT_EGRESS_POLICY_FILE=str(POLICY))
        child = subprocess.Popen(
            [sys.executable, str(POC / "scripts" / "start-sandbox-broker.py")],
            cwd=POC, env=env, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True)
    if not wait_health("http://127.0.0.1:8787/health", token=token, seconds=45):
        child.terminate()
        raise RuntimeError("host sandbox broker did not become ready")
    (DATA / "operator" / "broker.pid").write_text(str(child.pid), encoding="ascii")


def pilot_command() -> list[str]:
    return [
        "run", "-d", "--name", NEW, "--restart", "unless-stopped",
        "--env-file", str(POC / ".env"),
        "-e", "API_SERVER_ENABLED=true", "-e", "API_SERVER_HOST=0.0.0.0",
        "-e", "HERMES_DASHBOARD=1",
        "-e", "PILOT_SANDBOX_BROKER_URL=http://host.docker.internal:8787",
        "-e", "PILOT_SANDBOX_TOKEN_FILE=/run/pilot-broker-token",
        "-e", "HERMES_WRITE_SAFE_ROOT=/workspace",
        "--add-host", "host.docker.internal:host-gateway",
        "--mount", f"type=bind,source={DATA.resolve()},target=/opt/data",
        "--mount", f"type=bind,source={TOKEN.resolve()},target=/run/pilot-broker-token,readonly",
        "-p", "127.0.0.1:8642:8642", "-p", "127.0.0.1:9119:9119",
        IMAGE, "/opt/hermes/.venv/bin/python", "/opt/data/operator/run-gateway-with-boundary.py",
    ]


def rollback() -> None:
    if not container_exists(OLD):
        raise RuntimeError("saved gateway container is missing")
    if container_exists(NEW):
        docker("stop", NEW, allow_failure=True)
        docker("rm", "-f", NEW)
    config = _config_module()
    homes = config.profiles(DATA)
    if all(config.backup_path(home).is_file() for home in homes):
        config.restore(DATA)
    else:
        # An interrupted apply may have made only some backups. Restore those
        # profiles; the others were never rewritten.
        for home in homes:
            backup = config.backup_path(home)
            if backup.is_file():
                config.atomic_private(config.config_path(home), backup.read_bytes())
                backup.unlink()
    docker("rename", OLD, NEW)
    docker("start", NEW)
    if not wait_health(HEALTH):
        raise RuntimeError("old gateway was restored but did not become healthy")
    print("Original gateway and profile configs restored; health HTTP 200.")


def apply() -> None:
    if container_exists(OLD):
        raise RuntimeError("rollback container already exists; resolve the earlier pilot first")
    if not container_exists(NEW):
        raise RuntimeError("current gateway container is missing")
    if not (POC / ".env").is_file():
        raise RuntimeError("gateway env file is missing")
    ensure_broker()
    config = _config_module()
    policy = __import__("yaml").safe_load(
        (POC / "config" / "boundary-tool-policy.yaml").read_text(encoding="utf-8"))
    if policy.get("version") != 1:
        raise RuntimeError("unsupported boundary policy")
    try:
        docker("stop", NEW)
        docker("rename", NEW, OLD)
        config.apply(DATA, policy)
        docker(*pilot_command())
        if not wait_health(HEALTH):
            raise RuntimeError("sandbox gateway did not become healthy")
    except BaseException:
        # Restore before surfacing any error. A hard process kill can still
        # leave a recoverable OLD container and config backups for rollback().
        if container_exists(OLD):
            rollback()
        elif container_exists(NEW):
            docker("start", NEW, allow_failure=True)
        raise
    print("Sandbox pilot gateway healthy; original container and configs retained for rollback.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("apply", "rollback"))
    args = parser.parse_args()
    apply() if args.operation == "apply" else rollback()


if __name__ == "__main__":
    main()
