"""One live arm run, recorded (GitHub #25, ADR 0013).

This is the half that needs a real Ollama, so it is the half that runs on a
schedule rather than on a pull request. What it produces -- a `Recording` -- is
what the required per-PR gate scores.

THE PINS ARE ASSERTED BEFORE THE FIRST TOKEN, which is the sequencing #25 asks
for: a run whose pins do not match must fail rather than warn, and failing
after 314 seconds of inference is a warning with extra steps. The window is
observed rather than requested, because Ollama's OpenAI-compatible endpoint
ignores a requested one and truncates silently.

THE COMMIT IS PINNED ONCE PER SESSION rather than read per case (hy-r1i0), and
nothing is persisted under a commit no ref reaches. `hyperset.evals.provenance`
holds both checks and the reasoning behind them.

THE SESSION ALSO MINTS A RUN ID, and the store is keyed on it (hy-qc4u): a run
of a case is `recordings/<arm>/<case>/<run_id>.json`, so two sessions of one
case at one tree coexist instead of the second overwriting the first. That pair
is what #25's close condition means by "n and cross-session variance", and until
this it was unstorable rather than merely unmeasured.
"""

from __future__ import annotations

import os
from pathlib import Path

from hyperset.db.base import utcnow
from hyperset.evals.arms import arm_spec
from hyperset.evals.cases import Case, task_version
from hyperset.evals.pins import (
    FRONTIER_MODEL_ENV,
    PINNED_SEED,
    PINNED_TEMPERATURE,
    assert_pins,
    observe_pins,
)
from hyperset.evals.provenance import REPO_ROOT, recording_session, refuse_an_unresolvable_commit
from hyperset.evals.recording import FRONTIER_ARM, RECORDING_SCHEMA_VERSION, Recording
from hyperset.evals.source_identity import source_refs
from hyperset.planner.loop import plan_analytics_context
from hyperset.planner.ollama import declared_context_window
from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
from hyperset.planner.runtime import PINNED_MODEL, RuntimeConfig

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
MAX_TURNS = 8
"""Identical across arms, because a turn budget is part of the environment #25
holds constant. Eight is the planner's own default: catalog, resolve, validate
and an answer is four, which leaves room for one bounded retry and a wrong
turn."""

RECORDINGS_DIR = Path(__file__).parent / "recordings"

# The frontier arm's credential (and optional endpoint) are read from the environment, never
# a default, so the credential-free CI/gate path cannot run the frontier arm at all. WHICH
# frontier model, and the credential to run it, is the Overseer/Brandon infra decision hy-2tg6
# (#141); this slice only fixes the boundary. `FRONTIER_MODEL_ENV` is owned by the pin layer
# (`evals.pins`), since "what model an arm pins" is a pin fact -- imported above.
FRONTIER_API_KEY_ENV = "HYPERSET_FRONTIER_API_KEY"
FRONTIER_BASE_URL_ENV = "HYPERSET_FRONTIER_BASE_URL"


class FrontierArmNotConfigured(RuntimeError):
    """The frontier arm was asked to record with no pinned frontier model + credential
    configured. The local runner FAILS CLOSED here (hy-0qr6): it will not run the frontier
    arm on the local pinned model and label the result a `frontier_raw` recording -- that
    would publish a FALSE frontier comparison (a frontier claim measured against no frontier
    model and no credential). A real pinned frontier model + credential is hy-2tg6."""


def frontier_config_or_refuse() -> tuple[str, str, str | None]:
    """The configured `(frontier_model, api_key, base_url)`, or FAIL CLOSED.

    The frontier arm exists to compare a FRONTIER model to the raw-metadata baseline on an
    identical surface, so the ONLY thing that legitimises a `frontier_raw` recording is a
    real, separately-configured frontier model AND its credential. Both must be present and
    non-empty, and the model must NOT be the local pinned model (a frontier arm running the
    local model is exactly the impostor this refuses). Anything else raises, before any pin
    observation, inference, or recording."""
    model = os.environ.get(FRONTIER_MODEL_ENV, "").strip()
    api_key = os.environ.get(FRONTIER_API_KEY_ENV, "").strip()
    if not model or not api_key:
        raise FrontierArmNotConfigured(
            f"the frontier arm needs {FRONTIER_MODEL_ENV} and {FRONTIER_API_KEY_ENV} set "
            f"(a pinned frontier model and its credential, hy-2tg6); the local runner "
            f"refuses to record frontier evidence on the local pinned model {PINNED_MODEL!r}"
        )
    if model == PINNED_MODEL:
        raise FrontierArmNotConfigured(
            f"the frontier model must not be the local pinned model {PINNED_MODEL!r}; that "
            "would measure the raw baseline against itself and call it a frontier comparison"
        )
    base_url = os.environ.get(FRONTIER_BASE_URL_ENV, "").strip() or None
    return model, api_key, base_url


def run_case(
    case: Case,
    *,
    arm: str,
    session_factory,
    base_url: str = DEFAULT_BASE_URL,
    max_turns: int = MAX_TURNS,
) -> Recording:
    """Run one case on one arm against a live model and return its recording."""
    spec = arm_spec(arm)
    # FAIL-CLOSED frontier boundary (hy-0qr6), checked FIRST -- before any pin observation,
    # inference, or recording -- so the credential-free path produces no frontier evidence
    # at all. The other arms run the local pinned model; the frontier arm runs ONLY a
    # separately-configured frontier model + credential, or it refuses.
    run_model = PINNED_MODEL
    run_base_url = base_url
    api_key: str | None = None
    if arm == FRONTIER_ARM:
        run_model, api_key, frontier_base = frontier_config_or_refuse()
        run_base_url = frontier_base or base_url
    pins = observe_pins(arm=arm, base_url=run_base_url, model=run_model)
    # Before the first token, and fatal. Both halves matter: a mismatch found
    # afterwards has already spent the run, and a mismatch that only warns
    # produces a recording nobody can tell from a valid one.
    assert_pins(pins, arm=arm)
    # Beside the pin check and for the same reason: a session whose HEAD has
    # moved is a session recording under two different commits, and finding
    # that out after the inference means the answer is already unattributable.
    # The run id comes from the same object, so every case this process records
    # is attributed to one session rather than to as many sessions as cases.
    session = recording_session()
    config = RuntimeConfig(
        model=run_model,
        base_url=run_base_url,
        api_key=api_key,
        seed=PINNED_SEED,
        temperature=PINNED_TEMPERATURE,
        allocated_context_window=pins.context_window,
        declared_context_window=declared_context_window(run_model, base_url=run_base_url),
        max_turns=max_turns,
    )
    trace = plan_analytics_context(
        case.prompt(),
        runtime=OpenAIAgentsRuntime(
            config, instructions=spec.instructions, declarations=list(spec.declarations)
        ),
        executor=spec.executor(session_factory=session_factory),
        instructions=spec.instructions,
        declarations=list(spec.declarations),
    )
    return Recording(
        run_id=session.run_id,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=arm,
        case_id=case.id,
        task_version=task_version(case.suite),
        git_commit=session.commit,
        recorded_at=utcnow().isoformat(),
        pins=pins,
        trace=trace.to_dict(),
        source_refs=source_refs(trace.to_dict()),
    )


UNIDENTIFIED_RUN_STEM = "unidentified"
"""The filename the four schema-1 recordings keep, having no run id to be named
for. It names the ABSENCE of an identity rather than supplying one: nothing is
written into those files, which is the line ruling 2 drew when it refused a
hand-written run id as a fabrication. A real run id is hex, so no future run can
be filed here by accident."""


def case_recordings_dir(arm: str, case_id: str, *, directory: Path | None = None) -> Path:
    """Where every run of one case on one arm lives.

    A directory per case rather than `<case>-<run>.json` in one flat arm folder,
    so `n` is something a reader gets from a listing instead of from parsing a
    filename convention that each reader would have to parse the same way.
    """
    return (RECORDINGS_DIR if directory is None else directory) / arm / case_id


def recording_path(arm: str, case_id: str, run_id: str, *, directory: Path | None = None) -> Path:
    """The one path a given RUN of a case on an arm is written to.

    The run id is required rather than defaulted, which is the whole of hy-qc4u
    in one signature: the old two-argument form named one file per (arm, case),
    so a second session of one case overwrote the first and the pair #25's close
    condition wants compared could not both exist on disk.
    """
    return case_recordings_dir(arm, case_id, directory=directory) / f"{run_id}.json"


def recordings_of(arm: str, case_id: str, *, directory: Path | None = None) -> tuple[Path, ...]:
    """Every stored run of one case on one arm, oldest name first.

    THE ONE ENUMERATOR, and every reader goes through it. The layout used to be
    spelled out independently in nine files -- `report.py`, which is the
    required per-PR gate, built `directory / arm / f"{case.id}.json"` itself and
    would not even have failed to import if `recording_path` were deleted. One
    convention with two implementations is exactly the defect this bead is
    about, one level down: two records, each internally correct, that disagree
    and are never read side by side.

    Sorted by name, which is stable but carries no meaning: run ids are random,
    so the order is not chronological and nothing may read it as "latest".
    """
    case_dir = case_recordings_dir(arm, case_id, directory=directory)
    return tuple(sorted(case_dir.glob("*.json")))


def write_recording(recording: Recording, path: Path, *, root: Path = REPO_ROOT) -> None:
    """Persist a recording, or refuse it for the commit it names (hy-tz03).

    The one door every recording this repository keeps goes through, so the
    check sits here rather than on `Recording.write`: `write` is the serializer,
    and a test that hand-builds a recording to exercise a reader has no business
    being held to a commit that resolves in this checkout.

    What it refuses is the artifact hy-r1i0 found on disk -- a recording pinned
    to a commit reachable from no ref -- BEFORE the file exists, because the
    normal end of a recording session is a commit, and a bad recording that got
    written is one an unwary `git add -A` ships.
    """
    refuse_an_unresolvable_commit(recording.git_commit, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    recording.write(path)
