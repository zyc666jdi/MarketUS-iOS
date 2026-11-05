#!/usr/bin/env bash
# Launchd wrapper: loads the agent credential file, then runs one dispatcher sweep.
set -euo pipefail

REPO="${MARKETUS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO"

ENV_FILE="${AGENT_ENV_FILE:-$HOME/.config/marketUS/deepseek.env}"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

exec python3 Tools/dispatcher/dispatcher.py "$@"
