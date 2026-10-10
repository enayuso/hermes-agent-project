#!/usr/bin/env bash
# One-time Outlook sign-in for the agent (Microsoft device-code flow).
# Prints a URL and a short code: open the URL, enter the code, sign in with your work account.
# The token cache is written to data/connectors/outlook/ (gitignored), so it survives restarts.
set -euo pipefail
docker exec -it -u hermes \
  -e MS365_MCP_TOKEN_CACHE_PATH=/opt/data/connectors/outlook/.token-cache.json \
  -e MS365_MCP_SELECTED_ACCOUNT_PATH=/opt/data/connectors/outlook/.selected-account.json \
  -e MS365_MCP_USE_KEYTAR=0 \
  pilot-agent sh -c 'cd /tmp && npx -y @softeria/ms-365-mcp-server@0.159.1 --org-mode --preset outlook --login'
echo "Signed in. Restart the agent so it picks up the connector: docker restart pilot-agent"
