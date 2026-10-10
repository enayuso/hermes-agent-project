# Pilot Agent PoC — Handoff

**Live quota cutover, 2026-10-08 ~16:51 KST:** The owner approved pilot monthly token caps of **20,000,000 organization channels / 8,000,000 default per channel / 3,000,000 per personal DM** after review of official Claude Tag and Dots limit documentation and Pilot's observed usage. `poc/scripts/enable-model-quota.py apply` preserved the former live container as `pilot-agent-pre-quota` and both profile configs under `poc/data/operator/backup-quota-cutover-20261008`; the older `pilot-agent-rollback` container remains untouched. Both active profiles now route the exact model through the loopback quota proxy supervised with the gateway. Gateway and proxy health returned HTTP 200. A live unsigned probe received HTTP 403; a signed streaming probe reached vLLM and settled 57 tokens to both organization and channel scopes in the quota ledger. New reservations also carry the verified session ID and origin kind for per-fire reconciliation; the migration kept existing charges. The proxy preserves Hermes' prior behavior when an output-token bound is omitted. A disposable real-vLLM proxy test passed before cutover. A direct signed personal-DM proxy probe settled 57 tokens in its personal scope only; the organization and channel scopes were not charged. **The gate is active, but an ordinary human Slack channel and personal DM turn through Hermes, a live limit refusal, and the 18:00 scheduled fire still need end-to-end verification.** `python3 poc/scripts/enable-model-quota.py rollback` restores the pre-quota container and profile configs if required. Earlier entries below are historical snapshots.

**Routine/limit follow-up, 2026-10-08 ~16:25 KST:** The user requested the next routine reconciliation and a fact-based path to numeric limits. The two existing operator-created channel jobs lacked Slack `scope_id`; the bot's Slack `auth_test` returned workspace `T0AAAAAAAAA`. A backed-up, two-phase checked update attached that workspace to both origins without changing their schedule or delivery. `channel-watch` remains due at 18:00 KST, with the existing 18:10 KST thread heartbeat set to inspect its real run. The audit classifier now rejects missing/invalid workspace on scheduled channel calls and recognizes future personal-DM routine calls separately; source and live service/personal copies were reloaded, and gateway health returned 200. Before this fire, the request ledger has 21 service Slack calls (165,632 input + 1,718 output tokens), three personal DM calls (20,265 + 205), and three API calls (32,068 + 48). The prior `channel-watch` used 68,577 tokens in 26.9 seconds, according to cron's own audit: 49,226 uncached input, 18,000 cached input, and 1,351 output. Hermes' `session_model_usage` agrees when its separate `cache_read_tokens` column is included. A single read-only vLLM metrics snapshot saw two engines, five running requests, zero waiting, and 3%/9% KV-cache use; those aggregate metrics include other clients, so they do not justify a Pilot spending cap. The optional gateway launcher now starts and supervises its loopback quota proxy in the same container whenever an explicit quota policy is configured.  A two-token live vLLM streaming probe with `stream_options.include_usage` returned a final `usage.total_tokens` and `[DONE]`, confirming the proxy can settle completed streams against provider usage. **No numeric caps or live proxy route are active.**

**Live load check, 2026-10-08:** The staged `pilot-usage` and `pilot-slack-controls` files were copied into service and personal profiles with prior files backed up at `poc/data/operator/backup-usage-staging-20261008`. The gateway service restarted and returned health HTTP 200. A disposable no-model probe against the actual pinned Hermes auxiliary relay verified the new signer and channel origin. The live gateway has none of the quota policy, ledger, signing-key, or proxy-route environment settings, so the new checks are inert and numeric limits remain off. The local fake-provider and Slack tests do not substitute for a live limit refusal after cutover.

**Staged quota follow-up, 2026-10-08:** On the owner's request to finish auxiliary attribution and Slack limit replies, `pilot-usage/admission.py` now signs every pinned-Hermes auxiliary relay attempt (sync, async, retry, and streaming) using its verified parent session; it refuses a wrong provider route or unattributed origin before its provider callback. `pilot-slack-controls/usage_limits.py` checks available monthly allowance before a human Slack turn and prepares one explanatory reply in the original thread or DM. The provider proxy still makes the atomic decision; a concurrent race becomes a typed 429 usage-limit error. The gateway launcher checks every profile's exact local proxy route and performs a signed no-model startup probe whenever quota configuration is present. DM allowances are separate from the organization/channel allowance, following Claude Tag's billing split. Focused signer, Slack-reply, proxy, quota, and launcher tests pass. **Numeric limits are still unset and no live request is routed through the proxy.** A live limit refusal and actual auxiliary model call through the proxy await operator-chosen limits and cutover. See [07](07-phase2-boundaries.md).

**Latest recovery and usage work, 2026-10-08 ~15:40 KST:** The live Slack broker-outage check passed: with the broker paused, a human direct mention in `#pilot-agent-test` got exactly one retry notice in its thread, and the gateway created no model session for it. The broker was restored and passed health. After a gateway restart, the owner replied without a mention in an existing Pilot thread; Hermes reused the same saved session and correctly recalled that Respond automatically was Off. `pilot-slack-controls/recovery.py` performs an authenticated broker preflight only for admitted human turns, deduplicates Slack's paired event deliveries, and acknowledges the event. A real host logout/reboot and a live background-process interruption remain to test later. For usage, the owner chose **build the gate now; choose limits later**. `poc/usage/model_proxy.py` and `origin.py` stage a loopback provider boundary with signed service/channel/DM origin, atomic monthly token reservations, streaming usage settlement, and default denial. Five loopback tests using a local fake provider and five quota ledger tests pass. **The proxy is not on the live model route; numeric caps remain off.** Hermes' auxiliary client still needs trusted per-call origin propagation before cutover, along with a user-facing limit response and real cron reconciliation. See [07](07-phase2-boundaries.md).

Original snapshot as of **2026-10-07 17:50 KST**, last commit `d0ac525` on local `main`. Read this first, then `docs/02-poc-plan.md` (checklist), `docs/04-isolation-and-memory.md`, [05-routine-scoping.md](05-routine-scoping.md), and [06-personal-dm-profiles.md](06-personal-dm-profiles.md) (latest change). A later handoff commit is `c0cd468`; see the top updates and current Git history for subsequent work.

**Update 2026-10-08:** `channel-watch` ran successfully on October 7 at 18:00 and posted at 18:01. Routine origin, read-only channel/DM memory, and scoped `routines_list`/`cronjob` management are implemented and verified through Hermes' registry and provider; see [05](05-routine-scoping.md). The two active public jobs are bound to the test channel; the paused Outlook briefing remains unscoped. The first digest's false follow-up claims led to a prompt correction. The next scheduled fire still needs review. At 09:28 KST on October 8, the user clicked a 👎 button and confirmed the mute worked; gateway logs and `muted.json` confirm the same event.

**Profile update, 2026-10-08:** Claude Tag's DM versus channel access model and Hermes v2026.9.24 profile/secret routing were checked before changes. The owner's exact one-to-one DM now routes to an empty-created personal Hermes profile; channels and group DMs remain on `default`. Seven historical DM sessions, 32 messages and one note moved; legacy default DM routes were removed. Gateway selector, personal plugin/memory scope, read-only Slack tool, API key separation, and a human inbound DM/reply passed. Outlook remains disabled. The two profiles still share a container and bind mount, so this is not a hard security boundary. Full detail and setup commands: [06](06-personal-dm-profiles.md).

**Live DM correction:** The user's first post-change DM at 09:49 KST reached Slack but Hermes rejected the sender before routing: `SLACK_ALLOWED_USERS` existed only in Docker's process environment, while multiplex authorization reads the default profile's `.env`. `scripts/sync-default-profile-env.py` now mirrors the receiving bot's credentials and allowlist there, and `init.sh` runs it. The second DM at 09:51 KST succeeded: a new personal-profile session, scoped note load, reply, and completed delivery were verified. The default DB retained zero DM sessions.

**Boundary and limits research, 2026-10-08:** The user reported the revised `!status` and `!routines` Slack UI checks look fine. Official Claude Tag documentation says an idle thread's sandbox is released a few minutes after its turn finishes, while the Slack thread/transcript/memory survive and a reply rebuilds a clean sandbox. Files left only in the old sandbox do not survive. Dots documents a persistent cloud computer and pause/wake behavior, but no per-Slack-thread sandbox lifetime. Hermes v2026.9.24 has a session-scoped Docker mode and five-minute idle reaper, but this gateway has no Docker daemon access and its default worker forwarding/mounts require careful review. A test-only host broker and Hermes terminal/file adapter now run isolated, disposable workers; integration checks passed for unprivileged execution, per-session scratch, timeout/output teardown, and release/rebuild. The broker automatically selects and probes a local container route; only macOS/Colima was live tested. Windows and Linux branches remain to verify on those systems. That was the pre-pilot state; the live gateway now uses `pilot_sandbox`, while no usage cap is enabled. [07](07-phase2-boundaries.md) and [08](08-portability-and-locale.md) record the boundaries and portability requirements.

**Portability update, 2026-10-08:** The user requires Pilot to adapt to macOS, Windows, and Linux hosts and to multilingual users worldwide. Official references: Claude Tag executes remotely and uses a sender's Slack timezone when a routine has none; Dots executes on its cloud computer and separately connects one local computer. Pilot's test broker now selects a host interface, verifies container reachability, uses Windows-compatible pipe handling, and removes workers on shutdown. Windows/Linux branches remain untested on native hosts. Timezone setup has a Python entry point, and Slack locale is now preserved even if a sender has no timezone. That locale change and the revised language instructions were loaded into the live gateway with one restart; `/health` returned 200. The sandbox was enabled later in the isolated pilot below. See [08](08-portability-and-locale.md).

**Execution audit, 2026-10-08:** The user deferred native Windows/Linux testing until the full stress pass. A read-only audit of the running pinned Hermes tool catalog found that `browser_exec` launches Python from the gateway, and remote `execute_code` can RPC to gateway web tools; its empty-intersection fallback exposes all seven RPC stubs. The default catalog also contains gateway-managed skill/project/delegation and process routes. Terminal/file isolation by itself is therefore insufficient for a live boundary claim. The exact route matrix and first cutover gate are in [07](07-phase2-boundaries.md). At the time of this audit no live sandbox switch or usage cap was made; the last-24-hour ledger still has only three API calls and no real Slack/cron usage sample.

**Candidate cutover policy, 2026-10-08:** `config/boundary-tool-policy.yaml` plus `scripts/check-boundary-tools.py` now measure the tool names for Slack, cron, and API in pinned Hermes, including the two late-added scoped memory tools. Candidate checks passed for both service and personal profiles (16/15/0 tools); the API tool surface is zero only after marking known plugin toolsets, because Hermes auto-enables new plugin toolsets otherwise. `sandbox/egress.py` and the broker now offer credential-free, exact-host HTTPS fetch with a default-empty allowlist; the then-disabled `pilot-sandbox` plugin had a `web_fetch` tool for that route. Tests cover default denial, SSRF-resistant DNS pinning, redirects, broker authentication, and multilingual URLs. This was the candidate stage; the policy and fetch tool were enabled in the isolated pilot below. Credential injection, browser, and shell networking remain outside the pilot scope; real Slack/cron worker identity and failure UX still need live checks. See [07](07-phase2-boundaries.md).

**Broker smoke, 2026-10-08:** The temporary host broker's container reachability probe passed; authenticated health returned 200 and an HTTPS fetch under the empty policy returned 403. The broker was stopped and left no labeled worker. The cutover launcher preserves Hermes' supervised entrypoint and checks both profiles before starting; the local Docker CLI has no Compose subcommand, so the override has only passed YAML parsing. This was the pre-pilot broker smoke; the gateway was switched later as described below.

**Background worker check, 2026-10-08:** Pinned Hermes polls remote background jobs every two seconds via the environment, refreshing broker idle time. `tests/pilot-sandbox/live_background.py` passed against Docker: detached command, poll, release, empty rebuilt worker. No labeled worker remained. A stopped gateway/poller still eventually loses that process; live Slack interruption behavior remains to verify.

**Isolated pilot, 2026-10-08 ~13:56 KST:** After official Claude Tag/Dots comparison and a Hermes route audit, the user approved a pilot with existing public search and exact public GitHub hosts. The staged broker uses fixed keyless Parallel search plus credential-free HTTPS fetch; public search and GitHub API smokes passed, and an unlisted host was denied. Pinned-Hermes file-tool smokes found and fixed the image's `/opt/data` write-root mismatch and a broker stdin/heredoc collision; multilingual file write/read and cross-session denial now pass. The `pilot-routines` hook blocks per-job tool overrides, scripts, monitors, custom workdirs, and skill loading; the startup checker rejects active jobs with those routes or no Slack origin. The gateway was switched with `scripts/pilot-boundary.py apply`, retaining the old container as `pilot-agent-rollback` and byte-for-byte config backups. Both local health endpoints return 200; active service and personal tool audits resolve to 17 Slack, 16 cron, and zero API tools. A direct test inside the new gateway confirmed broker-backed file isolation and public search. At cutover the human two-thread/DM Slack test was pending; it passed later as recorded below. The broker is a detached host user process without boot persistence, and no usage cap is enforced yet. [07](07-phase2-boundaries.md) has the pilot details and rollback path.

**Pilot follow-up, 2026-10-08:** Gateway, dashboard, and broker loopback health probes returned 200 from a host-permitted shell; a restricted shell's socket permission error was not a service failure. The usage ledger still contains only three API calls. The offline quota ledger now preserves the reservation through retries and prevents a final charge above it; five focused tests pass. At that point, a Mac user LaunchAgent was only rendered; it was installed later as described below. Colima already has a started login service. No numeric cap or live provider proxy is enabled.

**Human isolation check and broker correction, 2026-10-08 ~14:23 KST:** The user sent separate channel top-level requests and a one-to-one DM. The first thread wrote and reread a harmless `/workspace` file; the second thread and personal DM each called `read_file` in their own worker and got not found. Distinct Hermes session IDs hashed to the three observed worker names, and the usage ledger charged channel calls to the service profile and DM calls to the personal profile. Inspection then found that the pinned Hermes image declares an anonymous writable `/opt/data` volume in each worker. The broker now overrides it with bounded tmpfs and calls `docker rm -f -v` on cleanup. A disposable mount probe and 20 broker tests passed; the host broker was restarted without restarting Slack. Its orphan cleanup removed the three finished workers and the identified DM volume. A new broker-created worker had zero Docker volumes and only three bounded tmpfs paths, and its release left no worker. See [07](07-phase2-boundaries.md).

**Broker recovery, 2026-10-08:** Docker event metadata identified six additional dangling anonymous `/opt/data` volumes from Pilot-labeled disposable workers; only those six were removed. A repeat event/volume audit found zero confirmed Pilot dangling volumes. The first Mac LaunchAgent failed because macOS denied its process access to the Documents checkout. A private runtime with only broker code, the approved egress policy, and a byte-matched token was staged in `~/Library/Application Support/PilotAgent/broker`; the user LaunchAgent now points there and is running. A worker create/release through that launchd-managed broker passed with zero Docker volumes, no network, and UID/GID 65532. A controlled launchd restart recovered broker health while gateway health stayed ready. An actual logout/reboot, native Windows/Linux service startup, and visible Slack broker-failure UX remain untested. Re-stage the runtime and restart its LaunchAgent after changing broker code, policy, or token. See [07](07-phase2-boundaries.md).

**Channel reply gate, 2026-10-08:** The user reported that an unmentioned top-level “hey how are you guys?” in `#pilot-agent-test` got a reply. The service session `20261008_142513_72fba9b7` confirms a model turn and response. Official Claude Tag docs say its per-channel `Respond automatically` setting is on by default; Dots documents Slack DMs and mentions in threads. The user wants this pilot channel quiet unless mentioned or continuing a Pilot thread. `SLACK_FREE_RESPONSE_CHANNELS` is now empty in the source and live env files, and the live config adds the test channel to `platforms.slack.extra.require_mention_channels` because the existing Docker container retains its old environment until recreation. Hermes' empty `free_response_channels: []` setting did **not** override that old env, so it was removed. The fixed-command middleware now honors the per-channel mention override. After restarting the gateway, both health endpoints returned 200; pinned-Hermes routing checks rejected the exact untagged greeting, accepted a mention, and accepted an untagged reply in a bot-joined thread. The command checks pass 37/37. The user later sent a fresh unmentioned top-level Slack post and reported that Pilot stayed silent; the service session database shows no new model turn after the 15:12 KST setting change.

**Channel setting update, 2026-10-08:** On the user's request to match Claude Tag's `Respond automatically` option, `pilot-slack-controls` now stores a per-workspace/channel on/off setting. Newly joined channels default on; `#pilot-agent-test` was explicitly seeded off and retains its live Hermes require-mention override. The private `@Pilot Agent !configure` card opens a Slack modal with On/Off and Save; `channel_response_setting` gives the model a scoped path for multilingual requests to read/change the current channel's setting. The middleware uses pinned Hermes' `_hermes_force_process` only for unmentioned human top-level channel posts when on, and suppresses them before Hermes when off. Existing joined threads and DMs are unaffected. The SOUL rule now says to ignore casual chatter in auto mode, and a one-turn `pre_llm_call` note identifies admitted unmentioned top-level posts without changing saved user text. Claude Tag's unpublished high-volume stop threshold is not copied. The versioned boundary policy and both live profiles admit the one new Slack-only tool; service and personal preflights passed (18 Slack/16 cron/0 API), 40 fixed-command checks and focused persistence/access/routing checks passed, and both health endpoints returned 200 after restart. A private backup of the prior live configs/plugin files is under `data/operator/backup-channel-response/`. The user then confirmed that the private Configure form saved On and Off in Slack; the final setting file is mode `0600`, Off, and gateway logs show no form error. The user then requested On and Off in natural language, and the service session tool results show `channel_response_setting` changing the value to `true` and then `false`. The persisted setting ended Off. The user then sent a fresh unmentioned top-level Slack post and reported that Pilot stayed silent; the service session database shows no new model turn after the 15:12 KST setting change. This verifies the pilot channel’s Off path end to end.

---

## 1. The task
Build a **"Pilot Agent"**: an autonomous, always-on (24/7) agent like **OpenAI Dots** and **Anthropic Claude Tag**. It lives in Slack, works on schedules without being asked, asks before risky actions, and should later be offered inside the Pilot service (Phase 3). The company uses Outlook for mail; calendars and other apps are personal choice; staff use both macOS and Windows.

The current deliverable is a **PoC**. Phases (see `docs/02-poc-plan.md`):
- **Phase 0, bring-up:** done.
- **Phase 1, Dots/Tag behaviours:** mostly done.
- **Phase 2, per-user isolation and sandboxing:** memory and personal DM profile boundaries are running; a disposable worker per session is in a live Mac pilot, with the remaining checks below.
- **Phase 3, Pilot integration:** not started.

## 2. Working rules from the user (follow strictly)
1. **Match Claude Tag and Dots in every detail.** Where they differ, **follow Claude Tag.** Before designing any behaviour, read the relevant Claude Tag docs (index: `https://claude.com/docs/llms.txt`, pages under `https://claude.com/docs/claude-tag/…`) and Dots docs (`https://learn.chatgpt.com/docs/dots`). Don't invent a mechanism when Claude Tag documents one. Note any deliberate deviation, such as a Hermes limitation, in the docs.
   - Example of getting it wrong: I first built 👎 feedback as an emoji-reaction trigger. Claude Tag ignores emoji reactions; its 👎 is a *button* that mutes the thread. It had to be rebuilt.
   - Second example: I set a fixed server timezone. Claude Tag uses the *sender's Slack profile* timezone.
2. **Verify, never assume.** Check Hermes source/docs and test live before concluding. Unit tests alone aren't enough: drive the real path, either the Hermes registry or hooks inside the container, or Slack.
3. **No Claude attribution** in commits or PRs: no `Co-Authored-By` and no "Generated with" lines. This is a CEO rule for all Pilot repos.
4. **Commit only when the user asks.** They usually do ask at the end of each step. Nothing has ever been pushed (see §11).
5. **Never print secrets.** Check `.env` values by length and prefix only. Before every commit, scan the staged diff for `xox[bp]-`, `xapp-`, `API_SERVER_KEY=`, `BASIC_AUTH_PASSWORD=`.
6. **Respond to the user in English.** Korean from coworkers is context, not a language switch. The user writes **daily reports in Korean** with a `[Pilot Agent]` section of short bullets.
7. **Keep research lean:** 5–8 sources, primary sources first.
8. **Prompts must be language-agnostic principles,** never hardcoded example phrases or keyword matching.
9. **Never click approval buttons on the user's behalf.**

## 3. Environment
| Item | Value |
|---|---|
| Repo | `~/hermes-agent-project` (remote `enayuso/hermes-agent-project`) |
| Host | macOS (Apple Silicon), Docker via **Colima**. There is **no `docker compose` plugin**, so use the `docker run` in the README |
| Shell | **zsh**: never use `path` as a variable (it clobbers `$PATH`), and a command stored in a variable doesn't word-split (use a function) |
| Agent runtime | `nousresearch/hermes-agent:v2026.9.24` (Hermes 0.21.5), container `pilot-agent`, data bind-mounted `poc/data → /opt/data` (`HERMES_HOME=/opt/data`) |
| Ports (host loopback only) | `127.0.0.1:8642` OpenAI-compatible API · `127.0.0.1:9119` dashboard (basic auth, credentials in `.env`) |
| Model (current) | self-hosted vLLM `Inferact/Qwen3.8-Flash-Next-NVFP4` at `http://203.0.113.20:11434/v1`, 262,144 context. **No auth, public IP** (raise separately). The server went down once on 2026-10-07 |
| Fallback model | Furiosa NPU `furiosa-ai/gpt-oss-20b` at `http://203.0.113.30:11551/v1`, 131,072 context. A commented block in `config.yaml`. **Weak:** failed about half of Slack reads and gave wrong refusal reasons; the user rejected it |
| Slack | Workspace **Example** (`T0AAAAAAAAA`), bot `@pilot_agent`, Socket Mode. Test channel `#pilot-agent-test` = `C0AAAAAAAAA` (public). The owner's DM with the bot = `D0BBBBBBBBB`. Owner Slack user is the first ID in `SLACK_ALLOWED_USERS` |
| Hermes source for reading | Shallow clone at commit `13dc3a73`, in an old session scratchpad that may be gone. Re-clone: `git clone --depth 1 https://github.com/NousResearch/hermes-agent` |

`.env` keys (names only): `API_SERVER_KEY, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_USERS, SLACK_HOME_CHANNEL, SLACK_FREE_RESPONSE_CHANNELS, HERMES_UID, HERMES_GID, HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET}, AGENT_TIMEZONE (auto), TZ, HERMES_TIMEZONE`.

## 4. Repo layout
```
docs/01-research.md            how Claude/OpenAI/xAI built agents; OpenClaw vs Hermes vs NemoClaw; why Hermes
docs/02-poc-plan.md            phase checklist + caveats (keep updated)
docs/03-phase1-dots-tag.md     Dots/Claude Tag feature mapping, gap check, feedback/mute spec
docs/04-isolation-and-memory.md  memory per place + isolation design, Hermes internals, Slack test results
docs/05-routine-scoping.md     routine origins, channel memory, management scope, remaining limits
docs/06-personal-dm-profiles.md personal DM profile routing and migration
docs/07-phase2-boundaries.md  execution isolation findings and usage-meter plan
docs/08-portability-and-locale.md  macOS/Windows/Linux runtime routing and per-sender language/timezone behavior
docs/HANDOFF.md                this file
poc/docker-compose.yml         (reference; compose plugin not installed locally)
poc/config/config.yaml         Hermes profile config (repo source of truth; init.sh seeds data/config.yaml once)
poc/config/pilot-rules.yaml   Allow/Ask/Block rules (seeded once into data/, hot-reloaded there)
poc/config/SOUL.md             persona (always re-copied to data/ by init.sh)
poc/config/slack-manifest.json Slack app manifest (user pastes it into the Slack app; reinstall if asked)
poc/plugins/pilot-*           our Hermes plugins (rsynced into data/plugins by init.sh)
poc/tests/pilot-*             unit tests (rsynced into data/tests) — NEVER put tests inside plugin folders (§8)
poc/scripts/init.py, detect-timezone.py  portable core setup and timezone detection; .sh wrappers for POSIX
poc/scripts/*.sh               smoke test, behaviour tests, routines, outlook login (POSIX operator helpers)
poc/scripts/report-usage.py    read-only lifetime token/call report by place (no dollar estimate)
poc/scripts/report-request-usage.py  read-only 24-hour/all-time audited request totals
poc/data/  poc/.env            live state + secrets (gitignored)
```
**Two copies of the config:** `poc/config/*` is versioned, and `poc/data/*` is what runs. `config.yaml` and `pilot-rules.yaml` are only *seeded* once. When you change them, edit **both** (the live copy in `data/` and the repo copy), then restart. `SOUL.md`, plugins and tests are re-synced by `init.sh` (`rsync -a --delete plugins/ data/plugins/`, `rsync … tests/ data/tests/`, `cp config/SOUL.md data/SOUL.md`).

## 5. How to run, restart and test
```bash
cd poc
python3 scripts/init.py      # seeds data/, generates API key + dashboard login, detects timezone
# (Re)create the container — required after any .env change (docker restart does NOT reload --env-file):
docker rm -f pilot-agent; docker run -d --name pilot-agent --restart unless-stopped \
  -v "$PWD/data:/opt/data" -p 127.0.0.1:8642:8642 -p 127.0.0.1:9119:9119 --env-file .env \
  -e API_SERVER_ENABLED=true -e API_SERVER_HOST=0.0.0.0 -e HERMES_DASHBOARD=1 \
  nousresearch/hermes-agent:v2026.9.24 gateway run
docker restart pilot-agent  # enough after config.yaml / plugin / SOUL changes (wait ~35-40 s)
./scripts/smoke-test.sh      # health + one tool-using turn
./scripts/behaviour-tests.sh # 7 API-driven behaviour checks (~3 min); re-run after any model change
for t in pilot-rules/test_rules pilot-slack-read/test_slack_read pilot-slack-controls/test_controls pilot-slack-controls/test_commands pilot-routines/test_scope pilot-memory/test_scoped_memory; do
  docker exec pilot-agent /opt/hermes/.venv/bin/python /opt/data/tests/$t.py | tail -1; done
python3 tests/test_report_usage.py
python3 tests/test_report_request_usage.py
python3 tests/test_detect_timezone.py
python3 tests/test_init.py
python3 -m unittest discover -s tests/pilot-sandbox -p 'test_*.py'
python3 tests/pilot-usage/test_audit.py
python3 scripts/report-usage.py  # read-only lifetime usage by profile/place
python3 scripts/report-request-usage.py  # audited physical calls in the last 24 hours
# Restricted sandbox prototype, separate from the live gateway:
python3 -m unittest discover -s tests/pilot-sandbox -p 'test_*.py'
# In a foreground host terminal: bash scripts/start-sandbox-broker.sh
# With that broker running: python3 tests/pilot-sandbox/live_smoke.py
# Stop the foreground broker after the test; it does not change config.yaml.
```
- Hermes CLI inside the container: `docker exec -u hermes pilot-agent /opt/hermes/.venv/bin/hermes <cmd>` (`cron list|runs|resume|pause|create`, `plugins list`, `tools list --platform slack`, `mcp list`, `doctor`).
- **Logs:** `poc/data/logs/agent.log` (tool calls: `tool X completed` / `Tool X returned error`), `gateway.log` (inbound messages, Slack connection), `errors.log`, and `logs/gateways/default/current` plus `gateway-exit-diag.log` for crashes. Container clock and logs use the agent timezone (KST). Lines before about 13:50 on 2026-10-07 are in UTC.
- **Session history:** `poc/data/state.db` (SQLite): `sessions` (`id, source, chat_id, chat_type, thread_id, user_id, session_key, title…`) and `messages` (`session_id, role, content`). API sessions store an **empty** `system_prompt`, so you can't inspect the prompt there; use logs.
- **Driving plugins without the model:** inside the container, load plugins and call through the registry with a simulated Slack context. `behaviour-tests.sh` test 2 shows the pattern:
  ```python
  from hermes_cli.plugins import get_plugin_manager; pm=get_plugin_manager(); pm.discover_and_load()
  from tools.registry import registry
  from gateway.session_context import set_session_vars
  set_session_vars(platform="slack", chat_id="C0AAAAAAAAA", chat_type="group", user_id="<owner id>")
  registry.dispatch("slack_read_channel", {"limit": 5}, task_id="t", session_id="s")
  # rule decisions: from hermes_cli.plugins import _get_pre_tool_call_directive_details
  # hooks: from hermes_cli.lifecycle import invoke_hook; invoke_hook("pre_llm_call", ...)
  ```

## 6. Architecture (what runs)
Hermes Agent in one container with `default` plus the owner's personal DM profile. The single Slack bot uses Socket Mode; the personal profile's Slack adapter is disabled. Our behaviour lives in **6 plugins + config + persona**:

| Plugin | Purpose | Key mechanism |
|---|---|---|
| `pilot-rules` | Dots-style **Allow / Ask / Block** on every tool call. Rules in `pilot-rules.yaml`, hot-reloaded, precedence block > allow > ask > default | `pre_tool_call` hook returning `block` or `approve`. `approve` goes through Hermes' approval gate: Slack buttons; fails closed; unattended (cron/API) → denied |
| `pilot-slack-read` | Tools `slack_read_channel`, `slack_read_thread`, `slack_list_pins`, `slack_list_channels`. **Claude Tag access rule:** the current conversation is always readable; elsewhere only public, joined, non-Slack-Connect channels; cron/API → public only. Also a `pre_llm_call` hook adding a `[Sender context]` line (sender's Slack timezone, local time, language/locale) | Current channel via internal `gateway.session_context.get_session_env` (guarded import, fails closed). Tool handlers only receive `task_id`/`session_id` |
| `pilot-slack-controls` | Claude Tag **👎 button mutes the thread** (`:mute:` notice), `!mute` / `!unmute`, `!status`, `!feedback`, `!routines`, and per-channel Respond automatically | Global slack_bolt **middleware** via `ctx.register_platform_handler("slack", …)` plus the Slack-only `channel_response_setting` tool. Hermes registers its own `hermes_feedback` listener first and bolt is first-match. Muted state, channel setting, and submitted feedback are stored under `data/plugin-data/pilot-slack-controls/` |
| `pilot-routines` | List and manage routines only in their originating place | `routines_list` and `pre_tool_call` restrictions on Hermes' profile-wide cron tools; see [05](05-routine-scoping.md) |
| `pilot-memory` | **Memory provider** (`memory.provider: pilot-memory`), Claude Tag memory per place: notes per channel / group DM / DM; workspace notes saved only from public channels and read in all channels; scheduled runs read-only; app API has its own notes. Tools `memory_notes`, `past_sessions` (only this place's sessions, via `sessions.chat_id`) | `initialize()` receives the gateway identity (platform, chat_id, chat_type, thread_id, user_id, agent_context). Slack `conversations.info` decides public/private; unknown → private. Notes in `data/pilot-memory/{channel,dm,group,workspace,app}/…json` |
| `pilot-usage` | Private, per-physical-request token/call audit, with verified channel/DM/cron place labels and no conversation text | Hermes `post_api_request` and `post_auxiliary_call` hooks; SQLite under each profile's `plugin-data/pilot-usage/`. Read with `scripts/report-request-usage.py` |

Config highlights (`poc/config/config.yaml`):
- `model`: custom vLLM provider.
- `gateway.multiplex_profiles: true`; personal DM `profile_routes` are generated in the live config by `scripts/provision-personal-dms.py` after the user's exact DM chat ID is verified. The reusable template has an empty list.
- `terminal.backend: local`: runs inside the container.
- `approvals.mode: manual`, `cron_mode: deny`, `unattended_mode: deny`.
- `plugins.enabled`: the three general plugins. The memory provider is selected separately.
- MCP `outlook` (`@softeria/ms-365-mcp-server@0.159.1 --org-mode --preset outlook`) with **`enabled: false`**: halted by the user.
- `group_sessions_per_user: false`: anyone can steer a thread, as in Claude Tag.
- `slack.require_mention: true` and `ignore_other_user_mentions: true`. `pilot-slack-controls` opens the mention gate for channels whose saved Respond automatically setting is on, while keeping the test channel off. Empty `SLACK_FREE_RESPONSE_CHANNELS` is the new environment default; the live test channel also has a `require_mention_channels` override for its stale container env. Joined threads continue either way. Fixed commands at channel top level require an @mention.
- `platforms.slack.extra.rich_blocks` / `feedback_buttons: true`.
- `memory: provider pilot-memory, memory_enabled: false, user_profile_enabled: false`.
- `agent.disabled_toolsets: [session_search]`.
- The checked-in and live configs currently set `tools.tool_search.enabled: "off"`; reconcile the older model-comparison notes before changing it.
- **Timezone isn't in config.yaml:** `scripts/detect-timezone.sh` writes `TZ` and `HERMES_TIMEZONE` to `.env` from the owner's Slack profile (`AGENT_TIMEZONE` override, else host, else UTC). Currently `Asia/Seoul`.

`pilot-rules.yaml`:
- `ask`: `skill_manage` (Claude Tag reviews skill changes), `mcp_outlook_*`.
- `allow`: Outlook read-only and draft tools.
- `block`: `session_search`, Outlook delete / mail-rule / calendar-permission / logout.
- 96 live Outlook tools are classified, so new connector tools ask by default.

Scheduled routines (`scripts/setup-routines.sh`, idempotent by name):

| Job | ID | Schedule (agent tz) | State |
|---|---|---|---|
| `morning-briefing` | `0faba019e5a5` | weekdays 08:30 | **paused**; needs Outlook |
| `weekly-agent-landscape` | `997b894a3cca` | Mon 09:00 | active, first run 2026-10-12 |
| `channel-watch` | `0324229cf761` | weekdays 18:00 | active; first run completed and delivered 2026-10-07 18:01; digest prompt corrected after review |

## 7. What is verified
- **Phase 0, live in Slack:**
  - DM and channel chat work.
  - The approval buttons work: Deny blocked a command, Approve ran it.
  - A scheduled post was delivered.
  - Memory persisted across a restart (on the old built-in memory).
- **Rules:** 30/30 unit tests, and Hermes' own resolver returns block/approve as expected.
- **Slack read:** 34 unit tests. Live through the registry: current channel/thread/pins/channel list OK, a DM read from a cron context is denied, public channel from cron is allowed.
- **Sender timezone/locale hook:** live output checked for the owner's real profile.
- **Feedback/mute:** 19 unit tests. The middleware is wired (`Wired native handlers from plugin 'pilot-slack-controls'`). The user updated the manifest. A live 👎 button click was confirmed by the user, gateway log and persisted muted state on 2026-10-08 09:28 KST.
- **Fixed Slack commands:** 36 command checks pass. Live `!routines` posted one scoped list with two jobs and no model turn at 10:19 KST. The user's 10:25 KST screenshots showed private `!status` and `!feedback` cards and the old raw-cron `!routines` copy. A live feedback form submission at 10:40 KST was saved in a private mode-`0600` file without a gateway error. At 10:53 KST, one `!feedback` produced two private cards and its confirmation appeared separately. The command path now deduplicates Slack event IDs in a private SQLite ledger, and the feedback button accepts Slack's `/actions/` response URL for replacing the original card. At 10:56 KST, the user confirmed that the card changed into the confirmation; one new report was saved without a gateway error.
- **Memory isolation:** 24 unit tests, plus a **live Slack test by the user**:
  - All 5 cases resolved to the right place.
  - A leak search over every reply and tool result found no DM→channel or channel→DM leakage.
  - `past_sessions` returned only the channel's sessions.
  - Details are in `docs/04`.
- **Behaviour suite:** 7/7 on Qwen. Qwen once replied in Chinese to an English prompt (intermittent).
- **Dashboard:** without login, protected APIs return 401; the generated login returns 200 and shows cron jobs.
- **Routine scope (after original handoff):** two active public jobs now have the test channel as origin; Hermes registry lists those two there and zero in the owner's DM, blocks global list/cross-place access, and the memory provider loads the channel's note read-only for both. See [05](05-routine-scoping.md).
- **Usage audit:** the enabled `pilot-usage` plugin recorded a live API turn's main and title-generation calls separately; both matched Hermes' `session_model_usage` rows. Temporary Hermes hook checks attributed a service channel, personal DM, and scoped cron job without touching live Slack or schedules. The 24-hour report runs read-only. Real Slack and scheduled calls remain to be reconciled; see [07](07-phase2-boundaries.md).

## 8. Hermes gotchas found (read before changing anything)
1. **The memory-provider loader imports every `.py` in the provider folder.** A test file there ran `sys.exit()` and stopped the gateway, so tests live in `poc/tests/`.
2. Built-in memory (`MEMORY.md`/`USER.md`) is injected into **every** session, and `session_search` is **unscoped**. Both are replaced or disabled for isolation. The `memory` toolset must stay enabled, or Hermes hides provider tools and the provider's prompt block.
3. **Plugin tools sit behind tool search,** and there's no per-tool "always visible" option. The model must use `tool_search`/`tool_call`. The persona names the Slack tools.
4. Plugin tool handlers get no chat context. The only accessor is internal `gateway.session_context`. Re-check it on Hermes upgrades.
5. **Plugin Slack action handlers are wired after Hermes' own** (the docs claim otherwise), and bolt is first-match. Use middleware.
6. **Feedback buttons need `rich_blocks`.** Hermes only logs clicks.
7. **`gateway.profile_routes`** (per-channel/user profiles, the strongest isolation) is read **only at gateway start**, and `hermes profile create --clone` **copies memory files**.
8. Hermes' system prompt tells the model to run `date` for the time, which is why the container `TZ` matters. Cron has **no per-job timezone**.
9. A silence token (`[SILENT]`) answering a person (reactions, commands) makes Hermes post a warning instead.
10. `hermes tools disable` writes a full per-platform allowlist (about 200 lines). Prefer `agent.disabled_toolsets`.
11. `docker restart` doesn't reload `--env-file`; recreate the container. The macOS bind mount forces SQLite out of WAL (harmless warning).
12. Docker's `--env-file` keeps trailing comments as part of the value: keep comments on their own lines.
13. Slack's 50-slash-command cap means `/fork`, `/status` and `/heartbeat` are reached as `/hermes <cmd>`.
14. Slack: internal apps keep Tier 3 history limits. A **commercially distributed non-Marketplace app gets 1 request/min and 15 messages** on history/replies, which matters for productizing. Bot workspace search needs an `action_token` from the event, which plugins can't reach.

## 9. Open items (original list; see update above)
1. **Check the next `channel-watch` fire with scoped memory.** The first run was checked and its prompt corrected; the live 👎 button → mute test passed on 2026-10-08.
2. **Check the next scheduled fire with channel memory and usage.** Registry, provider, and temporary audit-hook checks pass, but the next real cron execution has not yet run with the new origin metadata and per-request ledger. The existing 18:10 KST heartbeat will inspect the 18:00 `channel-watch` fire, compare the audit with `session_model_usage` and cron's `usage_audit.jsonl`, and update [05](05-routine-scoping.md) and [07](07-phase2-boundaries.md).
3. **`!routines` live verified; finish isolation.** The first live bare command reached Hermes as a model turn because the parser required a bot mention, although Hermes treats the test channel as free response. The second bypassed the model and listed both jobs, but posted duplicates because stopped Slack Bolt middleware returned no acknowledgment. The plugin now returns HTTP 200: the third live command at 10:19 KST posted one list of both jobs and no model turn, with no retry after the wait window. `routines_list` and the `cronjob` hook scope normal management. That was the pre-pilot state; the isolated pilot now routes terminal/files through disposable workers. See [05](05-routine-scoping.md).
4. **Per-person DM profiles:** implemented and live-tested for the owner; repeat provisioning for new allowed users. Personal connector consent and channel result review are still pending. See [06](06-personal-dm-profiles.md).
5. **Outlook** (halted by the user): `enabled: false` in config. To resume: `enabled: true`, then `./scripts/outlook-login.sh` (device code; the tenant may need admin consent for Softeria's app; prefer our own Azure app registration for production), then resume `morning-briefing`.
6. **Command UI verification:** screenshots confirm `!status` and the initial `!feedback` button are private in Slack. The editable form and saved submission were checked live. The first card-replacement attempt rejected Slack's real `/actions/` response URL and a later `!feedback` produced duplicate private cards. Both paths were corrected; at 10:56 KST the user confirmed that the original private card changed into its confirmation. `chat.postEphemeral` remains a fallback if replacement fails. The user later reported the revised `!status` and `!routines` visuals looked fine.
7. **Language drift:** Qwen intermittently answers in Chinese. Persona and locale hints exist; keep testing (behaviour test 7, and wordless turns).
8. **Phase 2:** Claude Tag-style durable session plus disposable sandbox per thread, separated control-plane identities, an egress/credential boundary, and usage limits. The Mac Slack pilot now runs a host broker plus `pilot-sandbox` Hermes adapter with one no-network, unprivileged worker per session. The user approved public search and exact GitHub hosts. Fixed-destination keyless Parallel search and credential-free HTTPS fetch are live, limited to `github.com`, `api.github.com`, and `raw.githubusercontent.com`; an allowed search and GitHub API request succeeded, and an unlisted destination returned 403. The active tool policy passed both service and personal-profile checks in pinned Hermes (17 Slack, 16 cron, zero API tools). It disables gateway-side browser Python, built-in web, code execution, project/delegation, and unreviewed plugins. **Still required:** real cron reconciliation, actual host reboot and live background interruption, and complete usage admission with auxiliary-call attribution and operator-chosen limits. Broker failure UX and gateway-restart thread continuity passed in the later checks at the top of this handoff. The human two-thread/DM worker identity test passed at 14:21–14:23 KST. The old container and config backups are retained for rollback. Scoped `memory_notes` writes now reuse Hermes' strict memory scanner; the container test and direct provider-path rejection check passed.
   - `scripts/report-usage.py` gives a read-only lifetime token/call breakdown by place. `pilot-usage` and `scripts/report-request-usage.py` now give a windowed physical-request audit, including auxiliary calls; reconcile its next real Slack/cron entries and measure capacity before limits. The live model rows have unknown dollar cost, so no spending threshold was guessed. Hermes' Docker backend supports session-scoped containers with a default five-minute idle reaper, but the current gateway has no Docker socket; unrestricted socket access would defeat the boundary. Research and implementation order: [07](07-phase2-boundaries.md).
9. **Decisions pending with the user:**
   - voice-note speech-to-text: the image lacks `faster-whisper` and on-demand installs are disabled; options are a cloud key, a custom image, or a Pilot Whisper service;
   - Microsoft Teams channel: needs an Azure Bot registration;
   - webhooks: need a public URL;
   - the Slack app icon;
   - prompt size: about 17.4k tokens per call on Qwen. The user said to **leave it**, since a stronger model is coming.
10. **Phase 3:** Pilot "Agent" tab via each agent's OpenAI-compatible API (`/v1/chat/completions`, `/v1/runs`, `/api/jobs`), and the decision between downloadable/local and hosted.

## 10. Research context (short)
- **Claude Tag:**
  - always-on Slack agent with its own service accounts and Agent Proxy credential injection;
  - per-channel memory plus workspace notes;
  - routines;
  - ambient "Respond automatically";
  - one session per thread;
  - 👎 button mutes;
  - `!mute` / `!unmute` / `!status` / `!feedback` / `!routines`.
- **Dots** (2026-09-29): its own cloud computer, Allow/Ask/Block custom rules, owner-only by default, ChatGPT/Slack/Teams.
- **Grok Bot:** cloud PC per agent, early beta.
- **Why Hermes:**
  - OpenClaw has a poor security record and is single-tenant.
  - NemoClaw is a sandbox wrapper at v0.0.x.
  - Hermes is MIT-licensed, has Slack/Teams, cron, approvals, an OpenAI-compatible API, memory providers and `profile_routes`.

  Hermes and OpenClaw are both **single-tenant**, so isolation is our job. That's why `pilot-memory` exists, and why per-person profiles are planned.

## 11. Git
- Historical note: at the time of `c0cd468`, routine changes were uncommitted and the remote could not be reached. Later commits supersede that state; check `git status` and `git log` before editing.
- Commit style: a short imperative subject plus bullet body, **no co-author lines**. Run the secret scan before every commit (§2.5).
