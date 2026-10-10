#!/usr/bin/env bash
# Creates the PoC's standing routines (Dots "scheduled work" / Claude Tag "routines").
# Usage: ./scripts/setup-routines.sh [--paused]
# Jobs post to SLACK_HOME_CHANNEL. Times are in the agent's timezone (HERMES_TIMEZONE in .env).
# Existing jobs with the same name are left alone; remove them first to redefine.
set -euo pipefail

hermes() { docker exec -u hermes pilot-agent /opt/hermes/.venv/bin/hermes "$@"; }

paused=()
if [ "${1:-}" = "--paused" ]; then
  paused=(--paused --paused-reason "created paused; resume with: hermes cron resume <id>")
fi

existing="$(hermes cron list 2>/dev/null || true)"

create() {
  local name="$1" schedule="$2" prompt="$3"
  if grep -q "Name: *${name}\$" <<<"$existing"; then
    echo "skip: ${name} already exists"
    return
  fi
  if [ "$name" = "morning-briefing" ] && [ "${#paused[@]}" -eq 0 ]; then
    # Personal Outlook access is still halted; never create this job active by default.
    hermes cron create "$schedule" "$prompt" --name "$name" --deliver slack \
      --paused --paused-reason "Outlook and per-person profile are not ready"
  else
    hermes cron create "$schedule" "$prompt" --name "$name" --deliver slack ${paused[@]+"${paused[@]}"}
  fi
}

create "morning-briefing" "30 8 * * 1-5" \
"Morning briefing for the person you work for. Gather: (1) today's Outlook calendar events, with \
times, attendees and anything that needs preparation; (2) unread or flagged Outlook mail from the \
last 24 hours that needs a reply or a decision, grouped by urgency; (3) anything you are already \
tracking that is waiting on them. Do not send, accept, move or delete anything: this is a read-only \
run. Post one short briefing: the outcome first, then each item on one line. If a source is \
unavailable, say which one in a single line and continue with the rest."

create "weekly-agent-landscape" "0 9 * * 1" \
"Proactive research on a standing topic: new releases and changes this past week in always-on AI \
agent products (OpenAI Dots, Anthropic Claude Tag and Claude Code, xAI Grok Bot, Hermes Agent, \
OpenClaw, NVIDIA NemoClaw). Use web search, prefer primary sources (official announcements, docs, \
release notes), and verify dates. Post only what changed this week, one line per item with its \
source link, then one line on what it means for the Pilot Agent PoC. If nothing meaningful \
changed, respond with exactly [SILENT]."

create "channel-watch" "0 18 * * 1-5" \
"Daily channel watch (end of day). Use slack_list_channels to find the public channels you can read, \
then slack_read_channel with since_hours=24 on each (and slack_read_thread for threads that show \
replies). Post one digest of unresolved questions or requests from people, decisions that affect \
work, and blockers. Do not treat your own unanswered suggestions as requests waiting on a person. \
Check this channel's notes before claiming what it remembers. One line per item with the channel \
name. Skip chatter and anything already resolved. If nothing needs attention, \
respond with exactly [SILENT]."

# Hermes' CLI has no --origin option. Chat-created jobs capture it automatically; these two
# operator-created public PoC jobs need their verified home channel attached explicitly.
# The personal Outlook briefing stays paused and unscoped until per-person profiles exist.
docker exec -i -u hermes pilot-agent /opt/hermes/.venv/bin/python - \
  < "$(dirname "$0")/provision-routine-origins.py"
