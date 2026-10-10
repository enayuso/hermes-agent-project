# 06 — Personal DM profiles

Implemented 2026-10-08. This is the Hermes state and connector boundary for one-to-one Slack DMs. It is **not** a container security boundary.

## Verified product behavior

- [Claude Tag agent identity](https://claude.com/docs/claude-tag/concepts/agent-identity): a one-to-one DM uses that person's account and connectors; channel and group-DM work uses the agent's service access.
- [Claude Tag personal connectors in channels](https://claude.com/docs/claude-tag/concepts/personal-connectors): requester-owned connectors in a channel need the owner's Allow / Allow with review / Don't allow choice; sensitive results can require review before posting. Pilot has **not** built that channel approval and result-review flow, so personal Outlook remains disabled.
- [Slack conversations.open](https://docs.slack.dev/reference/methods/conversations.open/): one user ID returns the bot's one-to-one DM ID and can create the conversation without posting a message. Provisioning uses it only when Hermes has no recorded DM ID.

## Hermes behavior checked in v2026.9.24 source

`gateway.profile_routes` matches declared fields conjunctively. The route combines `platform: slack`, `user_id`, and the **D…** `chat_id`, so the same person's message in a channel or group DM stays in the default service profile. `gateway.multiplex_profiles: true` serves the default and named profiles in one gateway. An explicit route to an unserved profile is rejected. Routes are read on startup.

`hermes profile create --no-alias --no-skills` creates an empty profile. `--clone` would copy configuration, credentials, skills, and memory, so it is not used. Hermes gives each profile its own `state.db`, memory, cron, plugin manager, secrets, and working directory. A named profile's `.env` is read through `agent.secret_scope.get_secret`; in multiplex mode a missing personal secret does not fall back to the default profile's secret. Pilot's rules and Slack read plugins now use the profile home and secret scope accordingly.

## What is running

- Default profile: service identity for channels, group DMs, and existing public routines.
- `person-u0bgk9e8btn`: owner's one-to-one DM. Created empty, then given a personal config and workspace. Its Slack adapter and Outlook connector are disabled. The default Slack bot receives the DM and Hermes routes the turn to this profile.
- The personal profile has a **copy of the shared bot token only for Pilot's read-only Slack tools**. No second Socket Mode adapter starts. Its API key is absent; Hermes advertises a `/p/<profile>/v1` mirror, but both unauthenticated requests and the default profile's key return 401.
- The owner's 7 existing DM sessions (32 messages) and one note were moved to the personal profile with Hermes' `export_session_for_move`, `import_moved_session`, and `rekey_legacy_main_sessions` methods. The default DB now has zero DM sessions; the legacy `sessions/sessions.json` default DM routes were removed. SQLite snapshots are under `poc/data/backups/personal-dm-migration/`.

`poc/config/config.yaml` is the reusable template and has multiplexing enabled but no personal IDs. `poc/scripts/provision-personal-dms.py` generates the live `poc/data/config.yaml` routes from `SLACK_ALLOWED_USERS`, Hermes history, or Slack's DM API. The script is idempotent: subsequent runs preserve personal config, credentials, rules, and persona. It keeps the personal Slack and API adapters disabled. `poc/scripts/init.sh` syncs Pilot plugins into existing personal profiles.

The first live DM at 09:49 KST was rejected by Hermes' **receiving adapter**, before routing. Its multiplexed authorization check reads the default profile's `.env`, whereas Docker `--env-file` had supplied `SLACK_ALLOWED_USERS` only to the process environment. `poc/scripts/sync-default-profile-env.py` now copies the service bot credentials, owner allowlist, and related settings into the default profile's `.env` during `init.sh`, preserving Hermes-managed entries and printing no values. A direct secret-scope check confirms the allowlist is present in `default` and absent from the personal profile. The gateway was restarted after this fix.

For an existing default-profile DM, stop the supervised gateway before running `migrate-personal-dm-state.py`, then start it again. `hermes gateway stop` alone can be revived automatically by s6:

```bash
python3 poc/scripts/provision-personal-dms.py
docker exec pilot-agent /command/s6-svc -d /run/service/gateway-default
docker exec -u hermes pilot-agent /opt/hermes/.venv/bin/python \
  /opt/data/scripts/migrate-personal-dm-state.py PROFILE D_CHAT_ID U_USER_ID
docker exec pilot-agent /command/s6-svc -u /run/service/gateway-default
```

The migration script is copied to `poc/data/scripts/` before the container call. New users provisioned before their first DM have no old state to move. Replace `PROFILE`, `D_CHAT_ID`, and `U_USER_ID` with the exact route values output by provisioning.

## Verification and limits

- Hermes' actual gateway profile selector returned the personal profile for the owner's exact DM, and default for a channel, group DM, and another sender.
- The personal runtime resolved its own home, had the Slack read credential but no API key, loaded Pilot plugins, and returned the moved note and 7 sessions.
- A read-only Slack history call succeeded through Hermes' registry inside the personal profile. All five plugin test suites passed. The gateway restarted with one connected Slack adapter and two cron-ticked profiles; no old DM routing mirror was re-imported.
- **Human DM passed at 09:51 KST.** Hermes accepted the second message, created session `20261008_095123_e458270f` under `person-u0bgk9e8btn`, loaded the DM's one note, generated a reply, and recorded delivery as `delivered`. The default DB still has zero sessions for the DM; the personal DB now has eight. The first DM was rejected before processing and is not in the session history.
- Both profiles still share one container, process, and bind mount. A terminal command can inspect another profile's files and the old database snapshots. Claude Tag's per-thread sandbox, Agent Proxy credential injection, and default-deny egress are separate Phase 2 work. Do not claim these profiles are a hard tenant boundary or enable multi-person personal connectors yet.
- Personal connector use in channel threads and review of sensitive results are not built. Outlook stays disabled and the morning briefing stays paused.
