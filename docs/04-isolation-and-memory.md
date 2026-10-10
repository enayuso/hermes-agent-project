# 04 — Isolation between people and places, and memory per place

Built 2026-10-07. Rule for this project: match Claude Tag and OpenAI Dots in detail; where they differ, follow Claude Tag.

## What Claude Tag and Dots do
**Claude Tag** ([memory](https://claude.com/docs/claude-tag/users/memory), [how it works](https://claude.com/docs/claude-tag/concepts/how-it-works), [agent identity](https://claude.com/docs/claude-tag/concepts/agent-identity), [personal connectors](https://claude.com/docs/claude-tag/concepts/personal-connectors)):
- **Memory follows places.** Each channel, public or private, has its own notes. **Workspace notes** (org-wide conventions) are saved only from public channels and read in every channel; a private channel reads them but saves only to its own notes. A group DM reads and saves only its own notes. Each one-to-one DM keeps separate notes, outside all of this.
- Memory is curated notes, not transcripts. "Remember for this channel: …" saves; "what do you remember about this channel?" lists; anyone in the channel can correct or remove entries; admins can view/edit the memory files.
- The agent can look back at **earlier sessions in the same channel** (list + read transcripts), not full-text search across everything.
- **Access follows places too.** A channel uses only the access applied to it. A DM runs on the person's own account and connectors. Using someone's personal connector in a channel needs that person's approval (Allow / Allow with review / Don't allow), and sensitive results are held for their review before posting.
- Skill improvements go through human review (pull requests on a skills repository).

**Dots** ([Meet dots](https://learn.chatgpt.com/docs/dots)) is a personal agent: it answers its owner by default, and checks with the owner before sharing anything from a private conversation with other people. Messaging access never grants app access.

## What Hermes does out of the box (checked in source, v2026.9.24 / `13dc3a73`)
| Mechanism | Behaviour | Problem for a shared agent |
|---|---|---|
| Built-in memory (`MEMORY.md`, `USER.md`) | One store per profile, injected into **every** session's system prompt at session start (`agent/agent_init.py`, `tools/memory_tool.py`) | A fact from one person's DM is in the prompt of every channel conversation |
| `session_search` | Full-text search over **every** session in `state.db`; no filter by chat or person (`tools/session_search_tool.py`) | The agent answered a channel question by searching other conversations (Phase 1 test) |
| Skills (`skill_manage`, curator) | Agent-written skills shared by every session | Details from one place can be distilled into a skill used elsewhere |
| Cron jobs (`cronjob` tool) | One job list per profile | Any session can list every job's prompt |
| Terminal / files | One working folder per profile | Files written in one place are readable from another |
| **Memory providers** (`agent/memory_provider.py`) | One external provider per profile, *alongside* built-in memory. `initialize()` receives the session's gateway identity: `platform`, `chat_id`, `chat_type`, `thread_id`, `user_id`, `agent_context` (`cron` for scheduler runs). Provides tools + a system-prompt block (frozen per session) + per-turn `prefetch` | — this is the extension point used |
| **Profiles + `gateway.profile_routes`** | One shared bot can route specific users / channels / threads to different profiles, each fully isolated (memory, sessions, skills, cron, credentials, logs). Routes are read **once at gateway start**; new profiles hot-add but new routes need a restart. `profile create --clone` **copies the source's memory files** | Strongest isolation, but a new channel would need provisioning + restart (Claude Tag works in a new channel immediately) |

Gotcha found while building: **Hermes' memory-provider loader imports every `.py` file in the provider's folder.** A unit test placed there ran at import and its `sys.exit()` stopped the gateway. All plugin tests now live in `poc/tests/`, never inside plugin folders.

## What was built
**`pilot-memory`** — a Hermes memory provider that replaces the built-in memory (`memory.provider: pilot-memory`, `memory_enabled: false`, `user_profile_enabled: false`):

| Place (from the session's gateway identity + Slack `conversations.info`) | Notes loaded | Can save to |
|---|---|---|
| Public channel | that channel's + workspace notes | the channel, or workspace notes |
| Private channel | that channel's + workspace notes (read-only) | the channel only |
| Slack Connect / unknown visibility | as private (fail closed) | the channel only |
| Group DM | its own | itself |
| One-to-one DM | its own (separate per DM) | itself |
| Pilot app API | its own ("app") | itself |
| Scheduled run with a verified Slack origin | its origin channel's notes, plus workspace notes if that place permits them | nothing (Hermes: cron never writes memory) |
| Scheduled run without a verified origin | no place's notes | nothing |

- Tools: `memory_notes` (list / add / replace / remove; `where` = here | workspace) and `past_sessions` (list / read earlier sessions **of this place only**, via `state.db.sessions.chat_id`).
- The prompt block tells the agent that nothing from other places is available and must not be guessed or repeated; the persona says the same.
- Notes: JSON per place under `data/pilot-memory/` (curated, 2,200 chars per place like Hermes' built-in), file-locked writes, author and time recorded. Additions and replacements pass through Hermes' own strict `_scan_memory_content` check before the file changes; an unavailable scanner fails closed.
- `session_search` is hidden from the model everywhere (`agent.disabled_toolsets`) and blocked in `pilot-rules.yaml` as a backstop; `skill_manage` → **ask** (Claude Tag reviews skill changes). The `memory` toolset must stay enabled: Hermes only exposes memory-provider tools when it is.
- The one existing built-in note came from the owner's DM; it was moved into that DM's notes and the old files archived under `data/backups/`.

**Verified:** the scoped-memory suite passes inside the Hermes container, including strict note scanning and origin-scoped scheduled reads; rules 30/30; live: provider activates per session and resolves the place (`api … → app`), the prompt block is assembled at session start, the agent used `memory_notes` and correctly saw no notes in the app place (the DM's note did not leak), and Hermes' own resolver returns block / approve for `session_search` / `skill_manage`. The standalone host test needs Hermes' `tools.memory_tool_store` import, so run this suite inside the container.

## Slack test (2026-10-07, owner in DM + #pilot-agent-test)
| Test | Place resolved | Result |
|---|---|---|
| DM: "What do you remember about me?" | `dm:D…` | Listed the DM's one note ✅ |
| Channel: "what do you know about my team?" | `channel:C…` (public) | No notes there; answered only from the channel's own history and pins; tried `session_search` → blocked (now hidden) ✅ |
| Channel: "remember for this channel: reports go out as tables" | `channel:C…` | Saved to the channel's notes ✅ |
| DM: "what are this channel's notes?" | `dm:D…` | Listed only the DM's note ✅ |
| Channel: "what did we discuss here earlier?" | `channel:C…` | `past_sessions` listed/read only this channel's sessions ✅ |

Leak search over every reply and tool result of those sessions: the DM's note never appears in a channel session, the channel's note never in a DM session.

## Not covered yet (and the plan)
| Gap | Claude Tag | Plan |
|---|---|---|
| Personal connectors (Outlook) per person | DM uses the person's own connectors; in channels, owner approval + result review | DM profile routing and old DM state migration are implemented ([06](06-personal-dm-profiles.md)); Outlook is still disabled, and channel use/review is not built |
| Cron job list visible across places | Routines are per channel | `pilot-routines` scopes the cronjob tool and adds `routines_list`; shared terminal/files still need profile and sandbox isolation ([05](05-routine-scoping.md)) |
| Shared working folder | Fresh sandbox per thread | Phase 2 (sandbox per session / profile) |
| Routines don't load their channel's notes | Routines run with the channel's memory | `pilot-memory` now resolves the stored job origin read-only; registry/provider path verified, next scheduled fire pending ([05](05-routine-scoping.md)) |
| Memory entries aren't scanned for prompt injection | — (Hermes scans built-in memory) | Closed: scoped note additions and replacements now reuse Hermes' strict scanner. Container tests and direct provider-path check passed; the injected note was rejected and no note was saved |
| No organization/channel spend caps | [Claude Tag has organization-wide and per-channel hard spend limits](https://support.claude.com/en/articles/15594475-what-is-claude-tag); DMs use the person's account | Hermes `agent.max_turns` is a per-turn iteration backstop, not a spend cap. Lifetime and per-request window reports now measure calls/tokens by place. Reconcile real Slack/cron usage and choose a policy before enforcing admission limits. Do not guess a monetary threshold or cut a running task mid-turn ([07](07-phase2-boundaries.md)). |
