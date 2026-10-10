"""Observe Hermes physical model requests without storing conversation content."""

from __future__ import annotations

import logging

from hermes_constants import get_hermes_home

from .audit import classify, record
from .admission import stamp_main_request, install_auxiliary_signer

logger = logging.getLogger(__name__)


def _record_request(**kwargs):
    try:
        from cron.jobs import get_job
        from gateway.session_context import get_session_env

        home = get_hermes_home()
        session_id = str(kwargs.get("session_id") or "")
        platform = str(kwargs.get("platform") or "")
        context = {
            "platform": get_session_env("HERMES_SESSION_PLATFORM", ""),
            "chat_id": get_session_env("HERMES_SESSION_CHAT_ID", ""),
            "chat_type": get_session_env("HERMES_SESSION_CHAT_TYPE", ""),
            "scope_id": get_session_env("HERMES_SESSION_SCOPE_ID", ""),
            "session_id": get_session_env("HERMES_SESSION_ID", ""),
        }
        kind, place = classify(session_id, platform, context, get_job)
        profile = home.name if home.parent.name == "profiles" else "service"
        record(home / "plugin-data" / "pilot-usage" / "requests.db",
               request_id=str(kwargs.get("api_request_id") or ""),
               session_id=session_id, profile=profile, kind=kind, place=place,
               platform=platform, model=str(kwargs.get("model") or ""),
               started_at=kwargs.get("started_at") or 0, usage=kwargs.get("usage"),
               task=str(kwargs.get("aux_task") or ""))
    except Exception as exc:
        logger.warning("pilot-usage: request audit failed (%s)", type(exc).__name__)


def register(ctx):
    install_auxiliary_signer()
    ctx.register_hook("post_api_request", _record_request)
    ctx.register_hook("post_auxiliary_call", _record_request)
    ctx.register_middleware("llm_request", stamp_main_request)
