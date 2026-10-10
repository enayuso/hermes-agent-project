#!/usr/bin/env python3
"""Render the Mac pilot's user LaunchAgent without installing or starting it.

The broker runs as the logged-in user, like this host's Colima service. Native
Windows and Linux service registration is deliberately handled separately.
"""

from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path


LABEL = "com.example.pilot-sandbox-broker"
DEFAULT_RUNTIME = Path.home() / "Library" / "Application Support" / "PilotAgent" / "broker"


def definition(*, poc: Path = DEFAULT_RUNTIME, python: Path = Path(sys.executable)) -> dict:
    poc = poc.resolve()
    python = python.resolve()
    if not python.is_file():
        raise ValueError("Python executable is missing")
    launcher = poc / "scripts" / "start-sandbox-broker.py"
    policy = poc / "config" / "egress-policy.json"
    if not launcher.is_file() or not policy.is_file():
        raise ValueError("broker launcher or egress policy is missing")
    logs = poc / "data" / "operator"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(launcher)],
        "WorkingDirectory": str(poc),
        "EnvironmentVariables": {
            "PILOT_EGRESS_POLICY_FILE": str(policy),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "StandardOutPath": str(logs / "broker-launchd.log"),
        "StandardErrorPath": str(logs / "broker-launchd.log"),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME,
                        help="private runtime root containing scripts, sandbox, config and token")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("this renderer is for macOS only")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plistlib.dumps(definition(poc=args.runtime_root), sort_keys=True))
    output.chmod(0o600)
    print(f"Rendered {LABEL} at {output}")


if __name__ == "__main__":
    main()
