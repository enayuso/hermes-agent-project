#!/usr/bin/env bash
# Shell wrapper around scripts/detect-timezone.py (macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/detect-timezone.py
