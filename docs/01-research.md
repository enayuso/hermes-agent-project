# 01 — Research: how the big labs built always-on agents, and which base to use

Researched 2026-10-07. Product details move fast — re-check before relying on them.

## How Claude, ChatGPT and Grok did it

All three converged on the same seven layers:

| Layer | Claude (Anthropic) | ChatGPT (OpenAI Dots) | Grok (xAI Grok Bot) |
|---|---|---|---|
| Agent loop | Claude Agent SDK (same loop as Claude Code) | GPT-6 Astra | Grok 4.x / Grok Build |
| Own compute | Routines / Managed Agents (cloud sandbox), or user's machine | "Own cloud computer" per Dot | Dedicated cloud PC per bot |
| Triggers | Cron, webhook, GitHub event, pushed channel events | Cron runner, proactive background research | Overnight jobs |
| Channels | Slack (Claude Tag), Telegram/Discord/iMessage (Channels), mobile (Remote Control) | ChatGPT, Slack, Teams, voice | Desktop, iOS, TUI, bot-to-bot groups |
| Connectors | MCP + plugins | 4,000+ apps via plugins | Signs into sites like a human + MCP |
| Memory | Per-channel, admin-editable (Tag) | Learns preferences | Learns from corrections |
| Guardrails | Permission modes, sender allowlists, remote approval relay, credential proxy, service-account identity | Rules for act-alone vs ask-first | Approval on some actions |

Key takeaways:
- **24/7 means hosted compute.** Every product marketed as 24/7 runs the agent on the vendor's servers; local variants only live while the user's machine is on.
- **The agent is layers, not one product** — loop + sandbox + triggers + channels + connectors + memory + permissions.
- **Guardrails are the differentiator.** Claude Tag's model (agent's own service accounts, credentials injected at the network edge, per-channel isolation and budgets) is the bar.

Notes per product:
- **Claude Tag** (beta 2026-06-23): always-on Slack agent, ambient mode, own service accounts, Agent Proxy for credentials, per-channel memory/tools/spend limits.
- **OpenAI Dots** (2026-09-29): own cloud VM per Dot (browser, terminal, files, memory, cron), ChatGPT/Slack/Teams, custom act-vs-ask rules. Pro / Business Premium.
- **Grok Bot** (early beta 2026-08-11): cloud PC per agent, human-style app sign-in, bundled into $120–$300/mo tiers; least mature.

## OpenClaw vs Hermes Agent vs NemoClaw

These are not three alternatives: OpenClaw and Hermes are agent runtimes; NemoClaw is NVIDIA's sandbox/security wrapper around them.

| | OpenClaw | Hermes Agent | NemoClaw |
|---|---|---|---|
| Role | Agent (personal assistant gateway) | Agent (self-improving autonomous agent) | Sandbox wrapper (OpenClaw / Hermes / Deep Agents) |
| License / steward | MIT, OpenClaw Foundation | MIT, Nous Research | Open source, NVIDIA |
| Version checked | v2026.9.8 | v2026.9.24 (0.21.5) | v0.0.128, config `v1alpha1` |
| Channels | Widest (Slack, Teams, Telegram, Discord, WhatsApp, Signal, iMessage, Google Chat, 20+) | ~20 incl. Slack, Teams, Telegram, Discord, WhatsApp, Signal, Email, Matrix | inherited |
| Autonomy | Cron (heartbeat merged into cron) | Memory-backed cron, delivery to any platform | — |
| Learning | Memory + ClawHub skills | Self-written, self-improving skills; 3-layer memory | — |
| Execution | On host by default | 7 backends: local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox | Docker/Podman sandbox, network policy |
| Security record | CVE-2026-25253 (critical gateway RCE); ClawHavoc: ~20% of ClawHub skills malicious | Approval gate, DM pairing, containers; dashboard auth made mandatory after June 2026 incident | No shell-command-level allow/deny (our July check) |
| Tenancy | Single trust boundary per gateway (docs) | "Single-tenant personal agent" (SECURITY.md §2) | — |

**Both agents are single-tenant**, so a service runs **one isolated instance per user** — the same shape as Dots' "own cloud computer".

## Decision: Hermes Agent for the PoC

Verified in Hermes source (commit `13dc3a73`, 2026-10-06):
- Slack + Teams platforms (`plugins/platforms/slack`, `teams`)
- In-chat exec approval with buttons, fail-closed on timeout (`gateway/platforms/base_exec_approval.py`)
- Built-in cron with delivery to any platform (`cron/`)
- Official multi-arch Docker image, s6-supervised gateway
- **OpenAI-compatible API server** on :8642 (`/v1/chat/completions`, `/v1/responses`, `/v1/runs`, `/api/jobs`) — the natural product integration point
- `custom` provider for any OpenAI-compatible `base_url` (our vLLM)

Why not OpenClaw: security history and the fragility we logged running it on Server 6; its channel breadth isn't needed for a PoC.
Why not NemoClaw (yet): not an agent, still v0.0.x; Hermes' container is enough isolation for a PoC.

## On "service version of OpenClaw" / workflow automation

A coworker framed this as "a service version of OpenClaw — an agent that works without being asked; bodiless robot intelligence, from an AI-workflow-automation angle". Accurate, with one nuance: classic workflow automation (n8n/Zapier) runs fixed, human-designed steps; an agent gets a goal and decides the steps. Shipped products combine both — a deterministic trigger (schedule/webhook) wakes an agent that plans the work. Pilot Agent follows the same pattern.

## Sources
- https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- https://code.claude.com/docs/en/channels
- https://www.techtimes.com/articles/318967/20260623/claude-tag-turns-slack-multiplayer-ai-anthropic-agent-writes-65-its-own-code.htm
- https://9to5google.com/2026/09/29/openai-dots-agent/
- https://gigazine.net/gsc_news/en/20260812-spacexai-grok-bot/
- https://github.com/openclaw/openclaw · https://docs.openclaw.ai/gateway/security
- https://www.bitdoze.com/openclaw-security-guide/
- https://github.com/NousResearch/hermes-agent
- https://docs.nvidia.com/nemoclaw/user-guide/openclaw/release-notes/2026/9/22
