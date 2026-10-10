# 02 — PoC plan: a Dots / Claude Tag–style Pilot Agent

Goal: an always-on agent that lives in a chat app, works on a schedule without being asked, asks before risky actions, and can later be surfaced inside a host product.

Update 2026-10-07: the first `channel-watch` run completed and delivered. Routine origin, read-only place memory, and current-place job management are implemented; verification and limits are in [05-routine-scoping.md](05-routine-scoping.md). Earlier checklist entries below record the initial PoC sequence.

Base: Hermes Agent `v2026.9.24` in Docker, model = self-hosted vLLM `Inferact/Qwen3.8-Flash-Next-NVFP4` (`http://203.0.113.20:11434/v1`, 262k context).

From 04:40 to 05:15 UTC on 2026-10-07 the vLLM server was down and the PoC ran on the Furiosa NPU server (`furiosa-ai/gpt-oss-20b`, `http://203.0.113.30:11551/v1`). Back on Qwen since; the Furiosa values stay as a comment in `config.yaml` as a fallback.

## Phase 0 — Bring-up
- [x] Agent container running on the self-hosted model (`poc/`)
- [x] Smoke test: health + one tool-using turn via the OpenAI-compatible API (2026-10-07, ~25s, ran `uname -a` inside its container)
- [x] vLLM checked: native tool calls work, streaming and non-streaming
- [x] Slack app (Socket Mode) connected; chat in a DM (7.9s) and a channel @mention (51.6s) — app manifest in `poc/config/slack-manifest.json`
- [x] Exec approval buttons render in Slack: Deny blocked the command, Approve ran it (`approvals.mode: manual`; default `smart` lets an aux LLM skip the prompt)
- [x] A cron job that posts to Slack on schedule (one-shot job ran `date`, delivered to the home channel)
- [x] Memory persists across sessions and container restarts (fact saved via `memory` tool, recalled in a new DM after restart)

Phase 0 completed 2026-10-07.

## Phase 1 — Dots / Tag behaviours
Design and research: [03-phase1-dots-tag.md](03-phase1-dots-tag.md).
- [x] Allow / Ask / Block rules — `pilot-rules` plugin, 28/28 rule tests, verified through Hermes' resolver
- [x] Outlook mail + calendar connector configured (`ms-365-mcp-server`, 96 tools classified)
- [ ] Outlook sign-in (`scripts/outlook-login.sh`) — **halted 2026-10-07**: connector set `enabled: false` until the sign-in is approved; the morning briefing needs it
- [x] Morning briefing routine created (paused) — `scripts/setup-routines.sh`
- [x] Weekly proactive research routine created (active; first fire due 2026-10-12)
- [x] Ambient channel mode, shared thread sessions, Pilot persona, current owner's timezone configured (Asia/Seoul on this deployment; no product default)
- [x] Slack-read plugin (`pilot-slack-read`): channel history, threads, pins, Claude Tag access rule — 28/28 unit tests, live-verified against Slack without the model; pins need `pins:read` (manifest updated, app reinstall pending)
- [x] Gap pass 2: dashboard (auth verified), 👎 feedback trigger, channel-watch routine (active), slash-command coverage checked — see 03 §"Gap list pass 2"
- [ ] Decisions: voice-note speech-to-text provider, Teams, webhooks host, app icon
- [x] Behaviour tests on `furiosa-ai/gpt-oss-20b` (`scripts/behaviour-tests.sh`, 2026-10-07): 6/6 pass after fixes. Found and fixed: (a) the agent said its jobs run in UTC — Hermes tells the model to run `date`, and the container was UTC → container `TZ=Asia/Seoul`; (b) plugin tools sit behind Hermes' tool search with no per-tool "always visible" option, and the model sometimes didn't find the Slack tools → persona names them. Remaining model weakness: for a refused DM read it sometimes gives the wrong reason ("no Slack tool") instead of the access rule; no content leaked in 6/6 tries. Re-run on Qwen when vLLM is back.
- [x] Timezone is per person, not hardcoded (Claude Tag model): the agent's timezone = its owner's Slack profile (`scripts/detect-timezone.sh`, run by `init.sh`; override with `AGENT_TIMEZONE`, else host, else UTC), written to `.env` as `TZ` + `HERMES_TIMEZONE`. Each Slack turn also gets the sender's timezone and local time from their Slack profile (`pilot-slack-read` `pre_llm_call` hook), with a note to convert when it differs from the agent's.
- [x] Model comparison on the behaviour suite: **gpt-oss-20b** passed most checks but failed ~half of Slack reads (it called hidden tools directly instead of through Hermes' `tool_call` bridge) and gave wrong refusal reasons. **Qwen3.8-Flash** passed 7/7 and test 1 in 4/4 repeats with tool search left on; once replied in Chinese to an English prompt (intermittent; test 7 added).
- [x] Memory per place + isolation (Claude Tag model): `pilot-memory` provider replaces Hermes' shared built-in memory; `past_sessions` replaces unscoped `session_search` (blocked); `skill_manage` asks. Slack-side live checks passed. Scoped note writes now use Hermes' strict injection scanner. See [04](04-isolation-and-memory.md).
- [x] Exact Slack `!routines`, `!status`, and `!feedback` command handlers added and container-tested. The user confirmed the feedback card updates in place and later reported the revised `!status` and `!routines` Slack rendering looked right; see [03](03-phase1-dots-tag.md) and [05](05-routine-scoping.md).
- [ ] Live tests once the model server is back: rules in Slack (ask/block), briefing, research, ambient silence, thread steering, `/goal`, `/heartbeat`

## Phase 2 — Per-user isolation

Runtime and usage findings, including the read-only lifetime report and the missing Docker daemon boundary, are in [07-phase2-boundaries.md](07-phase2-boundaries.md). The current personal profiles isolate Hermes state but do not sandbox terminal execution.
- [x] Route each allowed user's one-to-one Slack DM to an empty, personal Hermes profile; migrate the owner's existing DM note and 7 sessions. Channels/group DMs stay on the service profile. A human DM and reply were verified. See [06](06-personal-dm-profiles.md).
- [x] Record physical model and auxiliary calls in a private, place-scoped token ledger; expose a read-only 24-hour report. Live API calls reconcile with Hermes session usage. See [07](07-phase2-boundaries.md).
- [ ] Separate control-plane identity/credentials per user and create disposable execution workers per Slack thread/session. An idle worker should be released while the transcript and memory remain; a reply should rebuild a clean worker. Broker Docker authority outside the gateway. See the verified lifecycle and Hermes caveats in [07](07-phase2-boundaries.md).
- [ ] Verify every execution path (terminal, file, browser, plugin, MCP, background/cron) against that worker and network boundary. Test concurrent threads, DMs, failure, restart, idle cleanup, and fresh-worker resume.
- [ ] Reconcile real Slack/cron physical model calls and measure GPU capacity; then choose explicit token/GPU-time allowances for organization, channels/group DMs, and personal DMs. Add atomic admission and turn bounds, separate from concurrency/rate controls. No dollar amount is known for the self-hosted model.

## Phase 3 — Product integration
- [ ] "Agent" tab in the host product talking to each user's Hermes `api_server`
- [ ] Decide delivery model with the task owner: downloadable/local vs hosted service

## Open items / known caveats
- 2026-10-07 ~03:00 UTC: the vLLM host (203.0.113.20) went unreachable (no ping, port closed); live tests paused.
- Latency: ~14.5k-token system prompt per call (Hermes' default toolsets); trim toolsets if speed matters.
- Restarting with new `.env` values needs the container recreated (`docker rm -f` + run), not `docker restart`.
- **The vLLM endpoint is reachable on a public IP with no auth.** Anyone who finds it can use the GPU. Worth raising separately.
- On macOS (Colima) the bind-mounted `data/` forces SQLite out of WAL mode (Hermes warns). Fine for the PoC; use a named volume or a Linux host for anything longer-lived.
- Hermes warns that the API server + `local` terminal backend gives API callers full terminal access inside the container. Port is published to host loopback only.
- `docker compose` plugin isn't installed on the dev Mac; the root README lists the equivalent `docker run`.
