"""Hermes terminal/file backend for the restricted Pilot sandbox broker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent.terminal_env_provider import TerminalEnvironmentProvider
from tools.environments.base import BaseEnvironment
from tools.environments.base_output import _popen_bash

from .client import fetch_public, search_public


CLIENT = str(Path(__file__).with_name("client.py"))


class BrokerEnvironment(BaseEnvironment):
    # Keep Hermes' command stdin separate from the broker's JSON transport.
    # Embedding it as a shell heredoc corrupts file writes when the wrapped
    # command already contains heredocs.
    _stdin_mode = "pipe"

    def __init__(self, *, task_id: str, timeout: int):
        super().__init__(cwd="/workspace", timeout=timeout)
        self.task_id = task_id
        self._closed = False
        # Hermes keeps this worker between turns, then its normal idle reaper
        # calls cleanup() after the configured lifetime.
        self._session_scoped = True
        self.init_session()

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                  stdin_data: str | None = None):
        if self._closed:
            raise RuntimeError("sandbox was released")
        request = json.dumps({"action": "exec", "session": self.task_id,
                              "command": cmd_string, "stdin": stdin_data or "",
                              "timeout": max(1, min(int(timeout), 300))})
        return _popen_bash([sys.executable, CLIENT], stdin_data=request)

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        request = json.dumps({"action": "release", "session": self.task_id})
        try:
            subprocess.run([sys.executable, CLIENT], input=request, text=True,
                           capture_output=True, timeout=12, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


class PilotSandboxProvider(TerminalEnvironmentProvider):
    name = "pilot_sandbox"
    display_name = "Pilot isolated sandbox"
    is_remote = True
    is_container = True
    session_isolated_when_nonpersistent = True

    @property
    def skip_container_guards(self) -> bool:
        # Existing Hermes approval policy still applies to shell commands.
        return False

    @property
    def env_description(self) -> str:
        return "an isolated, temporary Linux sandbox without direct network access"

    def is_available(self) -> bool:
        return bool(os.environ.get("PILOT_SANDBOX_BROKER_URL")
                    and os.environ.get("PILOT_SANDBOX_TOKEN_FILE")
                    and Path(os.environ["PILOT_SANDBOX_TOKEN_FILE"]).is_file())

    def create_environment(self, *, cwd: str, timeout: int, task_id: str = "default",
                           image: str | None = None, container_config: dict | None = None,
                           **kwargs):
        if not self.is_available():
            raise RuntimeError("Pilot sandbox broker is not configured")
        if container_config and container_config.get("container_persistent", True):
            raise RuntimeError("Pilot sandbox requires terminal.container_persistent: false")
        return BrokerEnvironment(task_id=task_id, timeout=timeout)


def register(ctx) -> None:
    ctx.register_terminal_environment_provider(PilotSandboxProvider())
    ctx.register_tool(
        name="web_fetch", toolset="pilot-egress",
        schema={"name": "web_fetch",
                "description": "Fetch text from an administrator-allowed public HTTPS host. "
                               "Redirects are blocked and no account credential is sent. "
                               "A denied destination needs an administrator rule.",
                "parameters": {"type": "object", "properties": {
                    "url": {"type": "string", "description": "HTTPS URL to fetch"}},
                    "required": ["url"]}},
        handler=lambda args, **kw: json.dumps(fetch_public(
            str(args.get("url", "")), str(kw.get("task_id") or "")), ensure_ascii=False),
    )
    ctx.register_tool(
        name="public_web_search", toolset="pilot-egress",
        schema={"name": "public_web_search",
                "description": "Search the public web through a fixed, administrator-allowed "
                               "search provider. No account credential is sent. "
                               "Search terms are sent to the provider.",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Results from 1 to 10"}},
                    "required": ["query"]}},
        handler=lambda args, **kw: json.dumps(search_public(
            args.get("query"), args.get("limit", 5),
            str(kw.get("task_id") or "")), ensure_ascii=False),
    )
