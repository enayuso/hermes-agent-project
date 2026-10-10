"""Sign verified main and auxiliary origins for a mandatory model admission proxy.

This is inactive until both the signing key and shared library directory are
configured. If signing fails after cutover, the proxy rejects the unsigned call.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from hermes_constants import get_hermes_home

from .audit import classify


def _setup():
    key_file = os.environ.get("PILOT_QUOTA_SIGNING_KEY_FILE")
    library_dir = os.environ.get("PILOT_QUOTA_LIB_DIR")
    proxy_url = os.environ.get("PILOT_QUOTA_PROXY_BASE_URL")
    if not key_file and not library_dir and not proxy_url:
        return None
    if not key_file or not library_dir or not proxy_url:
        raise RuntimeError("model admission configuration is incomplete")
    library = Path(library_dir)
    if not (library / "usage" / "origin.py").is_file():
        raise RuntimeError("model admission library is missing")
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from usage.origin import sign
    return sign, Path(key_file).read_bytes().strip(), proxy_url.rstrip("/")


def _identity(session_id: str, platform: str) -> tuple[str, str, str]:
    from cron.jobs import get_job
    from gateway.session_context import get_session_env

    home = Path(get_hermes_home())
    profile = home.name if home.parent.name == "profiles" else "service"
    context = {
        "platform": get_session_env("HERMES_SESSION_PLATFORM", ""),
        "chat_id": get_session_env("HERMES_SESSION_CHAT_ID", ""),
        "chat_type": get_session_env("HERMES_SESSION_CHAT_TYPE", ""),
        "scope_id": get_session_env("HERMES_SESSION_SCOPE_ID", ""),
        "session_id": get_session_env("HERMES_SESSION_ID", ""),
    }
    kind, place = classify(session_id, platform, context, get_job)
    return kind, profile, place


def _require_proxy_target(actual: str, expected: str) -> None:
    parsed = urlsplit(actual)
    configured = urlsplit(expected)
    if (parsed.scheme, parsed.netloc, parsed.path.rstrip("/")) != (
            configured.scheme, configured.netloc, configured.path.rstrip("/")):
        raise RuntimeError("model request is not routed to the admission proxy")


def stamp_main_request(**kwargs):
    setup = _setup()
    if setup is None:
        return None
    sign, secret, proxy_url = setup
    _require_proxy_target(str(kwargs.get("base_url") or ""), proxy_url)
    session_id = str(kwargs.get("session_id") or "")
    kind, profile, place = _identity(session_id, str(kwargs.get("platform") or ""))
    credential = sign(secret, kind=kind, profile=profile,
                      place=place, session_id=session_id)
    request = dict(kwargs["request"])
    headers = dict(request.get("extra_headers") or {})
    headers["X-Pilot-Origin"] = credential
    request["extra_headers"] = headers
    return {"request": request, "source": "pilot-usage", "reason": "verified-origin"}


def _stamp_auxiliary(*, client, kwargs: dict) -> None:
    setup = _setup()
    if setup is None:
        return
    sign, secret, proxy_url = setup
    _require_proxy_target(str(getattr(client, "base_url", "") or ""), proxy_url)
    from agent.auxiliary_hooks import _parent_turn_identity

    parent = _parent_turn_identity()
    session_id = str(parent.get("session_id") or "")
    platform = str(parent.get("platform") or "")
    if not session_id:
        from gateway.session_context import get_session_env
        session_id = get_session_env("HERMES_SESSION_ID", "")
        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
    kind, profile, place = _identity(session_id, platform)
    credential = sign(secret, kind=kind, profile=profile,
                      place=place, session_id=session_id)
    headers = dict(kwargs.get("extra_headers") or {})
    headers["X-Pilot-Origin"] = credential
    kwargs["extra_headers"] = headers


def install_auxiliary_signer() -> None:
    """Wrap the pinned Hermes auxiliary relay, including retry/stream attempts.

    The relay takes the same mutable kwargs dict used by its provider callback.
    This is an explicit pinned-runtime integration, installed only on cutover.
    A changed relay signature must fail startup instead of silently skipping it.
    """
    if _setup() is None:
        return
    import inspect
    from agent import auxiliary_hooks

    for name in ("run_with_aux_hooks", "arun_with_aux_hooks"):
        original = getattr(auxiliary_hooks, name)
        if getattr(original, "_pilot_quota_wrapped", False):
            continue
        params = inspect.signature(original).parameters
        if not {"call", "client", "kwargs", "aux_task", "metadata"}.issubset(params):
            raise RuntimeError("unsupported Hermes auxiliary relay signature")
        if name == "run_with_aux_hooks":
            def wrapped(call, *, _original=original, **options):
                _stamp_auxiliary(client=options["client"], kwargs=options["kwargs"])
                return _original(call, **options)
        else:
            async def wrapped(call, *, _original=original, **options):
                _stamp_auxiliary(client=options["client"], kwargs=options["kwargs"])
                return await _original(call, **options)
        wrapped._pilot_quota_wrapped = True
        setattr(auxiliary_hooks, name, wrapped)
