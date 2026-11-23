#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

mkdir -p data/logs || exit 1
log="data/logs/daily-$(date +%F).log"

run_step() {
    local name=$1
    shift
    echo "$(date -Iseconds) start $name" >> "$log"
    "$@" >> "$log" 2>&1
    local status=$?
    echo "$(date -Iseconds) end $name exit=$status" >> "$log"
    return "$status"
}

run_step crawl uv run jobhunt crawl || exit $?
run_step apply uv run jobhunt apply || exit $?
