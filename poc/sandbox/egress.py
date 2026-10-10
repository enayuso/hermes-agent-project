"""Default-deny, credential-free HTTPS fetch for the host sandbox broker.

This is a narrow first egress route. It never follows redirects, uses ambient
proxy variables, accepts caller headers, or exposes credentials. DNS is resolved
and checked before a TLS connection is pinned to that approved public address.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import quote, urlsplit


MAX_URL_BYTES = 4096
MAX_BODY_BYTES = 1024 * 1024
ALLOWED_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml")


def load_policy(path: Path | None) -> frozenset[str]:
    """An absent policy intentionally allows no destinations."""
    if path is None:
        return frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("hosts"), list):
        raise ValueError("invalid egress policy")
    hosts = set()
    for value in data["hosts"]:
        if not isinstance(value, str) or not value or value != value.lower():
            raise ValueError("egress hosts must be lowercase DNS names")
        if value.endswith(".") or "*" in value or "/" in value or ":" in value:
            raise ValueError("egress host must be an exact DNS name")
        try:
            ascii_host = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid egress host") from exc
        if ascii_host != value or len(ascii_host) > 253 or "." not in ascii_host:
            raise ValueError("egress host must be an exact ASCII DNS name")
        try:
            ipaddress.ip_address(ascii_host)
        except ValueError:
            pass
        else:
            raise ValueError("IP literals cannot be allowlisted")
        hosts.add(ascii_host)
    return frozenset(hosts)


def validate_url(value: object, hosts: frozenset[str]) -> tuple[str, str]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_URL_BYTES:
        raise ValueError("invalid HTTPS URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only credential-free HTTPS URLs are accepted")
    if parsed.fragment or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("invalid HTTPS URL")
    try:
        port = parsed.port
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid HTTPS host or port") from exc
    if port not in (None, 443) or host not in hosts:
        raise PermissionError("destination is not allowed")
    target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return host, target


def public_addresses(host: str) -> list[str]:
    addresses = []
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = ipaddress.ip_address(sockaddr[0])
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global:
            raise PermissionError("destination resolves to a non-public address")
        addresses.append(sockaddr[0])
    if not addresses:
        raise PermissionError("destination has no public address")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, 443, timeout=12, context=ssl.create_default_context())
        self._approved_address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._approved_address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class PublicEgress:
    def __init__(self, hosts: frozenset[str]):
        self.hosts = hosts

    def fetch(self, url: object) -> dict:
        host, target = validate_url(url, self.hosts)
        address = public_addresses(host)[0]
        connection = _PinnedHTTPS(host, address)
        deadline = time.monotonic() + 20
        try:
            connection.request("GET", target, headers={
                "Host": host, "User-Agent": "PilotAgent/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if time.monotonic() > deadline:
                raise TimeoutError("HTTPS response was too slow")
            if 300 <= response.status < 400:
                raise PermissionError("redirects require a separately allowed destination")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if not any(content_type == kind or
                       (kind.endswith("/") and content_type.startswith(kind)) for kind in ALLOWED_TYPES):
                raise ValueError("response is not text or JSON")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("compressed responses are not accepted")
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("HTTPS response was too slow")
                chunk = response.read1(min(65536, MAX_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError("response exceeds 1 MiB")
            return {"status": response.status, "content_type": content_type,
                    "body": body.decode("utf-8", "replace")}
        finally:
            connection.close()
