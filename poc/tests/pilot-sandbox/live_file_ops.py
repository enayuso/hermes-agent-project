"""Run inside a disposable Hermes client image with /plugin and token mounted.

This checks the real pinned Hermes file stack, including its stdin transport
and write guard. The host broker must already be running.
"""

import importlib.util
import sys
from pathlib import Path


home = Path("/tmp/pilot-file-smoke")
home.mkdir()
(home / "config.yaml").write_text(
    "terminal:\n  backend: pilot_sandbox\n  cwd: /workspace\n"
    "  container_persistent: false\n", encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "pilot_file_smoke", "/plugin/__init__.py",
    submodule_search_locations=["/plugin"])
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from agent.terminal_env_registry import register_provider
from tools.file_tools import _get_file_ops
from pilot_file_smoke.client import broker_request

register_provider(module.PilotSandboxProvider())
try:
    first = _get_file_ops("pilot_file_smoke_a")
    data = "Hello, 안녕하세요, bonjour.\n"
    written = first.write_file("/workspace/note.txt", data)
    assert not written.error and written.bytes_written == len(data.encode("utf-8")), written.error
    assert data.strip() in first.read_file("/workspace/note.txt").content
    second = _get_file_ops("pilot_file_smoke_b")
    assert second.read_file("/workspace/note.txt").error
    print("Hermes file write/read and cross-session isolation passed.")
finally:
    for session in ("pilot_file_smoke_a", "pilot_file_smoke_b"):
        broker_request("release", {"session": session})
