"""Live Superset REST sync against the real pinned instance (hy-gh-27 Gate A).

Opt-in: needs an already-running demo stack (`make up-demo`) and
`HYPERSET_COMPOSE_DEMO=1`. Booting the pinned Superset takes 2-3 minutes, so
`#36` owns wiring this into a scheduled/pre-release job rather than every
push -- same reasoning as `tests/compose/test_core_stack.py`'s core-only
scope.

Everything runs through the production path: the packaged CLI inside the
compose network, against the compose Postgres and the pinned Superset. Unlike
`tests/postgres/test_superset_rest_sync.py` (recorded payloads, no network),
this proves the real transport: JWT login, list/detail pagination, and the
identities the pinned seed actually serves.

The test is additive and restores the baseline metric expression it drifts. It
never tears the stack down.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.compose.conftest import require_healthy_service

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads(
    (REPO_ROOT / "tests/fixtures/superset/6.1.0/revenue/manifest.json").read_text()
)
USAGE_MANIFEST = json.loads(
    (REPO_ROOT / "tests/fixtures/superset/6.1.0/usage/manifest.json").read_text()
)
# Live REST covers all four asset types since hy-rt4v, so the count this file
# expects now depends on which seeds the instance has had. The `usage_seeded`
# fixture applies the second one rather than leaving the total to whatever a
# developer happened to run: a test that accepted either total would stop
# noticing a type the connector failed to read, which is the defect hy-rt4v
# fixed.
EXPECTED_ASSET_COUNT = (
    1
    + len(MANIFEST["source_contract"]["datasets"])
    + len(USAGE_MANIFEST["source_contract"]["charts"])
    + len(USAGE_MANIFEST["source_contract"]["dashboards"])
)


def _compose(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _cli(env: dict, *args: str) -> str:
    result = _compose(
        "run",
        "--rm",
        "-e",
        f"HYPERSET_SUPERSET_USERNAME={env['SUPERSET_ADMIN_USERNAME']}",
        "-e",
        f"HYPERSET_SUPERSET_PASSWORD={env['SUPERSET_ADMIN_PASSWORD']}",
        "hyperset-migrate",
        *args,
        env=env,
    )
    assert result.returncode == 0, f"hyperset {' '.join(args)} failed:\n{result.stderr}"
    return result.stdout


def _counters(output: str) -> dict:
    match = re.search(r"counters=(\{[^}]*\})", output)
    assert match, f"no counters in sync output:\n{output}"
    return json.loads(match.group(1).replace("'", '"'))


def _persisted_changes(output: str) -> int:
    """How many `connector_changes` rows the run actually wrote -- the CLI
    reads them back from Postgres, so this is persisted state, not a tally."""
    match = re.search(r"changes=(\d+)", output)
    assert match, f"no change count in sync output:\n{output}"
    return int(match.group(1))


@pytest.fixture(scope="module")
def demo_env():
    if os.environ.get("HYPERSET_COMPOSE_DEMO") != "1":
        pytest.skip("set HYPERSET_COMPOSE_DEMO=1 with a running `make up-demo` stack")

    env = os.environ.copy()
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())
    env.setdefault("SUPERSET_ADMIN_USERNAME", "admin")

    # The demo services sit behind a compose profile, so a plain `ps` hides
    # them even while they are running.
    listing = _compose("--profile", "demo", "ps", "--format", "json", env=env)
    require_healthy_service(
        listing,
        "superset",
        "the pinned demo Superset is not healthy; start it with `make up-demo`",
    )
    return env


@pytest.fixture(scope="module")
def usage_seeded(demo_env):
    """Charts and one dashboard on the instance, so the two types hy-rt4v
    added to live coverage are proven against something rather than against an
    empty collection.

    `--no-deps` for the reason `demo-drift-apply` uses it: this service
    depends on `superset-demo-bootstrap`, and re-running that rewrites every
    seeded dataset. The usage seed itself is idempotent and creates no
    dataset, so a stack that has already had it is left exactly as it was.
    """
    result = _compose(
        "--profile", "demo", "run", "--rm", "--no-deps", "superset-usage-bootstrap", env=demo_env
    )
    assert result.returncode == 0, f"usage bootstrap failed:\n{result.stderr}"
    return result.stdout


@pytest.fixture(scope="module")
def live_connection_id(demo_env, usage_seeded):
    _cli(demo_env, "db", "upgrade")
    output = _cli(
        demo_env, "connections", "create-superset-rest", "--base-url", "http://superset:8088"
    )
    return output.strip().splitlines()[-1]


@pytest.mark.compose
def test_live_rest_sync_observes_the_canonical_revenue_assets(demo_env, live_connection_id):
    output = _cli(demo_env, "sync", "run", live_connection_id)

    assert "transport=rest" in output
    # The pinned build discloses no application version over REST, and the
    # sync says so instead of guessing one.
    assert "source_version=None" in output
    assert "does not disclose its application version" in output
    # All four types, from the real instance (hy-rt4v): the count includes the
    # seeded charts and dashboard, and the gap the connector used to disclose
    # is absent rather than reworded.
    assert "were not read" not in output
    assert "chart detail bodies disclose no `changed_on`" in output
    counters = _counters(output)
    assert counters["created"] == EXPECTED_ASSET_COUNT
    assert counters["deleted"] == 0
    # Every first observation is announced as one persisted ConnectorChange.
    assert _persisted_changes(output) == EXPECTED_ASSET_COUNT


@pytest.mark.compose
def test_live_resync_of_an_unchanged_instance_is_a_no_op(demo_env, live_connection_id):
    output = _cli(demo_env, "sync", "run", live_connection_id)

    assert _counters(output) == {
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": EXPECTED_ASSET_COUNT,
        "deleted": 0,
    }
    assert _persisted_changes(output) == 0


@pytest.mark.compose
def test_live_controlled_drift_produces_exactly_one_change(demo_env, live_connection_id):
    def drift(action: str) -> None:
        # --no-deps: the demo-evidence service depends on the bootstrap, and
        # re-running it would rewrite every dataset, so the run would no
        # longer be one controlled change.
        result = _compose(
            "--profile", "demo", "run", "--rm", "--no-deps", "demo-evidence", action, env=demo_env
        )
        assert result.returncode == 0, result.stderr

    drift("apply")
    try:
        output = _cli(demo_env, "sync", "run", live_connection_id)
        counters = _counters(output)
        assert counters["updated"] == 1
        assert counters["unchanged"] == EXPECTED_ASSET_COUNT - 1
        assert counters["deleted"] == 0
        # One controlled source edit, one immutable version, one persisted
        # ConnectorChange -- nothing else in the instance is announced.
        assert _persisted_changes(output) == 1
    finally:
        drift("restore")

    # Restoring is itself a source edit, so it is observed as one more
    # immutable version of the same asset family -- not a rollback.
    restored_output = _cli(demo_env, "sync", "run", live_connection_id)
    assert _counters(restored_output)["updated"] == 1
    assert _persisted_changes(restored_output) == 1
    assert _counters(restored_output)["created"] == 0
