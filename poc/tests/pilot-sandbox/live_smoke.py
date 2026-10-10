#!/usr/bin/env python3
"""Explicit broker integration smoke; run only against a started test broker."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


BASE = os.environ.get("PILOT_SANDBOX_BROKER_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = Path(os.environ.get("PILOT_SANDBOX_TOKEN_FILE",
                            str(Path(__file__).resolve().parents[2] / ".sandbox-broker-token"))).read_text().strip()


def call(action: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + "/" + action, json.dumps(payload).encode(),
        {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def exec_in(session: str, command: str, timeout: int = 15) -> dict:
    return call("exec", {"session": session, "command": command, "timeout": timeout})


def main() -> None:
    a, b = "pilot_smoke_a", "pilot_smoke_b"
    try:
        first = exec_in(a, "id -u; pwd; test ! -e /var/run/docker.sock; test ! -e /opt/data/.env")
        assert first["returncode"] == 0 and first["output"].splitlines()[:2] == ["65532", "/workspace"], first
        assert exec_in(a, "printf private > /workspace/marker")["returncode"] == 0
        assert exec_in(a, "test -f /workspace/marker")["returncode"] == 0
        assert exec_in(a, "exit 124")["returncode"] == 124
        assert exec_in(a, "test -f /workspace/marker")["returncode"] == 0
        assert exec_in(b, "test ! -e /workspace/marker")["returncode"] == 0
        assert exec_in(a, "sleep 2", timeout=1)["returncode"] == 124
        assert exec_in(a, "test ! -e /workspace/marker")["returncode"] == 0
        assert exec_in(a, "printf private > /workspace/marker")["returncode"] == 0
        oversized = exec_in(a, "python -c \"import sys; sys.stdout.write('x'*1048577)\"")
        assert oversized == {"output": "sandbox output exceeded 1 MiB", "returncode": 125}, oversized
        assert exec_in(a, "test ! -e /workspace/marker")["returncode"] == 0
        call("release", {"session": a})
        assert exec_in(a, "test ! -e /workspace/marker")["returncode"] == 0
        print("Broker integration passed: restricted worker, separate threads, fresh worker after release.")
    finally:
        call("release", {"session": a})
        call("release", {"session": b})


if __name__ == "__main__":
    main()
