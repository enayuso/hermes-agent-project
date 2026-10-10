#!/usr/bin/env python3
"""Optional host integration check for a detached job in one disposable worker."""

import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
from broker import Broker  # noqa: E402


def main() -> None:
    broker = Broker(max_workers=2)
    session = "background_smoke"
    try:
        launch = broker.execute(session, "( nohup bash -lc 'sleep 2; printf done' "
                                "> /workspace/bg.log 2>&1; echo $? > /workspace/bg.exit ) "
                                "& echo $!", 10)
        assert launch["returncode"] == 0, launch
        assert launch["output"].strip().splitlines()[-1].isdigit(), launch
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = broker.execute(session,
                                    "if test -f /workspace/bg.exit; then "
                                    "cat /workspace/bg.log; fi", 10)
            if status["output"].strip() == "done":
                break
            time.sleep(0.5)
        else:
            raise AssertionError("background command did not finish in its worker")
        broker.release(session)
        rebuilt = broker.execute(session, "test ! -e /workspace/bg.log", 10)
        assert rebuilt["returncode"] == 0, rebuilt
        print("background run, polling, release, and clean rebuild passed")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
