#!/usr/bin/env bash
# Launchd runner: tolerate transient git failures; never abort the sweep on pull errors.
set -uo pipefail

RUNNER="${MARKETUS_RUNNER:-$HOME/Library/Application Support/MarketUS/runner}"
cd "$RUNNER" || { echo "runner missing: $RUNNER" >&2; exit 1; }

for attempt in 1 2 3; do
    if git pull --ff-only --quiet; then
        break
    fi
    echo "[$(date -u +%FT%TZ)] git pull failed (attempt $attempt/3); retrying" >&2
    sleep $((attempt * 10))
done

ENV_FILE="${AGENT_ENV_FILE:-$HOME/.config/marketUS/deepseek.env}"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

exec python3 Tools/dispatcher/dispatcher.py "$@"
