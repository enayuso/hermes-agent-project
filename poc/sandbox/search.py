"""Fixed-destination, credential-free search through the host broker.

The wire format matches Hermes v2026.9.24's Parallel keyless MCP route, but
this adapter never invokes Hermes' provider fallback or accepts a caller URL.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from egress import MAX_BODY_BYTES, _PinnedHTTPS, public_addresses


SEARCH_HOST = "search.parallel.ai"
SEARCH_PATH = "/mcp"
MAX_QUERY_CHARS = 1000
MAX_RESULTS = 10
SESSION_ID = uuid.uuid4().hex


def validate_search(query: object, limit: object) -> tuple[str, int]:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ValueError("search query must contain 1 to 1000 characters")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError("search limit must be an integer from 1 to 10")
    return query.strip(), limit


def _mcp_text(body: str) -> str:
    # Parallel currently returns a JSON-RPC envelope. Accept its documented
    # SSE content type as Hermes does, but do not execute any returned data.
    candidates = [body.strip()] if body.lstrip().startswith("{") else []
    candidates.extend(line[6:] for line in re.split(r"\r\n|\r|\n", body)
                      if line.startswith("data: "))
    for candidate in candidates:
        try:
            envelope = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(envelope, dict):
            continue
        if envelope.get("error"):
            raise RuntimeError("search provider returned an error")
        result = envelope.get("result") or {}
        if not isinstance(result, dict) or result.get("isError"):
            raise RuntimeError("search provider returned an error")
        for item in result.get("content") or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"]
    raise ValueError("search provider returned an unexpected response")


class PublicSearch:
    def __init__(self, hosts: frozenset[str]):
        self.hosts = hosts

    def search(self, query: object, limit: object = 5) -> dict:
        query, limit = validate_search(query, limit)
        if SEARCH_HOST not in self.hosts:
            raise PermissionError("search provider is not allowed")
        address = public_addresses(SEARCH_HOST)[0]
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "web_search", "arguments": {
                "objective": query, "search_queries": [query], "session_id": SESSION_ID}},
        }, ensure_ascii=False).encode("utf-8")
        connection = _PinnedHTTPS(SEARCH_HOST, address)
        deadline = time.monotonic() + 20
        try:
            connection.request("POST", SEARCH_PATH, body=payload, headers={
                "Host": SEARCH_HOST, "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "PilotAgent/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError("search provider is unavailable")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("compressed search responses are not accepted")
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("search provider was too slow")
                chunk = response.read1(min(65536, MAX_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError("search response exceeds 1 MiB")
            data = json.loads(_mcp_text(body.decode("utf-8", "replace")))
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("search provider returned an unexpected result")
            results = []
            for item in data["results"][:limit]:
                if not isinstance(item, dict):
                    continue
                excerpts = item.get("excerpts") or []
                results.append({"url": str(item.get("url") or ""),
                                "title": str(item.get("title") or ""),
                                "description": " ".join(str(s) for s in excerpts[:5])
                                if isinstance(excerpts, list) else ""})
            return {"results": results}
        finally:
            connection.close()
