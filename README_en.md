# hermes-agent-project

> 한국어: [README.md](README.md)

A proof of concept of an **always-on AI coworker that lives in Slack** — in the spirit of OpenAI Dots and
Claude Tag — built on **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (Nous Research) and a
self-hosted, OpenAI-compatible vLLM model. The agent is called **Pilot** in the code.

The goal was to understand what it actually takes to turn an open-source agent framework into something a
team could leave running in its chat: what it should do on its own, when it must ask, how memory and
permissions are scoped per channel and per person, and how to bound its usage. Most of the code and the
design/research notes in `docs/` were written together with **Claude Code**.

## What Pilot can do

| Area | What's implemented | Where |
|---|---|---|
| Ask-before-acting | Dots-style **Allow / Ask / Block** rules evaluated on every tool call, hot-reloaded from YAML | `poc/plugins/pilot-rules`, `poc/config/pilot-rules.yaml` |
| Slack awareness | Read-only tools for channel history, threads, pins and channel lists, with Claude Tag's cross-channel access rule | `poc/plugins/pilot-slack-read` |
| Chat etiquette | Per-thread mute, 👎 feedback, a per-channel "respond automatically" setting, `!status` / `!routines` / `!feedback` commands, and staying `[SILENT]` on chatter | `poc/plugins/pilot-slack-controls`, `poc/config/SOUL.md` |
| Scoped memory | Notes per channel / group DM / DM, workspace notes only from public channels, past sessions per place (replaces Hermes' shared memory) | `poc/plugins/pilot-memory` |
| Routines | Scheduled jobs (briefings, research, channel watch) that stay scoped to the channel or DM that created them | `poc/plugins/pilot-routines`, `poc/scripts/setup-routines.sh` |
| Per-person isolation | Each user's 1:1 DM routes to its own empty Hermes profile; channels stay on a shared service profile | `poc/scripts/provision-personal-dms.py` |
| Execution sandbox | Terminal/file tools run in disposable, network-restricted Docker workers created by a broker outside the gateway; egress is allow-listed | `poc/plugins/pilot-sandbox`, `poc/sandbox/` |
| Usage limits | Signed request origins, a loopback quota proxy in front of the model with atomic monthly token reservations per organization / channel / DM, and usage reports | `poc/usage/`, `poc/plugins/pilot-usage`, `poc/scripts/report-*.py` |
| Locale | Timezone taken from the owner's Slack profile (fallback host → UTC); replies follow the language of the message | `poc/scripts/detect-timezone.py` |

## Repository layout

```
docs/                 research, design notes and the running log of the PoC
  HANDOFF.md          current state, how to run/test/debug, gotchas, open items (start here)
  01-research.md      how Claude / ChatGPT / Grok built their agents; OpenClaw vs Hermes vs NemoClaw
  02-poc-plan.md      phases and status
  03…08-*.md          Dots/Tag mapping, isolation & memory, routine scoping, DM profiles, boundaries, locale
poc/
  docker-compose.yml  one Hermes container = one agent
  config/             config.yaml template, SOUL.md (persona), rules, Slack app manifest, policies
  plugins/            the pilot-* Hermes plugins listed above
  sandbox/ usage/     execution broker / egress and the quota proxy
  scripts/            init, smoke & behaviour tests, provisioning, reports, boundary launcher
  tests/              unit tests (kept outside plugin folders on purpose — Hermes imports every .py there)
```

## Getting started

### Prerequisites

- Docker (on Windows: Docker Desktop with Linux containers) and Python 3
- An OpenAI-compatible model endpoint with tool calling (tested with vLLM serving Qwen; set it in
  `poc/config/config.yaml`)
- A Slack app in Socket Mode — create it from `poc/config/slack-manifest.json`

### Run

```bash
cd poc
python3 -m pip install -r requirements-host.txt
python3 scripts/init.py   # creates data/ and .env (generates the API key, detects timezone)
# fill SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_USERS, ... in .env
docker compose up -d
./scripts/smoke-test.sh
```

Without the compose plugin:

```bash
docker run -d --name pilot-agent --restart unless-stopped \
  -v "$PWD/data:/opt/data" -p 127.0.0.1:8642:8642 -p 127.0.0.1:9119:9119 --env-file .env \
  -e API_SERVER_ENABLED=true -e API_SERVER_HOST=0.0.0.0 -e HERMES_DASHBOARD=1 \
  nousresearch/hermes-agent:v2026.9.24 gateway run
```

- OpenAI-compatible API: `http://127.0.0.1:8642/v1`
- Hermes dashboard (sessions, logs, scheduled jobs, usage): `http://127.0.0.1:9119` — login from
  `HERMES_DASHBOARD_BASIC_AUTH_*` in `.env`
- Rules live in `poc/config/pilot-rules.yaml`; the live copy is `poc/data/pilot-rules.yaml` (hot-reloaded)
- `poc/data/` (live state) and `poc/.env` (secrets) are gitignored

### Tests

```bash
# unit tests inside the running container
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-rules/test_rules.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-read/test_slack_read.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-slack-controls/test_controls.py
docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/pilot-memory/test_scoped_memory.py

# host-side tests that need no Hermes install
python3 poc/tests/test_quota.py
python3 poc/tests/test_model_proxy.py

# end-to-end behaviour tests through the API (run after any model change)
cd poc && ./scripts/behaviour-tests.sh        # all seven, ~2-3 min
```

The behaviour suite checks that the agent: reads a public channel by name, never leaks a DM, gets stopped by
the approval gate on a destructive command when unattended, stays silent on chatter, answers short and
correct, and replies in the language it was asked in (Korean/English).

## Notes

- Slack IDs, IPs (`203.0.113.x` documentation range) and names in this repo are placeholders.
- This is a PoC: the personal profiles share one container, so they isolate state but are not a hard
  security boundary — see `docs/07-phase2-boundaries.md` for what is and isn't enforced yet.
