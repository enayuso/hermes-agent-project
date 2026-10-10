#!/usr/bin/env python3
"""Portable foreground launcher for the restricted host broker.

Requires Docker CLI access on this host (macOS: Docker Desktop or Colima). The
token file is generated with owner-only permissions.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path


POC = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN = POC / ".sandbox-broker-token"


def token_path() -> Path:
    path = Path(os.environ.get("PILOT_BROKER_TOKEN_FILE", str(DEFAULT_TOKEN))).expanduser()
    if not path.is_absolute():
        raise ValueError("broker token file must use an absolute path")
    if not path.exists():
        if os.name == "nt":
            raise ValueError("provision an ACL-protected PILOT_BROKER_TOKEN_FILE on Windows")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_hex(32) + "\n")
    if not path.is_file() or path.is_symlink():
        raise ValueError("broker token path must be a regular file")
    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ValueError("broker token file must be readable only by its owner")
    if len(path.read_text(encoding="utf-8").strip()) < 32:
        raise ValueError("broker token must contain at least 32 characters")
    return path


def main() -> None:
    os.environ["PILOT_BROKER_TOKEN_FILE"] = str(token_path())
    sys.path.insert(0, str(POC / "sandbox"))
    from broker import bind_address, main as run_broker

    host, port = bind_address()
    print(f"Starting Pilot sandbox broker on {host}:{port}; checking container access.", flush=True)
    try:
        run_broker()
    except KeyboardInterrupt:
        print("Pilot sandbox broker stopped.", flush=True)


if __name__ == "__main__":
    main()
