#!/usr/bin/env bash
# UNINSTALLED LEGACY, manual-only contributor helper. No background scheduler
# or LaunchAgent is installed or supported by this repository.
# Pings Claude Code with "ping" every run, expects "pong" back.
# Purpose: keep the Claude Code 5-hour usage window warmed on a predictable
# cadence, so its reset time stays under our control instead of landing at
# whatever inconvenient hour real work happens to start it.
set -euo pipefail

MODEL="${CLAUDE_PING_MODEL:-claude-haiku-4-5-20251001}"
LOG_FILE="${CLAUDE_PING_LOG:-$HOME/.claude-ping.log}"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

output="$(claude -p "ping" --model "$MODEL" 2>&1)" || {
  echo "$(timestamp) FAIL (claude exited non-zero): $output" >> "$LOG_FILE"
  exit 0
}

if grep -qi "pong" <<< "$output"; then
  echo "$(timestamp) OK: $output" >> "$LOG_FILE"
else
  echo "$(timestamp) UNEXPECTED: $output" >> "$LOG_FILE"
fi
