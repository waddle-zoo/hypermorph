#!/usr/bin/env bash
# Supported repo-local compatibility shim for scripts/gastown-agent.sh
# (hy-gh-99). This is a manual agent-profile setup command, not a daemon or
# LaunchAgent.
#
# A shim rather than a symlink on purpose: the real scripts resolve their own
# directory with ${BASH_SOURCE[0]} to find the repository root, and a symlink
# would report THIS path, sending them one level too high. `exec` also keeps
# one process and preserves the called script's repository-root resolution.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/gastown-agent.sh" "$@"
