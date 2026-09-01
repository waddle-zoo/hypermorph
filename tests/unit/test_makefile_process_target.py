"""`make process` runs the real offline processor, not the old `#38 blocked` stub (hy-jp0gq).

The generic target used to `echo '#38 blocked'; exit 1` because it had no source for
WHICH sync run to process. The real CLI `process sync <sync_run_id>` shipped (hy-1jgw6),
and #506's up-demo captures a specific sync_run_id and calls it directly. This binds the
generic operator target to a deterministic source -- `sync latest`, the most-recent
completed sync run across connections -- and pins that it no longer hard-exits, so the
`status` surface and `make process` cannot drift back to claiming the processor is blocked.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import hyperset.cli

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text()


def _target_body(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\n(?P<body>(?:\t.*\n?)+)", MAKEFILE, re.MULTILINE)
    assert match, f"a `{name}` target must exist"
    return match.group("body")


def test_sync_latest_is_a_cli_command():
    parser = hyperset.cli.build_parser()
    args = parser.parse_args(["sync", "latest"])
    assert args.func.__name__ == "cmd_sync_latest"


def test_process_target_runs_the_real_processor_over_the_latest_sync():
    body = _target_body("process")
    # The old stub is gone: no `#38 blocked`, no bare `exit 1` refusal.
    assert "#38" not in body
    assert "exit 1" not in body
    # It resolves a deterministic sync run and runs the real processor over it.
    assert "sync latest" in body
    assert "process sync" in body


def test_status_no_longer_claims_the_processor_is_blocked():
    body = _target_body("status")
    # `process` is runnable now; the status surface must not still list it under #38.
    assert not re.search(r"process(or)?\s*--\s*#38", body)
    assert "make process" in body


# A fake `docker` on PATH: `sync latest` behaves per $SCENARIO (a lookup FAILURE,
# a clean-but-empty result, or a real id), and `process sync <id>` records that it
# ran. The real `make process` recipe runs against it, so this exercises the actual
# shell idiom, not a paraphrase of it (critic/adversary #508 reproduced the bug the
# same way).
_DOCKER_SHIM = """#!/bin/sh
args="$*"
case "$args" in
  *"sync latest"*)
    case "$SCENARIO" in
      fail)  echo "FATAL: could not connect to database" >&2; exit 2 ;;
      empty) exit 0 ;;
      hasrun) echo "sync-run-xyz"; exit 0 ;;
    esac ;;
  *"process sync"*)
    echo "RAN_PROCESS_SYNC $args"; exit 0 ;;
esac
exit 0
"""


def _run_make_process(scenario: str, tmp_path) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "docker"
    shim.write_text(_DOCKER_SHIM)
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["SCENARIO"] = scenario
    return subprocess.run(["make", "process"], cwd=ROOT, env=env, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not on PATH")
def test_a_failed_sync_latest_lookup_makes_process_exit_nonzero(tmp_path):
    # The bug this bounce fixed: a lookup FAILURE (nonzero, empty stdout) must NOT
    # be mistaken for a benign 'no completed sync' no-op. It exits nonzero and says
    # the lookup failed -- never the healthy-looking exit 0.
    result = _run_make_process("fail", tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "could not look up the latest sync run" in result.stderr
    assert "no completed sync run yet" not in result.stdout
    assert "RAN_PROCESS_SYNC" not in result.stdout  # never processes on a failed lookup


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not on PATH")
def test_a_clean_empty_sync_latest_is_a_noop_success(tmp_path):
    result = _run_make_process("empty", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no completed sync run yet" in result.stdout
    assert "RAN_PROCESS_SYNC" not in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not on PATH")
def test_a_real_sync_run_is_processed(tmp_path):
    result = _run_make_process("hasrun", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "processing latest completed sync run sync-run-xyz" in result.stdout
    # The real processor is invoked over exactly that id.
    assert "RAN_PROCESS_SYNC" in result.stdout and "process sync sync-run-xyz" in result.stdout
