#!/usr/bin/env python3
"""Run the staged local model quota proxy after an operator sets its policy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from usage.model_proxy import ModelProxy  # noqa: E402
from usage.policy import Policy  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream-api-key-file", type=Path)
    args = parser.parse_args()
    secret = args.secret_file.read_bytes().strip()
    api_key = (args.upstream_api_key_file.read_text(encoding="utf-8").strip()
               if args.upstream_api_key_file else "")
    with ModelProxy(("127.0.0.1", args.port), policy=Policy.load(args.policy),
                    secret=secret, ledger=args.ledger, upstream_api_key=api_key) as proxy:
        proxy.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
