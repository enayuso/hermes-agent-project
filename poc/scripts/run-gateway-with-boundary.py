#!/usr/bin/env python3
"""Cutover-only gateway launcher: reject a widened tool policy before start."""

from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


HOME = Path("/opt/data")
PYTHON = "/opt/hermes/.venv/bin/python"
HERMES = "/opt/hermes/.venv/bin/hermes"
CHECK = HOME / "operator" / "check-boundary-tools.py"
POLICY = HOME / "operator" / "boundary-tool-policy.yaml"
PLUGIN = HOME / "plugins" / "pilot-sandbox"
MEMORY_PLUGIN = HOME / "plugins" / "pilot-memory"
QUOTA_ENV = ("PILOT_QUOTA_POLICY_FILE", "PILOT_QUOTA_LEDGER_FILE",
             "PILOT_QUOTA_LIB_DIR", "PILOT_QUOTA_SIGNING_KEY_FILE",
             "PILOT_QUOTA_PROXY_BASE_URL")


def check_model_quota(homes: list[Path]) -> None:
    """Require an exact local provider route before enabling the fail-open Hermes hooks."""
    values = {name: os.environ.get(name, "") for name in QUOTA_ENV}
    if not any(values.values()):
        return
    if not all(values.values()):
        raise RuntimeError("model quota cutover configuration is incomplete")
    library = Path(values["PILOT_QUOTA_LIB_DIR"])
    if not (library / "usage" / "origin.py").is_file():
        raise RuntimeError("model quota library is missing")
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from usage.origin import sign
    from usage.policy import Policy
    import yaml

    policy = Policy.load(Path(values["PILOT_QUOTA_POLICY_FILE"]))
    proxy_url = values["PILOT_QUOTA_PROXY_BASE_URL"].rstrip("/")
    parsed = urlsplit(proxy_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.path != "/v1" or parsed.port is None):
        raise RuntimeError("model quota proxy must be on this gateway's loopback")
    key = Path(values["PILOT_QUOTA_SIGNING_KEY_FILE"]).read_bytes().strip()
    if len(key) < 32:
        raise RuntimeError("model quota signing key is invalid")
    for home in homes:
        config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        model = config.get("model") or {}
        enabled = (config.get("plugins") or {}).get("enabled") or []
        if (model.get("provider") != "custom"
                or str(model.get("base_url") or "").rstrip("/") != proxy_url
                or model.get("default") != policy.model
                or model.get("context_length") != policy.context_window_tokens
                or "pilot-usage" not in enabled):
            raise RuntimeError(f"model quota route mismatch for {home.name}; gateway not started")
    # A signed invalid-model probe verifies that the proxy has the matching key
    # and declines before reserving tokens or contacting the upstream model.
    credential = sign(key, kind="service_slack", profile="service", place="TTEST:CTEST",
                      session_id="startup_check")
    request = urllib.request.Request(
        proxy_url + "/chat/completions", b'{"model":"__pilot_invalid_probe__"}',
        headers={"Content-Type": "application/json", "X-Pilot-Origin": credential},
        method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(request, timeout=3)
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read()).get("error", {}).get("code")
        except (ValueError, AttributeError):
            code = None
        if exc.code == 400 and code == "invalid_request":
            return
    except (OSError, ValueError):
        pass
    raise RuntimeError("model quota proxy failed its signed preflight")


def check(home: Path) -> None:
    env = dict(os.environ, HERMES_HOME=str(home))
    result = subprocess.run(
        [PYTHON, str(CHECK), "--policy", str(POLICY), "--plugin-dir", str(PLUGIN),
         "--memory-plugin-dir", str(MEMORY_PLUGIN)],
        cwd="/opt/hermes", env=env, timeout=45, check=False)
    if result.returncode:
        raise RuntimeError(f"boundary tool check failed for {home.name}; gateway not started")


def run_with_model_quota(homes: list[Path]) -> int:
    """Keep the local quota proxy and gateway under one supervised service."""
    library = Path(os.environ["PILOT_QUOTA_LIB_DIR"])
    if not (library / "usage" / "model_proxy.py").is_file():
        raise RuntimeError("model quota library is missing")
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from usage.model_proxy import ModelProxy
    from usage.policy import Policy

    policy = Policy.load(Path(os.environ["PILOT_QUOTA_POLICY_FILE"]))
    secret = Path(os.environ["PILOT_QUOTA_SIGNING_KEY_FILE"]).read_bytes().strip()
    ledger = Path(os.environ["PILOT_QUOTA_LEDGER_FILE"])
    port = urlsplit(os.environ["PILOT_QUOTA_PROXY_BASE_URL"]).port
    api_key_path = os.environ.get("PILOT_QUOTA_UPSTREAM_API_KEY_FILE")
    api_key = Path(api_key_path).read_text(encoding="utf-8").strip() if api_key_path else ""
    with ModelProxy(("127.0.0.1", port), policy=policy, secret=secret,
                    ledger=ledger, upstream_api_key=api_key) as proxy:
        thread = threading.Thread(target=proxy.serve_forever,
                                  kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()
        process = None
        old_handlers = {}
        try:
            check_model_quota(homes)
            process = subprocess.Popen([HERMES, "gateway", "run"])

            def stop(signum, frame):
                del signum, frame
                if process.poll() is None:
                    process.terminate()

            for sig in (signal.SIGTERM, signal.SIGINT):
                old_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, stop)
            while process.poll() is None:
                if not thread.is_alive():
                    raise RuntimeError("model quota proxy stopped; gateway terminating")
                time.sleep(0.25)
            return int(process.returncode)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # HTTPServer.shutdown() waits for serve_forever() to signal its exit.
            # If the proxy thread failed before entering that loop, it would wait forever.
            if thread.is_alive():
                proxy.shutdown()
            thread.join(timeout=3)


def main() -> None:
    if not os.environ.get("PILOT_SANDBOX_BROKER_URL") or not os.environ.get("PILOT_SANDBOX_TOKEN_FILE"):
        raise RuntimeError("sandbox broker route and token file are required")
    if os.environ.get("HERMES_WRITE_SAFE_ROOT") != "/workspace":
        raise RuntimeError("file write root must be the isolated /workspace")
    token = Path(os.environ["PILOT_SANDBOX_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        os.environ["PILOT_SANDBOX_BROKER_URL"].rstrip("/") + "/health",
        headers={"Authorization": "Bearer " + token})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("sandbox broker is not ready")
    except (OSError, ValueError) as exc:
        raise RuntimeError("sandbox broker is not ready") from exc
    homes = [HOME]
    for profile in sorted((HOME / "profiles").glob("person-*")):
        if profile.is_dir():
            homes.append(profile)
    for home in homes:
        check(home)
    if any(os.environ.get(name) for name in QUOTA_ENV):
        raise SystemExit(run_with_model_quota(homes))
    os.execv(HERMES, [HERMES, "gateway", "run"])


if __name__ == "__main__":
    main()
