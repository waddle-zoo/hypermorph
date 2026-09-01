"""Who can reach this server when nobody says (hy-voes).

The default was `0.0.0.0`, so `hyperset serve http` with no flag published an
unauthenticated read API on every interface the host happens to be on. The one
deployment that genuinely needs all interfaces -- the `api` service -- already
passes `--host 0.0.0.0` explicitly, so nothing depended on the default being
wide.

TWO GUARDS, and the second is the one that matters. The first pins today's
default. The second pins the compose flag, because a later cleanup reading
"serve http --host 0.0.0.0 --port 8080" beside a server that now defaults to
loopback will see a redundant argument and delete it -- silently republishing
the API to the LAN. The person who does that is reading the compose file, not
this module, which is why the comment lives there and the check lives here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from hyperset.cli import build_parser
from hyperset.transport.http import DEFAULT_HOST

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"


def test_the_server_defaults_to_loopback():
    assert DEFAULT_HOST == "127.0.0.1"


def test_serve_http_binds_loopback_when_nobody_says_otherwise():
    """Read off the parser rather than the constant: the CLI is what a person
    on a laptop actually invokes, and a default can be overridden at the
    argument without the constant moving."""
    args = build_parser().parse_args(["serve", "http"])

    assert args.host == "127.0.0.1"


def test_the_api_container_still_asks_for_every_interface_explicitly():
    """A container binding loopback inside its own namespace answers nothing on
    its published port, so the `--host 0.0.0.0` flag must stay written down. But the
    PUBLISH is host-loopback only (`127.0.0.1:`, hy-w5ld Option A): the container binds
    every interface INSIDE its namespace while the host forwards only from loopback, so
    the demo is reachable from this host but not the LAN."""
    api = yaml.safe_load(COMPOSE.read_text())["services"]["api"]

    assert api["command"] == ["serve", "http", "--host", "0.0.0.0", "--port", "8080"]
    assert api["ports"] == ["127.0.0.1:${HYPERSET_API_PORT:-8000}:8080"]
