#!/usr/bin/env python3
"""Security and lifecycle checks for the restricted worker broker."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "sandbox" / "broker.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("pilot_broker", SOURCE)
assert spec and spec.loader
broker_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = broker_module
spec.loader.exec_module(broker_module)


class FakeBroker(broker_module.Broker):
    def __init__(self):
        super().__init__(idle_seconds=5, max_workers=2)
        self.calls: list[tuple[str, ...]] = []
        self.serial = 0

    def _docker(self, *args: str, timeout: int = 30):
        self.calls.append(args)
        if args[0] == "run":
            self.serial += 1
            return subprocess.CompletedProcess(args, 0, f"{self.serial:064x}\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def _exec_bounded(self, container_id: str, command: str, timeout: int,
                      stdin_data: str = ""):
        return {"output": container_id + ":" + command, "returncode": 0}


class BrokerTests(unittest.TestCase):
    def test_bind_address_is_explicit_and_local(self):
        with patch.dict("os.environ", {"PILOT_BROKER_BIND_HOST": "172.17.0.1",
                                       "PILOT_BROKER_PORT": "18877"}):
            self.assertEqual(broker_module.bind_address(), ("172.17.0.1", 18877))
        for host in ("0.0.0.0", "8.8.8.8", "localhost"):
            with patch.dict("os.environ", {"PILOT_BROKER_BIND_HOST": host}):
                with self.assertRaises(ValueError):
                    broker_module.bind_address()

    def test_host_bind_detection_for_desktop_and_linux_engine(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(broker_module.platform, "system", return_value="Windows"):
            self.assertEqual(broker_module.detected_bind_host(), "127.0.0.1")
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(broker_module.platform, "system", return_value="Linux"), \
             patch.object(broker_module.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0, "Ubuntu 24.04\n", ""),
                 subprocess.CompletedProcess([], 0, "172.17.0.1\n", "")]) as run:
            self.assertEqual(broker_module.detected_bind_host(), "172.17.0.1")
            self.assertEqual(run.call_count, 2)

    def test_route_probe_contains_no_token_or_mount(self):
        with patch.object(broker_module.platform, "system", return_value="Linux"), \
             patch.object(broker_module.subprocess, "run") as run:
            broker_module.probe_container_route(8787)
            args = run.call_args.args[0]
            self.assertIn("host.docker.internal:host-gateway", args)
            self.assertNotIn("--mount", args)
            self.assertNotIn("-v", args)
            self.assertNotIn("--env-file", args)

    def test_rejects_untrusted_identity_command_and_timeout(self):
        for value in ("../other", "a:b", "", "a" * 129, None):
            with self.assertRaises(ValueError):
                broker_module.check_session(value)
        with self.assertRaises(ValueError):
            broker_module.check_command("x" * (broker_module.MAX_COMMAND_BYTES + 1))
        with self.assertRaises(ValueError):
            broker_module.check_stdin("x" * (broker_module.MAX_STDIN_BYTES + 1))
        for value in (0, 301, 1.5, True):
            with self.assertRaises(ValueError):
                broker_module.check_timeout(value)

    def test_worker_settings_are_fixed_and_contain_no_host_mount(self):
        broker = FakeBroker()
        broker.execute("thread_a", "printf hello", 10)
        run = next(call for call in broker.calls if call[0] == "run")
        self.assertIn("none", run)
        self.assertIn("--read-only", run)
        self.assertIn("--cap-drop", run)
        self.assertIn("--pids-limit", run)
        self.assertIn("/workspace:rw,exec,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=0700", run)
        self.assertIn("/opt/data:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700", run)
        self.assertNotIn("-v", run)
        self.assertNotIn("--mount", run)
        self.assertNotIn("printf hello", run)
        self.assertNotIn("thread_a", run)
        self.assertEqual(run[-3:], (broker_module.WORKER_IMAGE, "-c", "sleep infinity"))

    def test_session_reuse_isolation_idle_and_rebuild(self):
        broker = FakeBroker()
        first = broker.execute("thread_a", "true", 10)["output"].split(":")[0]
        again = broker.execute("thread_a", "true", 10)["output"].split(":")[0]
        second = broker.execute("thread_b", "true", 10)["output"].split(":")[0]
        self.assertEqual(first, again)
        self.assertNotEqual(first, second)
        broker._workers["thread_a"].last_use = time.monotonic() - 10
        self.assertEqual(broker.reap_idle(), 1)
        self.assertNotIn("thread_a", broker._workers)
        self.assertIn("thread_b", broker._workers)
        rebuilt = broker.execute("thread_a", "true", 10)["output"].split(":")[0]
        self.assertNotEqual(first, rebuilt)

    def test_capacity_refuses_without_starting_third_worker(self):
        broker = FakeBroker()
        broker.execute("a", "true", 10)
        broker.execute("b", "true", 10)
        with self.assertRaisesRegex(broker_module.SandboxCapacityError, "capacity"):
            broker.execute("c", "true", 10)
        self.assertEqual(len([c for c in broker.calls if c[0] == "run"]), 2)

        handler = type("TestHandler", (broker_module.Handler,), {
            "broker": broker, "token": "t" * 32})
        payload = json.dumps({"session": "c", "command": "true", "timeout": 10}).encode()
        instance = object.__new__(handler)
        instance.path = "/exec"
        instance.headers = {"Content-Length": str(len(payload)),
                            "Authorization": "Bearer " + "t" * 32}
        instance.rfile = io.BytesIO(payload)
        result = []
        instance._reply = lambda status, body: result.append((status, body))
        instance.do_POST()
        self.assertEqual(result[0][0], 429)
        self.assertEqual(result[0][1]["retry_after_seconds"], 5)

    def test_close_removes_all_workers(self):
        broker = FakeBroker()
        broker.execute("a", "true", 10)
        broker.execute("b", "true", 10)
        broker.close()
        self.assertFalse(broker._workers)
        removals = [c for c in broker.calls if c[0] == "rm"]
        self.assertEqual(len(removals), 2)
        self.assertTrue(all(c[1:3] == ("-f", "-v") for c in removals))

    def test_fetch_route_requires_token_and_denies_without_policy(self):
        handler = type("TestHandler", (broker_module.Handler,), {
            "broker": FakeBroker(), "token": "t" * 32,
            "egress": broker_module.PublicEgress(frozenset())})
        payload = json.dumps({"session": "thread_a", "url": "https://example.org/"}).encode()

        def call(authorization):
            instance = object.__new__(handler)
            instance.path = "/fetch"
            instance.headers = {"Content-Length": str(len(payload)),
                                "Authorization": authorization}
            instance.rfile = io.BytesIO(payload)
            result = []
            instance._reply = lambda status, body: result.append((status, body))
            instance.do_POST()
            return result[0]

        self.assertEqual(call("")[0], 401)
        self.assertEqual(call("Bearer " + "t" * 32)[0], 403)

    def test_health_waits_for_route_probe(self):
        ready = threading.Event()
        handler = type("TestHandler", (broker_module.Handler,), {
            "token": "t" * 32, "ready": ready})
        instance = object.__new__(handler)
        instance.path = "/health"
        instance.headers = {"Authorization": "Bearer " + "t" * 32}
        result = []
        instance._reply = lambda status, body: result.append(status)
        instance.do_GET()
        ready.set()
        instance.do_GET()
        self.assertEqual(result, [503, 200])


if __name__ == "__main__":
    unittest.main()
