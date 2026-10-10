#!/usr/bin/env bash
# POSIX compatibility wrapper. Windows setup can call detect-timezone.py.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/detect-timezone.py
