# Claude Code usage-window keepalive

> [!WARNING]
> **Status: uninstalled legacy helper.** No matching plist existed under
> `~/Library/LaunchAgents` when checked on 2026-08-30. This repository installs
> and supports no background keepalive job; the script is retained only for a
> manual one-off invocation.

Development infrastructure for contributors using Claude Code on this
repo — not part of the Hyperset product. See [`README.md`](../../README.md)
for the product itself.

`scripts/claude-ping.sh` pings Claude Code (`claude -p "ping" --model <haiku>`) hourly
and expects "pong" back. The point isn't the ping itself — Claude Code's
5-hour usage window starts on your first message of the day and resets
exactly 5 hours later, regardless of activity in between. Left alone, that
start time is whatever moment you happen to first use it, which can land the
reset at an inconvenient hour if a long task blows the budget shortly after.
Pinging on a predictable hourly cadence keeps a window (near-)permanently
running, so the reset boundary stays under your control instead of up to 5
hours away at the worst possible time.

This only affects Claude Code's own Pro/Max session window — it is
unrelated to raw Anthropic API usage (pay-per-token, no reset window), so
the ping must go through the `claude` CLI itself, not a direct API call.

### Automated scheduling: removed

This repo no longer ships a launchd template for this script. Run
`./scripts/claude-ping.sh` by hand if you want a one-off keepalive ping;
there is no standing background job.
