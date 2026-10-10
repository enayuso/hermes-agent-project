# 03 — Phase 1: following OpenAI Dots and Claude Tag

Researched 2026-10-07 from first-party docs: OpenAI's [Meet dots](https://learn.chatgpt.com/docs/dots) and Anthropic's [Claude Tag docs](https://claude.com/docs/claude-tag/overview) (public beta). Hermes capabilities were checked in its source at commit `13dc3a73` (v2026.9.24 image).

## How Dots works
- **A goal, not a prompt.** You give a dot an ongoing goal; it tracks progress between conversations, "decides when to pause and wake up to continue", and comes back with results or with decisions that need you.
- **Its own cloud computer and browser**, working while your devices are off. You can *Take over* the browser and *Return control*. Optional access to one personal computer through the ChatGPT desktop app (off by default).
- **Rules: Allow / Ask / Block.** Allowed by default: read-only research, connected plugins within their existing permissions, notes, background agents. Ask: actions affecting your accounts, sharing across channels, website sign-ins (credentials typed in a private form). Block: built-in safeguards (e.g. passwords, moving money).
- **Memory**: the conversation, ChatGPT memories and its own saved notes; persists across channels and devices.
- **Channels**: ChatGPT (desktop/web/mobile), Slack and Teams (DM or @mention; answers whoever mentioned it by default).
- **Visibility**: *Activity* tab (tasks, results, input requests) and *Scheduled* tab (recurring work), with pause/stop per task.
- **Connectors**: 4,000+ apps through plugins (Gmail, Drive, GitHub, …).

## How Claude Tag works
- **One agent identity for the team.** In channels it acts through its own service accounts set by an admin, not as the person asking; actions show up under those accounts in each tool's audit log. One-to-one DMs use the person's own connectors instead.
- **Thread = session.** Each Slack thread binds to one persistent session that anyone in the channel can steer by replying. The work runs in an ephemeral cloud sandbox per thread (released when idle, rebuilt on the next reply). A live checklist shows progress; a **Stop** button halts it.
- **Ambient mode ("Respond automatically", on by default per channel).** It reads every message; usually does nothing; replies unprompted only when it already has a one-message answer or someone states a need it can meet; hands new info to a running session. Stops reading a channel after high volume or a week of inactivity.
- **Routines**: scheduled or run-once jobs set up in plain language ("every weekday at 9am…"), channel watches, PR subscriptions. They run with the channel's access, post only into that channel, and keep running if their creator leaves.
- **Memory by channel**, plus workspace notes shared from public channels. Curated notes, not transcripts. Anyone in the channel can read/correct it; admins can edit it.
- **Agent Proxy**: credentials never enter the model or sandbox; they are injected at the network boundary for admin-allowed hosts only (default deny).
- **Governance**: bundles of connectors per channel, org and per-channel spend limits, admin activity page, channel managers.

## Mapping onto the Pilot Agent (Hermes)

| Behaviour | Dots / Tag | Pilot Agent PoC | Status |
|---|---|---|---|
| Allow / Ask / Block rules | Dots Custom Rules | `pilot-rules` plugin on Hermes' `pre_tool_call` hook; `ask` goes through Hermes' approval gate (Slack buttons, fail-closed) | ✅ Built, 28/28 rule tests, verified through Hermes' own resolver |
| Unattended runs never act for the user | Dots "Ask" never auto-runs | `approvals.cron_mode: deny` + `unattended_mode: deny` | ✅ Configured |
| Outlook mail + calendar | Dots plugins / Tag connectors | `ms-365-mcp-server@0.159.1`, `--org-mode --preset outlook` (96 tools) | ⏸ Configured, **halted** (`enabled: false`) until the sign-in is approved |
| Routines | Tag routines / Dots Scheduled | Hermes cron: `morning-briefing` (weekdays 08:30 KST), `weekly-agent-landscape` (Mon 09:00) | ✅ Created, paused until the model is back |
| Ongoing goals / wake-ups | Dots goals | Hermes `/goal` (judge-driven continuation) and `/heartbeat` (idle-only wake-ups in a thread) | Built in; to test |
| Channel reply gate | Tag's per-channel "Respond automatically" is on by default and can be changed in Slack or Configure | `pilot-slack-controls` stores one setting per channel; new channels start on, the pilot channel remains off; `@Pilot Agent !configure` opens a private switch and an in-channel model tool handles multilingual requests. Joined threads continue either way | ✅ Live Configure On/Off and natural-language setting changes passed; pilot channel ended Off |
| Shared thread steering | Tag thread = shared session | `group_sessions_per_user: false` | ✅ Configured — to test |
| Stay out of side conversations | Tag ambient rules | `slack.ignore_other_user_mentions: true` | ✅ Configured |
| Persona / operating principles | — | `config/SOUL.md` (Pilot identity, ask-first, quiet-unless-useful, report format) | ✅ |
| Timezone | Tag: UTC, user names TZ | `timezone: Asia/Seoul` | ✅ verified (+09:00) |
| Prompt size with many tools | — | Hermes Tool Search defers MCP tools by default | Built in; measure latency later |
| Website sign-ins without exposing passwords | Dots private sign-in form | Hermes credential vault (masked prompt, injected into the page) | Built in; not set up |
| Per-channel instructions | Tag channel instructions | `slack.channel_prompts` | Available; not used yet |
| Activity / audit view | Dots Activity tab, Tag Activity page | Hermes logs + dashboard (`HERMES_DASHBOARD=1`) | Not set up |

### Gaps vs Dots / Claude Tag
- **Memory is per agent, not per channel.** Hermes keeps one memory per profile. Fine for a personal agent (Dots model); a team agent in many channels (Tag model) would need per-channel scoping. Phase 2's one-container-per-user covers the Dots case.
- **No Agent Proxy.** The Outlook token cache lives in the agent's data dir (encrypted file, key beside it). The model never sees it, but the agent's container can read it. Credential injection at a network boundary is a Phase 2 item.
- **Identity.** Outlook is connected as the signed-in person (Dots model), not a service account (Tag model). For a personal agent that is the right choice.
- **No spend limits** yet (a self-hosted model, so cost is GPU time, not money).
- **No "take over the browser"** handoff.

## Platforms
The agent runs server-side in its container (hosted on a Mac for this PoC), so people reach it the same way from any device: through Slack (desktop, web, mobile). The Outlook connector also runs server-side, so nothing is installed on anyone's machine. Only relevant later:
- Dots-style "access my computer" would need a desktop client; Hermes' desktop app has macOS and Windows installers and can connect to a remote gateway (Phase 3 decision).
- The PoC's helper scripts are bash and are run on macOS.

## Outlook: why ms-365-mcp-server
- Microsoft's own Outlook MCP servers (Work IQ Mail/Calendar) require a Microsoft 365 Copilot license and are in preview.
- Softeria's server is MIT, ~1k stars, actively released (0.159.1), covers mail, calendar and contacts with an app-scoped `outlook` preset, and needs no Copilot license.
- Delegated permissions requested (`--org-mode --preset outlook`): `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Calendars.Read.Shared`, `Contacts.ReadWrite`, `MailboxSettings.ReadWrite`, `User.Read`.
- **Tenant consent**: sign-in uses Softeria's built-in app registration. If the organization's Microsoft 365 tenant blocks users from consenting to third-party apps, an admin must approve it once, or we register our own Azure app (`MS365_MCP_CLIENT_ID`). For production we should use our own app registration either way.

## Full gap check (2026-10-07)

Every Dots / Claude Tag feature from their docs, checked against Hermes' docs and source. "Built in" means documented or present in Hermes v2026.9.24 but **not yet turned on or tested** in the PoC.

### A. Built into Hermes, not turned on yet (config or setup only)
| Feature (source) | Hermes equivalent |
|---|---|
| Live progress checklist in the thread (Tag) | Slack progress cards (`display.platforms.slack.tool_progress`), in threads only |
| Stop / restart / fork a thread (Tag `!restart`, Stop button) | `/stop`, `/restart`, `/new`, `/fork`, `/branch`, `/status` slash commands |
| Event triggers, e.g. "when a bug report is posted" (Dots) | Webhook platform (`messaging/webhooks.md`) + cron `--monitor-script` |
| Split work across background agents (Dots) | `delegate_task` subagents and the Kanban board |
| Activity / Scheduled views (Dots tabs, Tag Activity page) | Web dashboard (`HERMES_DASHBOARD=1`, needs an auth provider off-loopback) |
| Website sign-ins without exposing passwords (Dots) | Credential vault (masked prompt, injected into the page) |
| Take over the cloud browser (Dots) | Browser VNC live view (`features/browser.md`) |
| Voice conversation (Dots voice calls) | Voice mode / wake word on desktop; Slack voice notes via speech-to-text |
| Microsoft Teams as a channel (Dots) | Teams platform plugin (needs a Bot Framework registration) |
| Per-channel instructions (Tag) | `slack.channel_prompts` |
| Network egress allowlist (Tag Agent Proxy default-deny) | Docker network isolation with an egress proxy (`egress/network-isolation.md`) |
| Self-improving skills (Tag skills repo) | Hermes creates and refines its own skills locally (no PR-to-repo flow) |

### B. Real gaps (need building)
| Feature (source) | Gap in the PoC |
|---|---|
| Searches the whole workspace (Tag baseline) | Channel history, threads and pins are now built (see below). Search is not: Slack's `assistant.search.context` needs an `action_token` from the triggering message event, which plugins can't reach, plus new search scopes — needs a change in Hermes' Slack adapter. |
| Channel memory + workspace notes (Tag) | One memory per agent. |
| Memory you can view and correct (Tag; Dots can't) | Only by asking the agent; no admin view. |
| Credentials injected outside the agent — Agent Proxy (Tag) | Tokens live in the agent's data dir. |
| Own service-account identity per tool (Tag) | Connects as the signed-in person (fine for a personal agent). |
| Spend / usage limits per org and channel (Tag) | None (a self-hosted GPU, so this is fairness, not money). |
| One fresh sandbox per thread (Tag) | One long-lived container for everything. |
| Per-channel model choice, fast mode (Tag) | One model for the whole agent. |
| Proactive "noticing" across many channels (Tag watches; Dots monitors) | Ambient mode only sees new messages in listed channels; no scheduled channel watch yet (can be a routine once the Slack-read tool exists). |
| Name, avatar and handle per agent (Dots) | Fixed in the Slack app manifest. |
| Feedback (Tag thumbs-down) | None. |
| Access to the user's own computer (Dots, optional) | Out of scope for now (Phase 3: Hermes desktop can connect to a remote gateway). |

### C. Not needed for this PoC
- Tag's GitHub/GitLab app identity, on-call setup, healthcare/BAA controls, Enterprise Grid, cost attribution API.
- Dots' Codex / ChatGPT Work hand-offs (Pilot's own code mode would be the equivalent).

## Built: `pilot-slack-read` plugin (2026-10-07)
Closes the biggest gap above: Hermes' Slack adapter only showed the agent the current thread.

- **Tools** (read-only): `slack_read_channel` (recent top-level messages, optional `since_hours`), `slack_read_thread`, `slack_list_pins`, `slack_list_channels`.
- **Access rule** (mirrors Claude Tag's cross-channel table): the conversation the agent was asked in is always readable; any other conversation only if it is a public channel the bot is a member of and not shared with another organization. Private channels, DMs and group DMs are never readable from elsewhere. Scheduled jobs and API calls have no current conversation (Hermes clears it for cron), so they can read public channels only. Anything unverifiable is denied.
- **Output**: oldest first, mentions resolved to names, entities decoded, text capped at 2,000 chars per message and 200 messages per call, with a note that message text is data, not instructions.
- **Current-conversation lookup** uses `gateway.session_context.get_session_env`, the accessor Hermes' own tools use. It is internal (not in Hermes' documented plugin contract), so the import is guarded: if it ever breaks, the plugin falls back to "no current conversation" (public channels only) and logs a warning. Re-check on every Hermes upgrade.
- **Verified**: 28/28 unit tests on a fake client, and live through Hermes' tool registry against the test workspace (no model): current channel, thread, channel list and own-DM reads succeed; a scheduled job reading the DM is denied; a scheduled job reading the public test channel is allowed.
- **Pins need `pins:read`**, added to `poc/config/slack-manifest.json`; the installed Slack app must be updated and reinstalled before `slack_list_pins` works (until then it returns a clear `missing_scope` message).

### Slack rate limits — matters for a product
Internal, customer-built apps (this PoC) keep Slack's normal Tier 3 limits (50+ requests/min) for `conversations.history` / `conversations.replies`. A **commercially distributed app that isn't in the Slack Marketplace is limited to 1 request/minute and 15 messages per request** (Slack, 2025-05-29; existing installs since 2026-03-03). If Pilot Agent is installed into customers' workspaces, it needs Marketplace approval or each customer installs their own internal app. Source: [Slack changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps).

## Gap list pass 2 (2026-10-07): what was turned on

| Item | Status |
|---|---|
| Activity / Scheduled views (Dots) | ✅ **Dashboard on** at `http://127.0.0.1:9119` (host loopback only), basic-auth login generated into `.env` by `init.sh`. Verified: no login → 401 on sessions/cron/env/logs; generated login → 200 and the scheduled jobs list. `/api/status` stays public by design (health). |
| Stop / restart / branch | ✅ Native Slack slash commands `/stop`, `/restart`, `/new`, `/branch`, `/goal`, `/usage`. Slack caps an app at 50 commands, so `/fork`, `/status`, `/heartbeat` are reached as `/hermes fork` etc. |
| Live progress checklist | On by default (Slack progress cards in threads; app uses Slack's Agent view with `assistant:write`). Verify when the model is back. |
| Feedback (Tag 👎) | **Replaced 2026-10-07 to match Claude Tag exactly** — see "Feedback and muting" below. (The first version routed 👎 emoji reactions to the agent; Claude Tag ignores emoji reactions, and the reaction turns caused a Chinese reply and a silence-marker warning on removal.) |
| Channel watch (Tag) | ✅ `channel-watch` routine (weekdays 18:00 KST, paused): reads every readable public channel's last 24h via `pilot-slack-read`, posts only what needs a person. |
| Background agents (Dots) | Built in and enabled (`delegation` toolset). Kanban board left off. |
| Self-improving skills (Tag skills repo) | Built in and on: the agent saves skills after novel tasks; the curator archives unused ones weekly. No PR-to-repo review flow. |
| Web research, browser | Working without API keys (`hermes doctor`: web search/extract, Playwright Chromium). |
| Removed | The Phase 0 one-off `poc-cron-test` job. |

### Needs a decision
| Item | Why it isn't on |
|---|---|
| **Voice notes in Slack** | The Docker image ships without local speech-to-text (`faster-whisper`) and disables on-demand installs. Options: a cloud key (Groq / OpenAI — audio leaves the company), a custom image with `faster-whisper`, or a Pilot-hosted Whisper endpoint. |
| **Microsoft Teams** | Needs an Azure Bot Framework registration (client ID/secret, tenant). The company uses Microsoft 365 for mail, so this may be wanted. |
| **Event triggers (webhooks)** | Hermes' webhook platform needs an inbound HTTPS URL; the PoC runs on a laptop. Possible on a server, or via cron `--monitor-script` polling in the meantime. |
| **Take over the browser** | Browser VNC live view exists; needs a persistent browser setup. Low value until the agent does website work. |
| **App name / avatar** | Name is "Pilot Agent" from the manifest; the icon is uploaded in the Slack app settings (Basic Information → Display Information). |

### Phase 2 (architecture, not switches)
Per-channel memory (memory provider plugin), Agent Proxy-style credential injection and an egress allowlist (Hermes' Docker network isolation with an egress proxy), per-user/per-thread sandboxes (orchestrator), usage limits (orchestrator), per-channel model choice.

## Feedback and muting — Claude Tag's behaviour (2026-10-07)
Source: [Claude Tag commands](https://claude.com/docs/claude-tag/users/commands) ("Thumbs-down button and muting", "Mute or unmute a thread"). Dots documents no reaction or feedback behaviour, so Claude Tag's is followed.

| Claude Tag | Pilot Agent |
|---|---|
| Some replies carry 👍 / 👎 **buttons** | Hermes' Slack-native feedback buttons on final replies (`platforms.slack.extra.feedback_buttons`, needs `rich_blocks`) |
| 👎 button mutes that thread and posts a notice starting `:mute: Claude is muted in this thread` | `pilot-slack-controls` plugin: 👎 click → thread muted (persisted), notice `:mute: Pilot Agent is muted in this thread…` |
| A 👎 **emoji reaction** does nothing | Reaction triggers removed; `reaction_added` / `reaction_removed` events and `reactions:read` dropped from the manifest |
| `@Claude !mute` / `!unmute` per thread; at top level a hint that muting is per thread | Same commands and hint (no mention needed in a DM) |
| An @-mention with a request unmutes the thread and is answered | Same |
| Other threads, top level, routine posts unaffected | Same (only human replies in the muted thread are dropped) |

How: Hermes only logs feedback clicks, and registers its `hermes_feedback` handler before plugin handlers (slack_bolt runs the first matching listener only), so the plugin adds a global slack_bolt middleware via the documented `ctx.register_platform_handler("slack", …)`. Consumed events explicitly return Slack Bolt HTTP 200 so Slack does not retry them. `!status` now reads the live Hermes run table and returns an ephemeral note without starting a model turn; it does not include conversation contents. `!feedback` sends a private button that opens an editable Slack form. Submitted reports are saved in a private local JSONL file for the Pilot Agent team; no remote feedback service exists yet. This two-step form is necessary because Slack message events have no modal trigger ID. `!routines` lists local scheduled jobs as described in [05](05-routine-scoping.md). The command behaviors follow [Claude Tag's command reference](https://claude.com/docs/claude-tag/users/commands); the modal and ephemeral delivery use [Slack's documented APIs](https://docs.slack.dev/surfaces/modals/) and [chat.postEphemeral](https://docs.slack.dev/reference/methods/chat.postEphemeral/). `!routines` is live-verified in the test channel with one reply and no model turn. The user's screenshots verified the private `!status` note and `!feedback` button; a later live form submission was saved successfully.

The user's 2026-10-08 screenshots confirmed that `!status` and `!feedback` render privately and `!routines` renders in a thread. Copy was then revised to match the documented presentation: the status note names the exact channel/thread/DM and gives the mute command hint in threads; the feedback card explains which IDs accompany a report; and the routine list replaces simple raw cron strings with readable local schedules plus the next UTC time. Claude Tag publishes a sample status note but no exact routine-list wording. [Dots' task guide](https://learn.chatgpt.com/docs/dots/tasks-and-memory) describes conversational task review and a Scheduled view, not these `!` commands. The user later reported that the revised `!status` and `!routines` Slack checks looked fine; this is user-reported visual verification.

At 10:40 KST on 2026-10-08, the user submitted a report through the Slack feedback form. One new record appeared in the private feedback JSONL file (mode `0600`) with the submission time and expected user/channel metadata; the gateway logged no feedback error. The private confirmation message has not been independently observed, because Slack history does not expose ephemeral messages.

After the user confirmed Slack showed the private “Feedback sent” note as a separate message, the submission flow was changed to use the button interaction's `response_url` with `replace_original: true`. Slack documents that this can update the source ephemeral message, so the feedback card itself becomes the confirmation while staying private. If that URL is unavailable or the update fails, the existing `chat.postEphemeral` confirmation remains. [Slack message updates](https://docs.slack.dev/messaging/modifying-messages/) and [interaction responses](https://docs.slack.dev/interactivity/handling-user-interaction/).

The 10:53 KST screenshots exposed two issues in that change: one `!feedback` produced two private cards, and submission still produced a separate confirmation. The `response_url` validator accepted Slack's `/services/` URL but rejected the `/actions/` URL shown in Slack's [block action example](https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/); it now accepts both. Slack [retries Events API deliveries](https://docs.slack.dev/apis/events-api/) if the acknowledgment is late, which is consistent with the duplicate cards. Commands now claim the Slack `event_id` once in a private SQLite ledger before posting, including across gateway restarts. This suppresses retries without suppressing a new command; 36 local checks pass. The exact cause of the earlier duplicate event was not logged.

Live recheck at 10:56 KST: the user reported that the feedback card itself changed into the “Feedback sent” confirmation. One new report was saved (three total) and one command event was claimed in the new private ledger; both files have mode `0600`. No feedback error appeared in the gateway log. The user did not report another duplicate card.

Live check 2026-10-08 09:28 KST: the user clicked a 👎 button and confirmed it worked. The gateway logged the `pilot-slack-controls` mute notice, and `muted.json` persisted one muted thread at the same time.

### Respond automatically

[Claude Tag's response guide](https://claude.com/docs/claude-tag/users/when-claude-responds) says this setting is per channel and on by default. A channel member can change it by asking Claude in that channel or using Configure; turning it off stops unprompted top-level replies, while threads Claude has joined continue. It also documents a quiet default even when the switch is on: casual chatter is usually left alone, a one-message answer is given only when the channel context supplies it, and larger unprompted work requires a standing instruction. Pilot now uses a persisted channel setting in `pilot-slack-controls`, a private `@Pilot Agent !configure` Slack form, and `channel_response_setting` for multilingual in-channel requests. The pilot channel starts off to preserve the user's preference; other newly joined channels start on. When on, the middleware marks eligible unmentioned human top-level events with Hermes v2026.9.24's `_hermes_force_process`, while Hermes still applies its channel allowlist and authorization checks. A one-turn `pre_llm_call` note identifies only an admitted unmentioned top-level post, so the model can distinguish it from a direct @mention and leave casual chatter unanswered without altering Slack text or the saved transcript. This model-based triage is not verified to match Claude Tag's classifier; Claude Tag's unpublished high-volume stop threshold is also not copied. Bot posts, group DMs, direct messages, and existing threads bypass the channel switch. The one-setting-per-channel state lives in `data/plugin-data/pilot-slack-controls/channel-response.json` (mode `0600`). Both active profile tool audits passed with 18 Slack, 16 cron, and zero API tools; source checks passed for persistence, scope, mentions, and modal card output. The user opened the private Configure card in Slack, saved On and then Off, and confirmed both worked; the persisted setting is Off, with no filtered form error in gateway logs. The user then requested On and Off in natural language; two service sessions recorded `channel_response_setting` tool results changing the value to `true` and then `false`. The persisted setting ended Off, with no filtered gateway error. The user then sent a fresh unmentioned top-level Slack post and reported that Pilot stayed silent. The service session database shows no new model turn after the 15:12 KST setting change, verifying the pilot channel’s Off path end to end.
