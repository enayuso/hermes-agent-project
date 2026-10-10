#!/usr/bin/env bash
# Shell wrapper around scripts/start-sandbox-broker.py (macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
exec python3 scripts/start-sandbox-broker.py
