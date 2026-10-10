#!/usr/bin/env bash
# POSIX compatibility wrapper. Native Windows setup can run: py -3 scripts/init.py
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/init.py
