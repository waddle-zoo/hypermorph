"""Live DataHub GraphQL sync against the real pinned instance (hy-gh-17).

Opt-in: needs an already-running stack (`make up-datahub`) and
`HYPERSET_COMPOSE_DATAHUB=1`. GMS boots behind its SystemUpdate job in ~2
minutes, so `#36` owns wiring this into a scheduled/pre-release job rather
than every push -- same reasoning as `tests/compose/test_superset_live_sync.py`.

Everything runs through the production path: the packaged CLI inside the
compose network, against the compose Postgres and the pinned GMS. Unlike
`tests/postgres/test_datahub_sync.py` (recorded payloads, no network), this
proves the real transport: scroll discovery, per-URN projections, and the
URNs the pinned seed actually serves.

The test is additive and restores the glossary definition it drifts. It never
tears the stack down.
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
    (REPO_ROOT / "tests/fixtures/datahub/v1.6.0/revenue/baseline/manifest.json").read_text()
)
PINNED_VERSION = MANIFEST["pinned_version"]
URNS = MANIFEST["urns_by_asset_type"]
EXPECTED_ASSET_COUNT = sum(len(urns) for urns in URNS.values())
TERM_URN = URNS["glossary_term"][0]


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
    result = _compose("run", "--rm", "hyperset-migrate", *args, env=env)
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
def datahub_env():
    if os.environ.get("HYPERSET_COMPOSE_DATAHUB") != "1":
        pytest.skip("set HYPERSET_COMPOSE_DATAHUB=1 with a running `make up-datahub` stack")

    env = os.environ.copy()
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())

    # The DataHub services sit behind a compose profile, so a plain `ps`
    # hides them even while they are running.
    listing = _compose("--profile", "datahub", "ps", "--format", "json", env=env)
    require_healthy_service(
        listing,
        "datahub-gms",
        "the pinned DataHub GMS is not healthy; start it with `make up-datahub`",
    )
    return env


@pytest.fixture(scope="module")
def live_connection_id(datahub_env):
    _cli(datahub_env, "db", "upgrade")
    output = _cli(
        datahub_env, "connections", "create-datahub", "--base-url", "http://datahub-gms:8080"
    )
    return output.strip().splitlines()[-1]


@pytest.mark.compose
def test_live_graphql_sync_observes_the_canonical_revenue_evidence(datahub_env, live_connection_id):
    output = _cli(datahub_env, "sync", "run", live_connection_id)

    assert "transport=graphql" in output
    # Unlike Superset, DataHub discloses its application version, so the
    # pinned build is asserted from the source itself.
    assert f"source_version={PINNED_VERSION}" in output
    counters = _counters(output)
    assert counters["created"] == EXPECTED_ASSET_COUNT
    assert counters["deleted"] == 0
    # Every first observation is announced as one persisted ConnectorChange.
    assert _persisted_changes(output) == EXPECTED_ASSET_COUNT
    # The projection is disclosed rather than implied.
    assert "lossless with respect to this connector's projections" in output
    assert "cross-source mapping is not performed here" in output


@pytest.mark.compose
def test_live_resync_of_an_unchanged_instance_is_a_no_op(datahub_env, live_connection_id):
    output = _cli(datahub_env, "sync", "run", live_connection_id)

    assert _counters(output) == {
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": EXPECTED_ASSET_COUNT,
        "deleted": 0,
    }
    assert _persisted_changes(output) == 0


@pytest.mark.compose
def test_live_controlled_drift_produces_exactly_one_change(datahub_env, live_connection_id):
    def drift(action: str) -> None:
        # --no-deps: datahub-seed depends on GMS being healthy, and re-running
        # the seed itself would rewrite every aspect, so the run would no
        # longer be one controlled change.
        result = _compose(
            "--profile",
            "datahub",
            "run",
            "--rm",
            "--no-deps",
            "datahub-seed",
            action,
            env=datahub_env,
        )
        assert result.returncode == 0, result.stderr

    drift("apply")
    try:
        output = _cli(datahub_env, "sync", "run", live_connection_id)
        counters = _counters(output)
        assert counters["updated"] == 1
        assert counters["unchanged"] == EXPECTED_ASSET_COUNT - 1
        assert counters["deleted"] == 0
        # One controlled source edit, one immutable version, one persisted
        # ConnectorChange -- the datasets that reference the term by URN are
        # not restated.
        assert _persisted_changes(output) == 1
    finally:
        drift("restore")

    # Restoring is itself a source edit, so it is observed as one more
    # immutable version of the same asset family -- not a rollback.
    restored_output = _cli(datahub_env, "sync", "run", live_connection_id)
    assert _counters(restored_output)["updated"] == 1
    assert _persisted_changes(restored_output) == 1
    assert _counters(restored_output)["created"] == 0


@pytest.mark.compose
def test_live_checkpoint_and_identity_survive_a_gms_restart(datahub_env, live_connection_id):
    before = _cli(datahub_env, "sync", "run", live_connection_id)
    assert _counters(before)["created"] == 0

    restart = _compose("--profile", "datahub", "restart", "datahub-gms", env=datahub_env)
    assert restart.returncode == 0, restart.stderr
    wait = _compose("--profile", "datahub", "up", "-d", "--wait", "datahub-gms", env=datahub_env)
    assert wait.returncode == 0, wait.stderr

    after = _cli(datahub_env, "sync", "run", live_connection_id)
    # Same URNs, same identities, nothing created and nothing deleted: the
    # checkpoint and the observed assets both survived the restart.
    assert _counters(after) == {
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": EXPECTED_ASSET_COUNT,
        "deleted": 0,
    }
    assert _persisted_changes(after) == 0
