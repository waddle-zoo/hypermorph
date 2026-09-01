"""Every published host port is ephemeral, and two concurrent runs never overlap.

Unique `COMPOSE_PROJECT_NAME`s (hy-xjch) separate two worktrees' networks but NOT their
HOST PORTS: a fixed `ports:` host binding collides between parallel suites regardless of
project name -- the partial-namespace lesson, and the residual hy-k9yw left (hy-l09x9).
The fixture forces every published host port to `0` (OS-assigned ephemeral, distinct per
run, read back with `docker compose port`).

Two properties, and the second is the load-bearing one (hy-l09x9 adversary): the fixture
maps every published port to `0`, AND two concurrent runs resolve DISJOINT host ports.
The disjointness is exercised against the real allocation mechanism -- a `0` host port is
handed a free port by the kernel, and the kernel keeps two concurrently-held bindings
apart -- rather than asserted in prose.

No Docker: the compose file's `ports:` blocks and the fixture env are readable without a
daemon, and the ephemeral allocation is the OS's, reproduced here by binding sockets. It
lives under tests/unit for the sibling-guard reason -- the compose suite skips itself
wherever Docker is absent, so a check beside it would be skipped on exactly the seats a
regression could land on.
"""

from __future__ import annotations

import re
import socket

from tests.compose.conftest import REPO_ROOT, compose_environment

# The single `${VAR}` or `${VAR:-default}` a ports entry uses for its host port. The
# `:-default` carries a colon and cannot be split on ':' -- it is matched, not split.
# Group 1 is the variable, group 2 the compose default (what `${VAR:-default}` yields
# when VAR is unset), which is the value a run would bind if the fixture stopped forcing 0.
_HOST_PORT_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _published_host_port_vars() -> list[tuple[str, str, str | None]]:
    """`(service, host-port env var, compose default)` for every published `ports:` entry.

    Read off docker-compose.yml rather than enumerated here, so a service that adds a
    published port is covered the moment it lands -- the whole point of the guard.
    """
    import yaml

    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    found: list[tuple[str, str, str | None]] = []
    for service, spec in (compose.get("services") or {}).items():
        for entry in (spec or {}).get("ports", []) or []:
            matches = _HOST_PORT_VAR.findall(str(entry))
            assert len(matches) == 1, (
                f"{service} ports entry {entry!r} has {len(matches)} ${{VAR}}, expected exactly "
                "one host-port variable; a hardcoded host port cannot be made ephemeral and "
                "would collide between parallel runs"
            )
            var, default = matches[0]
            found.append((service, var, default or None))
    return found


def _resolve_run_ports(env: dict[str, str]) -> tuple[set[int], list[socket.socket]]:
    """The host ports one run would publish, resolving `0` to an OS-assigned ephemeral port.

    Returns the port set and the OPEN sockets that reserve the ephemeral ones. The caller
    keeps them open across BOTH runs, so the kernel cannot hand the same ephemeral port to
    the second run -- exactly the guarantee docker leans on when two stacks each publish a
    `0` host port. A fixed (non-`0`) mapping resolves to that literal port, which is how a
    reverted service makes two runs collide.
    """
    ports: set[int] = set()
    held: list[socket.socket] = []
    for _service, var, default in _published_host_port_vars():
        raw = env.get(var, default)
        if raw == "0":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            held.append(sock)
            ports.add(sock.getsockname()[1])
        else:
            assert raw is not None, f"{var} is neither forced to 0 nor given a compose default"
            ports.add(int(raw))
    return ports, held


def test_two_concurrent_runs_publish_disjoint_host_ports():
    """The concurrency proof: two runs never share a published host port (hy-l09x9).

    This is what "parallel worktree suites do not collide" actually means, and prose in
    the fixture is not it. Two runs each resolve their published ports through the real
    ephemeral mechanism while BOTH holds are open, and the two sets must be disjoint.

    Reverting any service to a fixed host port -- e.g. dropping it from the fixture's force
    loop so it falls back to its compose default -- makes both runs resolve that one port
    identically, the sets intersect, and this reddens. A single-mapping check cannot see
    that, because one run's mapping looks fine in isolation.
    """
    run_a, held_a = _resolve_run_ports(compose_environment())
    try:
        run_b, held_b = _resolve_run_ports(compose_environment())
        try:
            assert run_a and run_b, "each run must publish at least one host port"
            assert run_a.isdisjoint(run_b), (
                f"two concurrent runs published overlapping host ports {sorted(run_a & run_b)}; "
                "a fixed host port collides between parallel worktree suites"
            )
        finally:
            for sock in held_b:
                sock.close()
    finally:
        for sock in held_a:
            sock.close()


def test_every_published_host_port_is_ephemeral_under_the_fixture():
    """The mapping half: the fixture sets every published host port to 0 (ephemeral)."""
    published = _published_host_port_vars()
    assert published, "the compose stack publishes host ports; the guard must actually see them"

    env = compose_environment()
    for service, var, _default in published:
        assert env.get(var) == "0", (
            f"service {service!r} publishes host port ${{{var}}}, which the fixture must set to 0 "
            f"(ephemeral) so parallel worktree suites never collide on it; got {env.get(var)!r}"
        )


def test_the_fixture_forces_ephemeral_over_an_inherited_fixed_port(monkeypatch):
    """The fixture must FORCE 0, not merely default to it (hy-l09x9).

    A developer's `.env` sets these host ports to fixed values (55432, 8088, 8000, 8090)
    for `make up-demo`, and pytest inherits the process environment. A `setdefault` would
    let an inherited fixed port pass straight through, and two worktree suites would bind
    it and collide -- the exact isolation this closes. So the fixture overrides the
    inherited value; this plants one to prove it, and reddens on a revert to `setdefault`.
    """
    published = _published_host_port_vars()
    for _service, var, _default in published:
        monkeypatch.setenv(var, "59999")

    env = compose_environment()

    for service, var, _default in published:
        assert env[var] == "0", (
            f"service {service!r} host port ${{{var}}} must be forced to ephemeral even when the "
            f"process inherits a fixed {var}=59999; got {env[var]!r} (a setdefault would leak it)"
        )


def test_the_guard_reads_real_published_ports_not_an_empty_set():
    """The control on the guards above.

    An empty `published` -- a parse that found nothing, or a compose file that stopped
    publishing ports -- would make the assertions vacuously true. Pin that the stack does
    publish the host ports this bead is about, so the guard cannot pass by seeing nothing.
    """
    services = {service for service, _var, _default in _published_host_port_vars()}
    assert {"postgres", "api", "mcp-http"} <= services, (
        f"the api and mcp-http host ports the suite brings up must be among those seen: {services}"
    )
