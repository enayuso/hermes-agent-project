#!/usr/bin/env python3
"""Restricted Docker worker broker for the Pilot PoC.

The broker runs on the host using the host user's Docker CLI. No Docker daemon
socket enters the gateway or a sidecar. Its API never accepts an image, mount,
network mode, user, or Docker flag from a caller.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from egress import PublicEgress, load_policy
from search import PublicSearch, validate_search


SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_COMMAND_BYTES = 64 * 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 300
WORKER_IMAGE = "nousresearch/hermes-agent:v2026.9.24"

# Fixed worker entry point: Docker argv contains no caller command or stdin.
# A private anonymous tmpfile becomes Bash's stdin after the JSON control
# message has been read. The worker has no network, host mounts, or secrets.
WORKER_RUNNER = """import json,os,sys,tempfile
r=json.load(sys.stdin)
fd=tempfile.TemporaryFile() if r['stdin'] else open(os.devnull,'rb')
if r['stdin']:
 fd.write(r['stdin'].encode('utf-8'))
 fd.seek(0)
os.dup2(fd.fileno(),0)
fd.close()
os.execv('/bin/bash',['/bin/bash','-c',r['command']])
"""


class SandboxCapacityError(RuntimeError):
    pass


def check_session(value: object) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value):
        raise ValueError("invalid session identity")
    return value


def check_command(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("command must be nonempty text")
    if len(value.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValueError("command is too large")
    return value


def check_stdin(value: object) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_STDIN_BYTES:
        raise ValueError("command stdin is too large")
    return value


def check_timeout(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be an integer from 1 to 300 seconds")
    return value


def worker_name(session: str) -> str:
    return "pilot-worker-" + hashlib.sha256(session.encode()).hexdigest()[:20]


def detected_bind_host(docker: str = "docker") -> str:
    """Find the host interface Docker's Linux containers can reach."""
    override = os.environ.get("PILOT_BROKER_BIND_HOST")
    if override:
        return override
    system = platform.system()
    if system in ("Darwin", "Windows"):
        return "127.0.0.1"
    if system == "Linux":
        info = subprocess.run([docker, "info", "--format", "{{.OperatingSystem}}"],
                              text=True, capture_output=True, timeout=10, check=True)
        if "Docker Desktop" in info.stdout:
            return "127.0.0.1"
        bridge = subprocess.run(
            [docker, "network", "inspect", "bridge", "--format",
             "{{(index .IPAM.Config 0).Gateway}}"],
            text=True, capture_output=True, timeout=10, check=True)
        return bridge.stdout.strip()
    raise RuntimeError(f"unsupported broker host OS: {system}")


def bind_address(docker: str = "docker") -> tuple[str, int]:
    """Listen on one local interface, never on every interface or a public address."""
    host = detected_bind_host(docker)
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError as exc:
        raise ValueError("broker bind host must be an IPv4 address") from exc
    if address.is_unspecified or not (address.is_loopback or address.is_private):
        raise ValueError("broker bind host must be a loopback or private IPv4 address")
    try:
        port = int(os.environ.get("PILOT_BROKER_PORT", "8787"))
    except ValueError as exc:
        raise ValueError("broker port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("broker port must be between 1024 and 65535")
    return host, port


def probe_container_route(port: int, *, docker: str = "docker", image: str = WORKER_IMAGE) -> None:
    """Fail startup if a disposable container cannot reach the host broker."""
    args = [docker, "run", "--rm", "--network", "bridge"]
    if platform.system() == "Linux":
        args += ["--add-host", "host.docker.internal:host-gateway"]
    args += ["--entrypoint", "python", image, "-c",
             "import socket; socket.create_connection(('host.docker.internal', "
             + str(port) + "), 5).close()"]
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("broker is unreachable from Docker containers") from exc


@dataclass
class Worker:
    container_id: str
    last_use: float = field(default_factory=time.monotonic)
    active: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class Broker:
    def __init__(self, *, docker: str = "docker", idle_seconds: int = 300, max_workers: int = 16,
                 image: str = WORKER_IMAGE):
        self.docker = docker
        self.idle_seconds = idle_seconds
        self.max_workers = max_workers
        self.image = image
        self._lock = threading.Lock()
        self._workers: dict[str, Worker] = {}

    def _docker(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run([self.docker, *args], text=True, encoding="utf-8",
                              errors="replace", capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, check=True)

    def _create(self, session: str) -> Worker:
        # The only caller-controlled Docker input is a hashed name/label.
        name = worker_name(session)
        args = [
            "run", "-d", "--name", name, "--label", "pilot-sandbox=1",
            "--label", f"pilot-session={name}",
            "--network", "none", "--read-only", "--user", "65532:65532",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "64", "--cpus", "1", "--memory", "512m",
            "--tmpfs", "/workspace:rw,exec,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=0700",
            "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=64m",
            # The pinned Hermes image declares VOLUME /opt/data. Override it
            # so Docker does not create a persistent anonymous volume per worker.
            "--tmpfs", "/opt/data:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700",
            "-e", "HOME=/workspace", "-w", "/workspace",
            "--entrypoint", "/bin/sh", self.image, "-c", "sleep infinity",
        ]
        result = self._docker(*args, timeout=120)
        container_id = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise RuntimeError("Docker did not return a container ID")
        return Worker(container_id)

    def _ensure(self, session: str) -> Worker:
        with self._lock:
            worker = self._workers.get(session)
            if worker is not None:
                return worker
            if len(self._workers) >= self.max_workers:
                raise SandboxCapacityError("sandbox capacity is full")
            worker = self._create(session)
            self._workers[session] = worker
            return worker

    def _remove(self, worker: Worker) -> None:
        try:
            # -v also removes any anonymous image-declared volume if a future
            # image adds one outside the explicit tmpfs mounts.
            self._docker("rm", "-f", "-v", worker.container_id, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass

    def release(self, session: str) -> None:
        session = check_session(session)
        with self._lock:
            worker = self._workers.pop(session, None)
        if worker:
            self._remove(worker)

    def close(self) -> None:
        """Destroy all workers when the broker stops, including busy workers."""
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            self._remove(worker)

    def reap_idle(self) -> int:
        now = time.monotonic()
        stale: list[Worker] = []
        with self._lock:
            for session, worker in list(self._workers.items()):
                if worker.active == 0 and now - worker.last_use >= self.idle_seconds:
                    stale.append(self._workers.pop(session))
        for worker in stale:
            self._remove(worker)
        return len(stale)

    def remove_orphans(self) -> None:
        """A broker restart starts fresh; its old ephemeral workers carry no durable state."""
        try:
            result = self._docker("ps", "-aq", "--filter", "label=pilot-sandbox=1")
        except (OSError, subprocess.SubprocessError):
            return
        for container_id in result.stdout.splitlines():
            if re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                self._remove(Worker(container_id))

    def execute(self, session: str, command: str, timeout: int,
                stdin_data: str = "") -> dict:
        session = check_session(session)
        command = check_command(command)
        timeout = check_timeout(timeout)
        stdin_data = check_stdin(stdin_data)
        worker = self._ensure(session)
        with worker.lock:
            with self._lock:
                # release() may have removed this worker while we waited.
                if self._workers.get(session) is not worker:
                    raise RuntimeError("sandbox was released; retry the command")
                worker.active += 1
                worker.last_use = time.monotonic()
            try:
                result = self._exec_bounded(worker.container_id, command, timeout, stdin_data)
                if result.pop("broken", False):
                    self.release(session)
                return result
            finally:
                with self._lock:
                    worker.active -= 1
                    worker.last_use = time.monotonic()

    def _exec_bounded(self, container_id: str, command: str, timeout: int,
                      stdin_data: str = "") -> dict:
        # The command goes on stdin; it is never interpolated into a Docker
        # argv option or logged in the broker.
        proc = subprocess.Popen(
            [self.docker, "exec", "-i", "--user", "65532:65532", "-w", "/workspace",
             container_id, "python3", "-c", WORKER_RUNNER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt")
        assert proc.stdin is not None and proc.stdout is not None
        script = json.dumps({"command": command, "stdin": stdin_data},
                            ensure_ascii=False).encode("utf-8")
        output = bytearray()
        finished = threading.Event()
        overflow = threading.Event()
        read_failed = threading.Event()

        def feed() -> None:
            try:
                proc.stdin.write(script)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def drain() -> None:
            try:
                while True:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    remaining = MAX_OUTPUT_BYTES - len(output)
                    output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
                        break
            except OSError:
                read_failed.set()
            finally:
                finished.set()

        writer = threading.Thread(target=feed, name="sandbox-stdin", daemon=True)
        reader = threading.Thread(target=drain, name="sandbox-stdout", daemon=True)
        reader.start()
        writer.start()
        deadline = time.monotonic() + timeout
        reason = ""
        try:
            while not finished.wait(0.05):
                if overflow.is_set():
                    reason = "sandbox output exceeded 1 MiB"
                    break
                if time.monotonic() >= deadline:
                    reason = "sandbox command timed out"
                    break
            if overflow.is_set():
                reason = "sandbox output exceeded 1 MiB"
            if read_failed.is_set():
                reason = "sandbox output could not be read"
            if reason:
                proc.kill()
                proc.wait(timeout=5)
                return {"output": reason, "returncode": 124 if "timed out" in reason else 125, "broken": True}
            try:
                code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                return {"output": "sandbox command timed out", "returncode": 124, "broken": True}
            return {"output": output.decode("utf-8", "replace"), "returncode": code}
        finally:
            writer.join(timeout=1)
            reader.join(timeout=1)
            proc.stdout.close()


class Handler(BaseHTTPRequestHandler):
    broker: Broker
    token: str
    egress: PublicEgress
    search: PublicSearch
    ready: threading.Event

    def log_message(self, format: str, *args) -> None:
        # Never log caller-submitted session IDs, commands, or authorization.
        return

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(404, {"error": "not found"})
        elif not self._authorized():
            self._reply(401, {"error": "unauthorized"})
        elif not self.ready.is_set():
            self._reply(503, {"error": "broker is starting"})
        else:
            self._reply(200, {"status": "ready"})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, "Bearer " + self.token)

    def do_POST(self) -> None:
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("request must be an object")
            if self.path == "/exec":
                result = self.broker.execute(body.get("session"), body.get("command"),
                                             body.get("timeout"), body.get("stdin", ""))
                self._reply(200, result)
            elif self.path == "/release":
                self.broker.release(body.get("session"))
                self._reply(200, {"released": True})
            elif self.path == "/fetch":
                check_session(body.get("session"))
                try:
                    fetched = self.egress.fetch(body.get("url"))
                except PermissionError:
                    raise
                except (OSError, http.client.HTTPException):
                    self._reply(502, {"error": "allowed destination could not be fetched"})
                    return
                self._reply(200, fetched)
            elif self.path == "/search":
                check_session(body.get("session"))
                query, limit = validate_search(body.get("query"), body.get("limit", 5))
                try:
                    found = self.search.search(query, limit)
                except PermissionError:
                    raise
                except (OSError, http.client.HTTPException, RuntimeError, TimeoutError,
                        ValueError):
                    self._reply(502, {"error": "search provider is unavailable"})
                    return
                self._reply(200, found)
            else:
                self._reply(404, {"error": "not found"})
        except PermissionError as exc:
            self._reply(403, {"error": str(exc)})
        except ValueError as exc:
            self._reply(400, {"error": str(exc)})
        except SandboxCapacityError:
            self._reply(429, {"error": "sandbox capacity is full", "retry_after_seconds": 5})
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            # Docker stderr can contain host details. Keep the client error
            # stable; operators inspect the host broker log for diagnosis.
            print(f"broker operation failed: {type(exc).__name__}", flush=True)
            self._reply(503, {"error": "sandbox unavailable; retry later"})


def main() -> None:
    token_file = Path(os.environ["PILOT_BROKER_TOKEN_FILE"])
    token = token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit("broker token must be at least 32 characters")
    policy_path = os.environ.get("PILOT_EGRESS_POLICY_FILE")
    hosts = load_policy(Path(policy_path) if policy_path else None)
    egress = PublicEgress(hosts)
    search = PublicSearch(hosts)
    broker = Broker()
    broker.remove_orphans()

    def reaper() -> None:
        while True:
            time.sleep(30)
            broker.reap_idle()

    ready = threading.Event()
    handler = type("BoundHandler", (Handler,),
                   {"broker": broker, "token": token, "egress": egress,
                    "search": search, "ready": ready})
    address = bind_address()
    server = ThreadingHTTPServer(address, handler)
    serving = threading.Thread(target=server.serve_forever, name="sandbox-http", daemon=True)
    serving.start()
    try:
        probe_container_route(address[1])
        ready.set()
        print("Container-to-broker route verified; broker ready.", flush=True)
        threading.Thread(target=reaper, name="sandbox-reaper", daemon=True).start()
        serving.join()
    finally:
        server.shutdown()
        server.server_close()
        broker.close()


if __name__ == "__main__":
    main()
