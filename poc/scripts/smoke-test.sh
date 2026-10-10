#!/usr/bin/env bash
# Phase 0 smoke test: health check, then one agent turn that must use a tool
# (the terminal) through the agent's OpenAI-compatible API.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

base="http://127.0.0.1:8642"
echo "== health"; curl -sf "$base/health"; echo

echo "== agent turn (tool use)"
curl -sf "$base/v1/chat/completions" \
  -H "Authorization: Bearer ${API_SERVER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Run `uname -a` in your terminal and tell me the OS and CPU architecture in one sentence."}]}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"])'
