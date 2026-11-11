#!/usr/bin/env bash
# Launchd runner: tolerate transient git failures; never abort the sweep on pull errors.
# A failed pull leaves an observable .agent/state/degraded.json and a macOS notification.
set -uo pipefail

RUNNER="${MARKETUS_RUNNER:-$HOME/Library/Application Support/MarketUS/runner}"
cd "$RUNNER" || { echo "runner missing: $RUNNER" >&2; exit 1; }

DEGRADED_FILE=".agent/state/degraded.json"

notify() {
    [[ "${MARKETUS_NOTIFY:-1}" == "0" ]] && return 0
    [[ "$(uname -s)" == "Darwin" ]] || return 0
    if command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier -title "$1" -message "$2" -sound Glass -group marketus-dispatcher >/dev/null 2>&1 || true
    elif command -v osascript >/dev/null 2>&1; then
        osascript -e 'on run argv' \
            -e 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"' \
            -e 'end run' -- "$1" "$2" >/dev/null 2>&1 || true
    fi
}

PULL_FAILED=1
for attempt in 1 2 3; do
    if git pull --ff-only --quiet; then
        PULL_FAILED=0
        break
    fi
    echo "[$(date -u +%FT%TZ)] git pull failed (attempt $attempt/3); retrying" >&2
    sleep $((attempt * 10))
done

if [[ "$PULL_FAILED" -ne 0 ]]; then
    mkdir -p "$(dirname "$DEGRADED_FILE")"
    cat > "$DEGRADED_FILE" <<EOF
{
  "degraded": true,
  "at": "$(date -u +%FT%TZ)",
  "attempts": 3,
  "reason": "git pull failed after 3 attempts"
}
EOF
    echo "[$(date -u +%FT%TZ)] runner degraded: git pull failed after 3 attempts" >&2
    notify "MarketUS runner degraded" "git pull failed after 3 attempts; continuing with the local checkout"
else
    rm -f "$DEGRADED_FILE"
fi

ENV_FILE="${AGENT_ENV_FILE:-$HOME/.config/marketUS/deepseek.env}"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

exec python3 Tools/dispatcher/dispatcher.py "$@"
