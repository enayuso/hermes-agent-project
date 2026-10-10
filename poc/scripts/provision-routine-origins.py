"""Attach the verified Slack home channel to the two existing public PoC routines.

Run inside Hermes with stdin (setup-routines.sh does this). The paused personal Outlook
briefing is intentionally excluded: it belongs in the owner's DM after profile isolation.
"""

import os
import re

from cron.jobs import list_jobs, update_job
from cron.scheduler import _resolve_delivery_target
from slack_sdk import WebClient


home = os.environ.get("SLACK_HOME_CHANNEL", "")
if not re.fullmatch(r"C[A-Z0-9]+", home):
    raise SystemExit("SLACK_HOME_CHANNEL must be a public Slack channel ID")
token = os.environ.get("SLACK_BOT_TOKEN", "")
if not token:
    raise SystemExit("Slack bot token is required to verify the workspace")
team = str(WebClient(token=token).auth_test().get("team_id") or "")
if not re.fullmatch(r"T[A-Z0-9]+", team):
    raise SystemExit("Slack auth_test did not return a valid workspace ID")

jobs = [job for job in list_jobs(include_disabled=True)
        if job.get("name") in {"weekly-agent-landscape", "channel-watch"}]
for job in jobs:
    origin = job.get("origin")
    if origin:
        if (origin.get("platform") != "slack" or origin.get("chat_id") != home
                or origin.get("scope_id") not in (None, team)):
            raise SystemExit(f"{job['name']}: existing origin differs from the verified Slack place")
        continue
    target = _resolve_delivery_target(job)
    if not target or target.get("platform") != "slack" or str(target.get("chat_id")) != home:
        raise SystemExit(f"{job['name']}: current delivery is not the home channel; no change made")

for job in jobs:
    origin = job.get("origin")
    if origin:
        if origin.get("scope_id") != team:
            update_job(job["id"], {"origin": {**origin, "scope_id": team}})
            print(f"{job['name']}: added verified workspace to its origin")
        else:
            print(f"{job['name']}: origin already set")
        continue
    update_job(job["id"], {"origin": {"platform": "slack", "chat_id": home,
                                       "scope_id": team, "thread_id": None, "user_id": None},
                           "deliver": "origin"})
    print(f"{job['name']}: bound to its home channel")
