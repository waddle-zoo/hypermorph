"""No hosted curator or model credential is required to CREATE, SCORE, or APPROVE
the benchmark context, end to end (hy-8g4m, hy-bwo SS5, #25 scope 5).

The #25 headline is a credential-free claim: a customer can reconstitute the benchmark
context from the committed source, SCORE the recorded runs, and APPROVE them through the
provenance-completeness gate without any hosted key. The ONLY path that consumes a
credential is the opt-in FRONTIER arm, which FAILS CLOSED without one (`run.py`); nothing
on the create/score/approve path of the LOCAL benchmark touches it.

Proven two ways, extending the hy-quol source-closure work:
- a CLEAN SUBPROCESS whose environment carries no hosted credential reconstitutes a
  committed recording, scores it, and grades it complete -- and confirms the frontier
  boundary fail-closes in that same credential-free process;
- an IMPORT-CLOSURE guard: the scoring and approval modules cannot even REACH a model
  runtime or the credential-reading orchestration in their source closure, so the
  behaviour above cannot regress via a new import edge.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.evals.test_report_time_purity import import_closure

ROOT = Path(__file__).resolve().parents[3]

# Every hosted credential (and the frontier config that gates it). Stripped from the clean
# subprocess so create/score/approve run with nothing to authenticate against.
CREDENTIAL_ENV = (
    "HYPERSET_FRONTIER_API_KEY",
    "HYPERSET_FRONTIER_MODEL",
    "HYPERSET_FRONTIER_BASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)

# The credential-bearing / model-runtime code the SCORE and APPROVE closures must never
# reach: the hosted model client, the Agents SDK, the run orchestration that reads
# HYPERSET_FRONTIER_API_KEY, and a subprocess (which could shell out to one). Direct network
# egress (a socket/urllib call that authenticates without a named runtime) is the residual a
# fixed module-name ban cannot cover; the clean-subprocess test denies it BEHAVIOURALLY
# instead (an armed socket denial), which is why `urllib.parse`-for-ref-parsing stays allowed
# here.
CREDENTIAL_FORBIDDEN = (
    "hyperset.planner.openai_runtime",
    "agents",
    "hyperset.evals.run",
    "hyperset.evals.arms",
    "hyperset.evals.raw_arm",
    "subprocess",
)

# What a customer imports to reconstitute (create), SCORE, and APPROVE the committed
# benchmark context -- none may name a credential/model runtime in its source closure.
CREATE_SCORE_APPROVE = (
    "hyperset.evals.recording",
    "hyperset.evals.cases",
    "hyperset.evals.scorers",
    "hyperset.evals.provenance_completeness",
)

_CLEAN_SUBPROCESS_SCRIPT = """
import json, os, socket, sys

# 1. The process is genuinely credential-free.
_CRED = ("HYPERSET_FRONTIER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
LEAKED = [k for k in _CRED if os.environ.get(k)]
assert not LEAKED, f"a credential leaked into the clean subprocess: {LEAKED}"

# Import the whole lifecycle FIRST (import machinery legitimately subclasses socket.socket,
# e.g. ssl.SSLSocket), THEN deny egress before running it.
from hyperset.evals.provenance_completeness import grade_recording_completeness
from hyperset.evals.recording import GOVERNED_ARM, Recording
from hyperset.evals.report import score_recordings
from hyperset.evals.run import (
    FrontierArmNotConfigured,
    frontier_config_or_refuse,
    recordings_of,
)

# 2. DENY network egress for the lifecycle itself, and PROVE the denial is armed -- so a
# direct socket/DNS call on the create/score/approve path (one a fixed module-name closure
# ban would miss) reds this test.
def _deny_network(*a, **k):
    raise AssertionError("network egress attempted on the credential-free lifecycle")
socket.socket = _deny_network
socket.create_connection = _deny_network
socket.getaddrinfo = _deny_network
try:
    socket.getaddrinfo("example.invalid", 443)
except AssertionError:
    pass
else:
    raise AssertionError("the network denial did not fire -- the egress guard is not armed")

# 3. The ONLY credentialed path (the frontier arm) FAILS CLOSED without a key.
try:
    frontier_config_or_refuse()
except FrontierArmNotConfigured:
    pass
else:
    raise AssertionError("the frontier arm ran without a credential -- the boundary is open")

# 4. SCORE via the SHIPPED path: score_recordings -> _score_one reads, VERIFIES, suite-binds,
# and scores every committed recording. A credential requirement added to the real read/verify
# path reds this, which scoring a hand-built Recording directly would not.
report = score_recordings()
assert report["scored_a_recording"] is True, "the shipped score path scored nothing"
assert report["arms"], "the shipped score path reported no arms"
# The gate-green condition: nothing failed that was not already declared. Any critical
# governed failures are the DECLARED ones; a regression would surface here.
assert report["unexpected_failures"] == [], (
    "the shipped score path found unexpected failures: " + repr(report["unexpected_failures"])
)

# 5. CREATE + APPROVE via the shipped reader+verifier, then the completeness gate.
path = recordings_of(GOVERNED_ARM, "revenue_by_region")[0]
recording = Recording.read(path)   # shipped reader
recording.verify()                 # shipped verification (what _score_one runs before scoring)
grade = grade_recording_completeness(json.loads(path.read_text()))
assert grade.complete is True, "the committed governed recording must grade complete"

print("CREDFREE-OK")
"""


def test_create_score_approve_run_credential_free_end_to_end_in_a_clean_subprocess():
    """The whole lifecycle in a process that has no hosted credential to offer.

    A fresh interpreter, its environment stripped of every hosted credential, reconstitutes
    a committed recording, scores it, and grades it complete -- and the frontier boundary
    fail-closes in the same process. A `TimeoutExpired` (a hung import reaching for a
    network client) propagates as a failure rather than a skip.
    """
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV}
    for name in ("HYPERSET_FRONTIER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert name not in env, f"{name} was not stripped from the subprocess environment"

    result = subprocess.run(
        [sys.executable, "-c", _CLEAN_SUBPROCESS_SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, f"credential-free lifecycle failed:\n{result.stderr}"
    assert "CREDFREE-OK" in result.stdout, result.stdout


def test_the_frontier_arm_is_the_only_credentialed_path_and_fails_closed(monkeypatch):
    """The credential boundary is real and isolated: the ONLY thing that needs a hosted key
    is the frontier arm, and without one it refuses BEFORE any run -- so a credential-free
    create/score/approve of the local benchmark is never blocked by it."""
    from hyperset.evals.run import (
        FRONTIER_API_KEY_ENV,
        FRONTIER_MODEL_ENV,
        FrontierArmNotConfigured,
        frontier_config_or_refuse,
    )

    monkeypatch.delenv(FRONTIER_API_KEY_ENV, raising=False)
    monkeypatch.delenv(FRONTIER_MODEL_ENV, raising=False)
    with pytest.raises(FrontierArmNotConfigured):
        frontier_config_or_refuse()


def test_score_and_approve_source_closure_names_no_credential_or_model_runtime():
    """Structural guard (extends hy-quol): the create/score/approve modules a customer
    imports cannot REACH a hosted model runtime, the Agents SDK, the credential-reading run
    orchestration, or a subprocess in their SOURCE closure -- so the credential-free
    behaviour above cannot regress via a one-line import edge."""
    dirty: dict[str, list[str]] = {}
    for start in CREATE_SCORE_APPROVE:
        closure = import_closure(start)
        reachable = sorted(
            name
            for name in closure
            for bad in CREDENTIAL_FORBIDDEN
            if name == bad or name.startswith(bad + ".")
        )
        if reachable:
            dirty[start] = reachable

    assert not dirty, (
        "the create/score/approve source closure must not name a credential or model "
        f"runtime, but reached: {dirty}"
    )
    # And the guard measures the closure it thinks it does.
    assert "hyperset.evals.scorers" in import_closure("hyperset.evals.scorers")
