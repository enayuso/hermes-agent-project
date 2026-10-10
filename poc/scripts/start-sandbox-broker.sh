#!/usr/bin/env bash
# POSIX convenience wrapper; the Python entry point also runs on Windows.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
exec python3 scripts/start-sandbox-broker.py
