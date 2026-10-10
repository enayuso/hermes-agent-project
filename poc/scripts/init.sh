#!/usr/bin/env bash
# Shell wrapper around scripts/init.py (macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/init.py
