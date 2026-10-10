#!/usr/bin/env python3
"""Copy only the host broker runtime to private macOS Application Support.

LaunchAgents cannot assume access to a source checkout under Documents.
The generated service points to this copy; rerun staging after broker updates.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


POC = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = Path.home() / "Library" / "Application Support" / "PilotAgent" / "broker"
FILES = (
    "scripts/start-sandbox-broker.py",
    "sandbox/broker.py",
    "sandbox/egress.py",
    "sandbox/search.py",
    "config/egress-policy.json",
    ".sandbox-broker-token",
)


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("runtime directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_private(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("runtime file must not be a symlink")
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=".pilot-stage-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stage(source: Path = POC, runtime: Path = DEFAULT_RUNTIME) -> None:
    source = source.resolve()
    if runtime.is_symlink():
        raise ValueError("runtime root must not be a symlink")
    token = source / ".sandbox-broker-token"
    if token.is_symlink() or not token.is_file() or len(token.read_text().strip()) < 32:
        raise ValueError("private broker token is missing or invalid")
    content = {}
    for relative in FILES:
        item = source / relative
        if item.is_symlink() or not item.is_file():
            raise ValueError(f"broker runtime file missing: {relative}")
        content[relative] = item.read_bytes()
    private_dir(runtime)
    for relative, data in content.items():
        atomic_private(runtime / relative, data)
    private_dir(runtime / "data" / "operator")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("this staging script is for macOS only")
    stage(runtime=args.runtime_root.expanduser())
    print(f"Staged {len(FILES)} broker files in {args.runtime_root.expanduser()}")


if __name__ == "__main__":
    main()
