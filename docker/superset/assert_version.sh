#!/usr/bin/env bash
# Fails startup clearly if the running Superset image is not the exact
# pinned contract target (hy-gh-37 "Startup must fail clearly if the
# running Superset version differs from the pinned contract target").
# Reads the image's own build-time version_info.json rather than trusting
# an env var alone -- that file can't drift from what's actually running.
set -euo pipefail

EXPECTED="${SUPERSET_PINNED_VERSION:?SUPERSET_PINNED_VERSION not set}"
VERSION_FILE="/app/superset/static/version_info.json"

if [ ! -f "$VERSION_FILE" ]; then
    echo "FATAL: $VERSION_FILE not found -- cannot assert Superset version" >&2
    exit 1
fi

ACTUAL=$(python3 -c "import json; print(json.load(open('$VERSION_FILE'))['version'])")

if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "FATAL: running Superset version '$ACTUAL' does not match pinned contract target '$EXPECTED'." >&2
    echo "The connector contract suite (#27) is only valid against the pinned version. Refusing to proceed." >&2
    exit 1
fi

echo "Superset version assertion passed: $ACTUAL"
