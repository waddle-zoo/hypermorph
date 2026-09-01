"""The live local arms, which need a real Ollama and a real database (#25).

Skipped unless both are present -- except where the caller says the arms MUST
run, which is the scheduled workflow. A skip is exit 0, and a benchmark that
quietly passes when nothing ran is the failure ADR 0013's split gate is most
exposed to: the required gate scores recordings, and this is the only thing
that checks a live model still passes. Measured against an unreachable server,
`pytest tests/evals` was `4 skipped`, exit 0 -- indistinguishable from a real
run in a workflow summary (hy-26nd).

Reuses `tests/postgres`'s container and the revenue slice rather than building
a second fixture. The arms must run against the same governed slice the rest of
the suite asserts against, or the benchmark measures a corpus nothing else
checks.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from hyperset.evals.run import DEFAULT_BASE_URL
from hyperset.planner.ollama import ollama_root

REQUIRE_LIVE = "HYPERSET_REQUIRE_LIVE"
"""Set by the scheduled workflow. Live benchmark arms are explicitly opt-in.

The release gate must never discover a locally running legacy benchmark server
and silently route a normal test run through it. A scheduled job that owns the
Ollama benchmark sets this flag deliberately; ordinary product and CI runs do
not execute this legacy benchmark surface.
"""

_SUITE = "tests/evals/"

_completed: list[str] = []


def _ollama_is_up(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(ollama_root(base_url) + "/api/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def base_url() -> str:
    if os.environ.get(REQUIRE_LIVE) != "1":
        pytest.skip(f"live benchmark arms are opt-in; set {REQUIRE_LIVE}=1 to run them")
    url = os.environ.get("HYPERSET_OLLAMA_BASE_URL", DEFAULT_BASE_URL)
    if not _ollama_is_up(url):
        pytest.skip(f"no Ollama serving {url}")
    return url


def pytest_runtest_logreport(report) -> None:
    if report.when == "call" and report.passed and report.nodeid.startswith(_SUITE):
        _completed.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Turn a session that ran no arm red, when the caller required one.

    Counted rather than asserted per test, because there are two ways to run
    nothing -- no Ollama and no Docker -- and each is a skip raised by a
    different fixture in a different conftest. What the scheduled job needs is
    not "the server was up" but "an arm completed", and that is one number.
    """
    if os.environ.get(REQUIRE_LIVE) != "1" or _completed or exitstatus != 0:
        return
    print(
        f"\nNO LIVE ARM RAN and {REQUIRE_LIVE}=1: every case was skipped, so this session is "
        "evidence about nothing. The required per-PR gate already scores the committed "
        "recordings; this job exists only to check a live model, and a green with no run is "
        "the one outcome it must not report (hy-26nd)."
    )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
