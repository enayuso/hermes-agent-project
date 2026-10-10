#!/usr/bin/env python3
"""Mirror the receiving bot's credentials and access policy into Hermes' default scope.

Docker --env-file populates process os.environ. Hermes multiplexing intentionally does
not fall back to that global environment for profile secrets, including Slack's
allowed-user list. Keep the default profile's .env in sync without disturbing
Hermes-managed settings in that file or printing any values.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    "API_SERVER_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_USERS",
    "SLACK_HOME_CHANNEL", "SLACK_FREE_RESPONSE_CHANNELS", "HERMES_TIMEZONE",
    "HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
    "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
}


def lines_by_key(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in KEYS:
                result[key] = line
    return result


def main() -> None:
    source = lines_by_key(ROOT / ".env")
    required = {"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_USERS", "API_SERVER_KEY"}
    if not required <= source.keys():
        raise RuntimeError("missing required default-profile credentials or Slack access policy")
    target = ROOT / "data" / ".env"
    old = target.read_text().splitlines() if target.exists() else []
    kept = [line for line in old if not ("=" in line and line.split("=", 1)[0] in KEYS)]
    target.write_text("\n".join(kept + [source[key] for key in sorted(source)]) + "\n")
    target.chmod(0o600)
    print(f"Synchronized {len(source)} default-profile settings without displaying values")


if __name__ == "__main__":
    main()
