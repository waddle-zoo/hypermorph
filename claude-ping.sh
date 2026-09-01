#!/usr/bin/env bash
# UNINSTALLED LEGACY shim for scripts/claude-ping.sh (hy-gh-99). Retained only
# for old manual invocations. A 2026-08-30 inspection found no matching plist
# under ~/Library/LaunchAgents; this repository installs or supports no
# background keepalive job.
#
# A shim rather than a symlink on purpose: the real scripts resolve their own
# directory with ${BASH_SOURCE[0]} to find the repository root, and a symlink
# would report THIS path, sending them one level too high. `exec` also keeps
# one process and preserves the called script's repository-root resolution.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/claude-ping.sh" "$@"
