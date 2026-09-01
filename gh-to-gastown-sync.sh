#!/usr/bin/env bash
# Manual operator compatibility shim for scripts/gh-to-gastown-sync.sh
# (hy-gh-99). Retained for old command paths; it is not installed or scheduled
# by this repository.
#
# A shim rather than a symlink on purpose: the real scripts resolve their own
# directory with ${BASH_SOURCE[0]} to find the repository root, and a symlink
# would report THIS path, sending them one level too high. `exec` also keeps
# one process and preserves the called script's repository-root resolution.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/gh-to-gastown-sync.sh" "$@"
