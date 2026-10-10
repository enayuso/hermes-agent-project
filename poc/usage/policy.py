"""Explicit, operator-chosen token allowance policy. No policy means no cutover."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Policy:
    model: str
    upstream_base_url: str
    timezone: str
    organization_tokens: int
    default_channel_tokens: int
    personal_dm_tokens: int
    context_window_tokens: int
    max_output_tokens: int

    @classmethod
    def load(cls, path: Path) -> "Policy":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("enabled") is not True:
            raise ValueError("model admission requires an explicitly enabled policy")
        policy = cls(**{name: data[name] for name in cls.__dataclass_fields__})
        policy.validate()
        return policy

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model or len(self.model) > 200:
            raise ValueError("exact model is required")
        parsed = urlsplit(self.upstream_base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1"):
            raise ValueError("upstream must be a fixed OpenAI-compatible /v1 URL")
        # Accessing port also rejects a malformed or out-of-range explicit port.
        _ = parsed.port
        ZoneInfo(self.timezone)
        for name in ("organization_tokens", "default_channel_tokens", "personal_dm_tokens",
                     "context_window_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_output_tokens > self.context_window_tokens:
            raise ValueError("output bound cannot exceed model context")
        for name in ("organization_tokens", "default_channel_tokens", "personal_dm_tokens"):
            if getattr(self, name) < self.context_window_tokens:
                raise ValueError(f"{name} must cover one full request reservation")

    def limits_for(self, scopes: tuple[str, ...]) -> dict[str, int]:
        result = {"organization": self.organization_tokens} if "organization" in scopes else {}
        for scope in scopes:
            if scope.startswith("channel:"):
                result[scope] = self.default_channel_tokens
            elif scope.startswith("personal:"):
                result[scope] = self.personal_dm_tokens
        return result
