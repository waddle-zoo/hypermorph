#!/usr/bin/env bash
# One-shot bootstrap for the `superset-init` service: assert the pinned
# version, then run Superset's own official init sequence (db upgrade +
# admin creation + roles/perms -- /app/docker/docker-init.sh,
# upstream-supported, not reimplemented here).
#
# Demo dataset bootstrap (demo_bootstrap.py) is a SEPARATE service
# (`superset-demo-bootstrap`), not run here: it talks to Superset's REST
# API, which only exists once the `superset` webserver is actually
# running -- and `superset` itself depends on *this* script completing
# first (migrations must exist before the webserver starts). Bundling
# both into one script would be a circular dependency.
set -euo pipefail

/hyperset-demo/assert_version.sh
/app/docker/docker-init.sh
