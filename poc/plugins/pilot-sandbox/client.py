#!/usr/bin/env python3
"""Fixed-protocol client spawned by the Hermes terminal environment."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def broker_request(action: str, request: dict, *, timeout: int = 20) -> dict:
    """Call one fixed broker endpoint without inheriting ambient proxy settings."""
    if action not in ("exec", "release", "fetch", "search"):
        raise ValueError("unsupported action")
    url = os.environ["PILOT_SANDBOX_BROKER_URL"].rstrip("/") + "/" + action
    token = Path(os.environ["PILOT_SANDBOX_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    payload = json.dumps(request).encode("utf-8")
    http_request = urllib.request.Request(
        url, payload,
        {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(http_request, timeout=timeout) as response:
        return json.load(response)


def fetch_public(url: str, session: str) -> dict:
    try:
        return broker_request("fetch", {"url": url, "session": session}, timeout=20)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {"error": "This HTTPS destination is not allowed for Pilot Agent."}
        if exc.code == 400:
            return {"error": "The HTTPS URL is invalid."}
        return {"error": "The allowed HTTPS destination could not be fetched."}
    except (KeyError, ValueError, OSError, urllib.error.URLError):
        return {"error": "The Pilot network boundary is unavailable; try later."}


def search_public(query: str, limit: int, session: str) -> dict:
    try:
        return broker_request("search", {"query": query, "limit": limit,
                                         "session": session}, timeout=25)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {"error": "Web search is not enabled for Pilot Agent."}
        if exc.code == 400:
            return {"error": "The search query or result limit is invalid."}
        return {"error": "Web search is temporarily unavailable."}
    except (KeyError, ValueError, OSError, urllib.error.URLError):
        return {"error": "The Pilot network boundary is unavailable; try later."}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        action = request.pop("action")
        if action not in ("exec", "release"):
            raise ValueError("unsupported action")
        timeout = min(330, int(request.get("timeout", 10)) + 30)
        result = broker_request(action, request, timeout=timeout)
        if action == "exec":
            sys.stdout.write(str(result.get("output", "")))
            return int(result.get("returncode", 125))
        return 0
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            sys.stderr.write("sandbox capacity is full; retry in a few seconds\n")
        else:
            sys.stderr.write("sandbox broker unavailable (HTTP error)\n")
        return 125
    except (KeyError, ValueError, OSError, urllib.error.URLError) as exc:
        # Never print the URL, token, request, or exception text.
        sys.stderr.write(f"sandbox broker unavailable ({type(exc).__name__})\n")
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
