"""Pilot Agent: Claude Tag's per-thread mute and 👎 feedback button behaviour on Slack.

Hermes renders Slack's native feedback buttons (``platforms.slack.extra.feedback_buttons``) but
only logs clicks, and registers its ``hermes_feedback`` handler before plugin action handlers
(slack_bolt runs only the first matching listener). So this plugin adds a global slack_bolt
middleware through the documented ``ctx.register_platform_handler("slack", ...)`` hook: it sees
every request before any listener, applies the mute rules in ``controls.py``, and calls
``next()`` unless the request is a mute command or a reply in a muted thread.
"""

from __future__ import annotations

import logging
import json
import asyncio
from pathlib import Path
from hermes_constants import get_hermes_home
from slack_bolt.response import BoltResponse

from .controls import MuteStore, decide
from .auto_response import (ChannelResponseStore, route_ambient, ambient_turn_context,
                            ambient_candidates)
from .commands import (CommandEventStore, handle_message, feedback_view, save_feedback,
                       valid_slack_response_url, configure_view, configure_card, _member)
from .settings import SCHEMA, tool_handler
from .recovery import broker_ready, recovery_target
from .usage_limits import limit_decision

logger = logging.getLogger(__name__)

_store = MuteStore(Path(get_hermes_home())
                   / "plugin-data" / "pilot-slack-controls" / "muted.json")
_events = CommandEventStore(Path(get_hermes_home())
                            / "plugin-data" / "pilot-slack-controls" / "command-events.db")
_response_store = ChannelResponseStore(Path(get_hermes_home())
                                       / "plugin-data" / "pilot-slack-controls" / "channel-response.json")


def _wire(app, adapter):
    @app.middleware
    async def pilot_thread_mute(body, client, next):
        try:
            if await handle_message(body or {}, adapter, client, _store, _events, _response_store):
                return BoltResponse(status=200, body="")
        except Exception as exc:  # fail closed for command events
            logger.warning("pilot-slack-controls: command handler failed (%s)", exc)
            return BoltResponse(status=200, body="")
        try:
            forward, posts = decide(body or {}, _store)
        except Exception as exc:  # noqa: BLE001 — never block Slack traffic on our bug
            logger.warning("pilot-slack-controls: decide failed (%s); forwarding", exc)
            return await next()
        for post in posts:
            try:
                await client.chat_postMessage(**post)
            except Exception as exc:  # noqa: BLE001
                logger.warning("pilot-slack-controls: notice failed: %s", exc)
        if posts:
            logger.info("pilot-slack-controls: %s", posts[0]["text"][:40])
        if forward:
            try:
                if not route_ambient(body or {}, adapter, _response_store):
                    return BoltResponse(status=200, body="")
            except Exception as exc:
                logger.warning("pilot-slack-controls: channel reply gate failed (%s)", type(exc).__name__)
                return BoltResponse(status=200, body="")
            event = (body or {}).get("event") or {}
            is_human_turn = ((body or {}).get("type") == "event_callback"
                             and event.get("type") in ("message", "app_mention")
                             and not event.get("subtype") and not event.get("bot_id")
                             and event.get("user") and event.get("channel") and event.get("ts"))
            if is_human_turn and not await asyncio.to_thread(broker_ready):
                target = await recovery_target(body or {}, adapter)
                # The health check precedes the model turn. A retry of this
                # Slack event must not post another outage notice.
                ambient_candidates.take(str((body or {}).get("team_id") or event.get("team") or ""),
                                        str(event.get("channel") or ""), str(event.get("ts") or ""))
                try:
                    fresh = _events.claim({"team_id": (body or {}).get("team_id"), "event": event})
                except Exception as exc:
                    logger.warning("pilot-slack-controls: recovery event ledger failed (%s)", type(exc).__name__)
                    fresh = False
                if fresh and target:
                    try:
                        await client.chat_postMessage(**target)
                    except Exception as exc:
                        logger.warning("pilot-slack-controls: recovery notice failed (%s)", type(exc).__name__)
                logger.warning("pilot-slack-controls: broker unavailable; refused Slack turn")
                return BoltResponse(status=200, body="")
            if is_human_turn:
                refuse, target = await limit_decision(body or {}, adapter)
                if refuse:
                    ambient_candidates.take(str((body or {}).get("team_id") or event.get("team") or ""),
                                            str(event.get("channel") or ""), str(event.get("ts") or ""))
                    try:
                        fresh = _events.claim({"team_id": (body or {}).get("team_id"), "event": event})
                    except Exception as exc:
                        logger.warning("pilot-slack-controls: quota event ledger failed (%s)", type(exc).__name__)
                        fresh = False
                    if fresh and target:
                        try:
                            await client.chat_postMessage(**target)
                        except Exception as exc:
                            logger.warning("pilot-slack-controls: quota notice failed (%s)", type(exc).__name__)
                    return BoltResponse(status=200, body="")
            return await next()
        return BoltResponse(status=200, body="")

    @app.action("pilot_feedback_open")
    async def pilot_feedback_open(ack, body, client):
        await ack()
        try:
            value = (body.get("actions") or [{}])[0].get("value") or "{}"
            metadata = json.loads(value)
            user = (body.get("user") or {}).get("id")
            channel = (body.get("channel") or {}).get("id")
            if user != metadata.get("user") or channel != metadata.get("channel"):
                return
            if adapter._early_reject_unauthorized(user, channel, channel.startswith("D")):
                return
            draft = metadata.pop("draft", "")
            response_url = body.get("response_url")
            if valid_slack_response_url(response_url):
                metadata["response_url"] = response_url
            await client.views_open(trigger_id=body["trigger_id"],
                                    view=feedback_view(draft, metadata))
        except Exception as exc:
            logger.warning("pilot-slack-controls: feedback form failed (%s)", exc)

    @app.view("pilot_feedback_submit")
    async def pilot_feedback_submit(ack, body, view, client):
        try:
            metadata = json.loads(view.get("private_metadata") or "{}")
            user = (body.get("user") or {}).get("id")
            channel = metadata.get("channel", "")
            if user != metadata.get("user") or not channel or adapter._early_reject_unauthorized(
                    user, channel, channel.startswith("D")):
                await ack(response_action="errors", errors={"feedback": "This form is no longer valid."})
                return
            report = (((view.get("state") or {}).get("values") or {}).get("feedback") or {}).get("report", {}).get("value", "")
            if not report or not report.strip():
                await ack(response_action="errors", errors={"feedback": "Please write a report."})
                return
            save_feedback(Path(get_hermes_home()) / "plugin-data" / "pilot-slack-controls" / "feedback.jsonl",
                          metadata, report.strip())
            await ack()
            confirmed = False
            response_url = metadata.get("response_url")
            if valid_slack_response_url(response_url):
                try:
                    from slack_sdk.webhook.async_client import AsyncWebhookClient
                    response = await AsyncWebhookClient(response_url).send_dict({
                        "replace_original": True,
                        "response_type": "ephemeral",
                        "text": "Feedback sent to the Pilot Agent team.",
                    })
                    confirmed = response.status_code == 200
                except Exception as exc:
                    logger.warning("pilot-slack-controls: feedback card update failed (%s)", type(exc).__name__)
            if not confirmed:
                await client.chat_postEphemeral(channel=channel, user=user,
                                                text="Feedback sent to the Pilot Agent team.",
                                                **({"thread_ts": metadata["thread"]} if metadata.get("thread") else {}))
        except Exception as exc:
            logger.warning("pilot-slack-controls: feedback submission failed (%s)", exc)
            try:
                await ack(response_action="errors", errors={"feedback": "Could not save feedback. Try again."})
            except Exception:
                pass

    @app.action("pilot_channel_config_open")
    async def pilot_channel_config_open(ack, body, client):
        await ack()
        try:
            metadata = json.loads((body.get("actions") or [{}])[0].get("value") or "{}")
            user = (body.get("user") or {}).get("id")
            channel = (body.get("channel") or {}).get("id")
            if (not user or user != metadata.get("user") or channel != metadata.get("channel")
                    or adapter._early_reject_unauthorized(user, channel, False)):
                return
            if valid_slack_response_url(body.get("response_url")):
                metadata["response_url"] = body["response_url"]
            await client.views_open(trigger_id=body["trigger_id"],
                                    view=configure_view(_response_store.enabled(metadata["team"], channel),
                                                        metadata))
        except Exception as exc:
            logger.warning("pilot-slack-controls: channel config form failed (%s)", type(exc).__name__)

    @app.view("pilot_channel_config_submit")
    async def pilot_channel_config_submit(ack, body, view, client):
        acknowledged = False
        metadata = {}
        try:
            metadata = json.loads(view.get("private_metadata") or "{}")
            channel, team = metadata.get("channel", ""), metadata.get("team", "")
            user = (body.get("user") or {}).get("id")
            choice = (((view.get("state") or {}).get("values") or {}).get("auto_response") or {}).get("choice", {}).get("selected_option", {}).get("value")
            if (not user or user != metadata.get("user") or choice not in ("on", "off")
                    or adapter._early_reject_unauthorized(user, channel, False)
                    or (adapter._slack_allowed_channels() and channel not in adapter._slack_allowed_channels())):
                await ack(response_action="errors", errors={"auto_response": "This channel setting is unavailable."})
                return
            await ack()
            acknowledged = True
            info = (await client.conversations_info(channel=channel)).get("channel") or {}
            if (not (info.get("is_channel") or info.get("is_group")) or info.get("is_member") is False
                    or info.get("is_ext_shared") or info.get("is_org_shared")
                    or not await _member(client, channel, user)):
                await client.chat_postEphemeral(channel=channel, user=user,
                    text="Channel membership could not be verified; the setting was not changed.")
                return
            _response_store.set(team, channel, choice == "on", user)
            response_url = metadata.get("response_url")
            confirmed = False
            if valid_slack_response_url(response_url):
                try:
                    from slack_sdk.webhook.async_client import AsyncWebhookClient
                    response = await AsyncWebhookClient(response_url).send_dict({
                        "replace_original": True, "response_type": "ephemeral",
                        "text": f"Respond automatically is now {'on' if choice == 'on' else 'off'} for this channel.",
                        "blocks": configure_card(choice == "on", metadata),
                    })
                    confirmed = response.status_code == 200
                except Exception:
                    pass
            if not confirmed:
                await client.chat_postEphemeral(channel=channel, user=user,
                    text=f"Respond automatically is now {'on' if choice == 'on' else 'off'} for this channel.",
                    **({"thread_ts": metadata["thread"]} if metadata.get("thread") else {}))
        except Exception as exc:
            logger.warning("pilot-slack-controls: channel config save failed (%s)", type(exc).__name__)
            if not acknowledged:
                try:
                    await ack(response_action="errors", errors={"auto_response": "Could not save the setting."})
                except Exception:
                    pass
                return
            try:
                await client.chat_postEphemeral(channel=metadata.get("channel", ""),
                    user=(body.get("user") or {}).get("id", ""),
                    text="Could not save the channel setting. Please try again.")
            except Exception:
                pass


def register(ctx):
    ctx.register_platform_handler("slack", _wire)
    ctx.register_tool("channel_response_setting", "pilot-slack-controls", SCHEMA,
                      lambda args, **kwargs: tool_handler(args, _response_store, **kwargs))
    ctx.register_hook("pre_llm_call", _ambient_pre_llm_call)


def _ambient_pre_llm_call(**kwargs):
    del kwargs
    try:
        from gateway.session_context import get_session_env
        if (get_session_env("HERMES_SESSION_PLATFORM", "").lower() != "slack" or
                get_session_env("HERMES_SESSION_CHAT_TYPE", "").lower() != "group"):
            return None
        return ambient_turn_context(get_session_env("HERMES_SESSION_SCOPE_ID", ""),
                                    get_session_env("HERMES_SESSION_CHAT_ID", ""),
                                    get_session_env("HERMES_SESSION_THREAD_ID", ""))
    except Exception:
        return None
