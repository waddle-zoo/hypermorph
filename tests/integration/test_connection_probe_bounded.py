"""The live connection probe is BOUNDED end-to-end: a real source that accepts the TCP
connection then stalls must be cut at the probe's explicit deadline, not hang the request
~60s on the connector's 30s SYNC default (hq-hnrf area 2).

This drives the WHOLE real chain -- probe_connection -> build_connector -> SupersetConnector
-> SupersetRestClient -> a real socket -- against a listening-but-never-answering server, so
it proves the threaded timeout actually cuts a hanging read (the plumbing half, that the probe
passes the value, is the unit test test_connection_probe.py). No external network: the stall
server is a local socket that completes the TCP handshake (kernel accept queue) and then never
replies, so the login POST blocks on read until the deadline.
"""

from __future__ import annotations

import socket
import time
from types import SimpleNamespace

from hyperset.ops import connection_probe
from hyperset.ops.connection_probe import probe_connection


def test_a_stalling_source_is_cut_at_the_probe_deadline_and_does_not_hang(monkeypatch):
    # A short bound so the test is fast; the shipped value (3.0) is asserted by the unit test.
    monkeypatch.setattr(connection_probe, "CONNECTION_PROBE_TIMEOUT", 0.5)
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "u")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "p")

    # Listen but never accept: the kernel completes the handshake into the backlog, the login
    # POST sends its bytes, then blocks on the response read until the deadline fires.
    stall = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stall.bind(("127.0.0.1", 0))
    stall.listen(1)
    try:
        host, port = stall.getsockname()
        record = SimpleNamespace(
            id="c1",
            connector_type="superset",
            display_name="Prod",
            config_ref=f"http://{host}:{port}",
        )

        started = time.monotonic()
        probe = probe_connection(record, latest_finished_run=None)
        elapsed = time.monotonic() - started

        # Cut fast: honest blocked/unreachable within a small multiple of the deadline, never the
        # ~60s the 30s sync default would have allowed for two sequential stalling calls.
        assert probe.status == "blocked"
        assert probe.reachable is False
        assert probe.configured is True
        assert elapsed < 5, f"probe hung {elapsed:.1f}s -- the deadline did not bound the read"
    finally:
        stall.close()
