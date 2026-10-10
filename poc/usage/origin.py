"""Short-lived, authenticated place identity for the model admission proxy."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time

_PLACE = re.compile(r"^[A-Z0-9]{3,}:[CG][A-Z0-9]{3,}$")
_DM = re.compile(r"^D[A-Z0-9]{3,}$")
_PROFILE = re.compile(r"^person-[a-z0-9-]{3,}$")
_SESSION = re.compile(r"^[a-zA-Z0-9_:\-.]{1,300}$")
_AUDIENCE = "pilot-model-admission-v1"


def scopes(kind: str, profile: str, place: str) -> tuple[str, ...]:
    """Derive allowance scopes from a verified origin, never from prompt text."""
    if kind in ("service_slack", "scheduled_slack") and profile == "service" and _PLACE.fullmatch(place):
        return ("organization", f"channel:{place}")
    if kind in ("personal_dm", "scheduled_personal_dm") and (
            _PROFILE.fullmatch(profile) and _DM.fullmatch(place)):
        return (f"personal:{profile}",)
    raise ValueError("unattributed or inconsistent model origin")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def sign(secret: bytes, *, kind: str, profile: str, place: str,
         session_id: str, at: int | None = None) -> str:
    if len(secret) < 32 or not _SESSION.fullmatch(session_id):
        raise ValueError("invalid signing secret or session")
    scopes(kind, profile, place)
    payload = {"aud": _AUDIENCE, "iat": int(time.time() if at is None else at),
               "kind": kind, "profile": profile, "place": place,
               "session": session_id, "nonce": secrets.token_hex(12)}
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
    return encoded + "." + _b64(mac)


def verify(secret: bytes, token: str, *, now: int | None = None) -> dict:
    if len(secret) < 32 or len(token) > 2000:
        raise ValueError("invalid origin credential")
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(supplied)):
            raise ValueError("invalid origin signature")
        payload = json.loads(_unb64(encoded))
        at = int(time.time() if now is None else now)
        if (not isinstance(payload, dict) or payload.get("aud") != _AUDIENCE
                or type(payload.get("iat")) is not int or abs(at - payload["iat"]) > 120
                or not isinstance(payload.get("session"), str)
                or not _SESSION.fullmatch(payload["session"])):
            raise ValueError("expired or malformed origin credential")
        scopes(payload.get("kind"), payload.get("profile"), payload.get("place"))
        return payload
    except (UnicodeError, ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise ValueError("invalid origin credential") from exc
