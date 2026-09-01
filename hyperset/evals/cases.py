"""The locked cases, loaded rather than constructed (GitHub #25).

The cases are human-owned and live in YAML beside this module, because #25 is
explicit that Hyperset does not generate the exam it is graded against. This
module reads that file and refuses anything it cannot check.

THE ONE RULE THAT MAKES THE BENCHMARK VALID lives here as code rather than as a
comment in the fixture: `Case.prompt()` is the only thing a run may hand an
arm, and it is the question and nothing else. Every other field -- the expected
domain, the refs, whether a plan had to validate -- is read by the scorers
after the run. A harness that passed the domain along would prove a model can
read a bundle somebody else selected, which is exactly what part 1 of GitHub
#70 deleted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from hyperset.planner.trace import content_hash

CASES_DIR = Path(__file__).parent / "cases"
CASES_PATH = CASES_DIR / "revenue.yaml"
PRIMARY_SUITE = "revenue"
"""The one suite that exists today. A run over ONLY revenue must produce the
byte-identical `task_version` it produced before this file learned about more
than one suite, or every committed recording fails `refuse_a_different_exam`.
That is why `task_version` is per-suite and parses its own suite name rather
than hashing the whole directory: adding `billing.yaml` must not restate what
`revenue@<hash>` means (hy-esp, part 2)."""

GOVERNED_FETCH = "governed_fetch"
NO_MATCH = "no_match"
STALE_GOVERNED_CONTEXT = "stale_governed_context"
FAMILIES = (GOVERNED_FETCH, NO_MATCH, STALE_GOVERNED_CONTEXT)
"""#25's three families. The third -- stale, conflicting and finding-aware
behaviour -- reuses the processor's `approved_expression_drift` finding: over a
drifted sync the served bundle carries that finding, and a governed answer must
SURFACE the staleness rather than answer as if the definition were current. The
scorer for it is `scorers._stale_governed_context_surfaced`; a case using it
must run against a drifted fixture with the finding persisted, so it is scored
against a recorded run over that state, never a case that cannot fail (hy-2m0r,
step 6, extending hy-ast part 1)."""

PARAPHRASE = "paraphrase"
CONTROL = "control"
PROBES = (PARAPHRASE, CONTROL)
"""How a case reaches governed context. A CONTROL case names its domain
literally and measures whether the machinery works at all; a PARAPHRASE case
expresses a governed intent WITHOUT naming any configured domain, so it reaches
governed context only through the planner -- which is the architecture #70
mandated and the only thing this benchmark actually tests. They are reported
SEPARATELY: a governed arm that wins on controls and ties on paraphrases has not
demonstrated the claim. A case is CONTROL unless it declares `probe: paraphrase`,
so today's revenue suite (which names `revenue`) is control by construction and
needs no edit (hy-esp)."""


def suite_path(suite: str) -> Path:
    return CASES_DIR / f"{suite}.yaml"


def discover_suites() -> tuple[str, ...]:
    """Every suite present, in a deterministic order. Tolerates a suite that has
    not been authored yet: `billing.yaml` is absent until the human fixture
    lands (hy-unks), and its absence is not an error -- the loader guards over
    what exists rather than hard-failing on what does not (hy-esp ruling)."""
    return tuple(sorted(path.stem for path in CASES_DIR.glob("*.yaml")))


def task_version(suite: str = PRIMARY_SUITE) -> str:
    """What exam a run sat, as a value that moves when THAT suite does.

    Content-addressed rather than hand-incremented: a case edited without a
    version bump would otherwise produce two recordings claiming to be the same
    task, and the one thing a benchmark cannot survive is not knowing which
    questions a score belongs to.

    PER-SUITE, and the suite name is IN the value (`revenue@<hash>`), so a
    recording verifies against only the file it sat: adding a second suite must
    not change what a revenue recording is checked against. The value for the
    revenue suite is byte-identical to the pre-part-2 single-suite value.
    """
    return f"{suite}@{content_hash(suite_path(suite).read_text())}"


def combined_task_version() -> str:
    """The identity of the WHOLE exam a multi-suite run sat: every present
    suite's per-suite version, joined deterministically. With one suite it is
    byte-identical to that suite's `task_version`, so a revenue-only repository
    reports exactly what it did before part 2."""
    return "+".join(task_version(suite) for suite in discover_suites())


class UnusableCase(RuntimeError):
    """A case this harness cannot score, named as such at load time.

    Loudly at load rather than quietly at scoring: a case whose family nothing
    handles would otherwise contribute no failing predicate and read as a case
    that passed everything.
    """


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    question: str
    expected_domain: str | None
    must_cite: tuple[str, ...]
    must_not_cite: tuple[str, ...]
    must_state: tuple[str, ...]
    """Exact rule text the answer has to carry -- an expression, a filter, a
    grain -- as it is written in the human-owned context.

    This is where a governed substrate is supposed to win and a raw-metadata
    arm is supposed to lose, so it is scored rather than admired: raw Superset
    metadata knows a `gross_amount` column exists and does not know revenue is
    recognized net of tax on completed orders. Exact strings, because a
    paraphrase check would be a model judging a model."""
    requires_plan_validation: bool
    reason: str
    suite: str = PRIMARY_SUITE
    probe: str = CONTROL
    """`control` (names its domain literally) or `paraphrase` (must not). See
    `PROBES`. A paraphrase case whose question carries a configured domain
    literal is rejected at load, because it would let the arm key on surface
    wording instead of routing through the planner. Defaulted, and LAST, so a
    direct `Case(...)` in a scorer test keeps working and is a control by
    default -- only a suite file opts a case into paraphrase."""

    def prompt(self) -> str:
        """Everything an arm is given. Deliberately one field."""
        return self.question


def load_cases(directory: Path | None = None) -> tuple[Case, ...]:
    """Every case in every suite, loaded rather than constructed.

    Multi-suite (hy-esp): a run must select between domains, which is impossible
    with one. The domain-literal set the paraphrase guard needs is collected
    across ALL suites first, so a paraphrase in `billing` cannot leak the word
    `revenue` either. Case ids are unique across suites, because a recording
    names its case and nothing else disambiguates two suites' `revenue_by_region`.

    `CASES_DIR` is resolved at CALL time, not frozen as a default argument, so a
    test that repoints the directory (a second suite, a weakened exam) is seen by
    every caller -- `task_version` already reads the global at call time, and a
    loader that did not would answer a different question than the versioner.
    """
    directory = CASES_DIR if directory is None else directory
    payloads = {
        path.stem: yaml.safe_load(path.read_text()) for path in sorted(directory.glob("*.yaml"))
    }
    if not payloads:
        raise UnusableCase(f"{directory} holds no suites; a benchmark with no exam scores nothing")
    literals = _domain_literals(payloads)
    cases: list[Case] = []
    for suite, payload in payloads.items():
        if payload.get("schema_version") != 1:
            raise UnusableCase(
                f"suite {suite!r} declares schema {payload.get('schema_version')!r}; "
                "this loader understands 1"
            )
        suite_cases = [_case(suite, entry, literals) for entry in payload.get("cases") or []]
        if not suite_cases:
            raise UnusableCase(
                f"suite {suite!r} defines no cases, and an empty suite scores perfect"
            )
        cases.extend(suite_cases)
    ids = [case.id for case in cases]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        raise UnusableCase(
            f"duplicate case ids across suites {duplicates}; a recording names the case it ran"
        )
    return tuple(cases)


def load_suite(path: Path) -> tuple[Case, ...]:
    """One suite, with the paraphrase guard scoped to that suite's own literals.
    For tests and single-file inspection; a run uses `load_cases`."""
    payload = yaml.safe_load(path.read_text())
    literals = _domain_literals({path.stem: payload})
    return tuple(_case(path.stem, entry, literals) for entry in payload.get("cases") or [])


def _domain_literals(payloads: dict[str, dict]) -> frozenset[str]:
    """The words a paraphrase must not carry: every suite name, every
    `expected_domain` a suite names, and any extra `domain_literals` a suite
    declares (an alias a human knows would leak). Lowercased; matched on word
    boundaries so `revenue` in `revenue_by_region` the id never triggers -- only
    the question text is checked."""
    literals: set[str] = set()
    for suite, payload in payloads.items():
        literals.add(suite.lower())
        for extra in payload.get("domain_literals") or ():
            literals.add(str(extra).lower())
        for entry in payload.get("cases") or []:
            if entry.get("expected_domain"):
                literals.add(str(entry["expected_domain"]).lower())
    return frozenset(literals)


def _case(suite: str, entry: dict, literals: frozenset[str]) -> Case:
    probe = entry.get("probe", CONTROL)
    if probe not in PROBES:
        raise UnusableCase(
            f"case {entry.get('id')!r} declares probe {probe!r}; "
            f"expected one of {', '.join(PROBES)}"
        )
    question = " ".join(str(entry["question"]).split())
    if probe == PARAPHRASE:
        leaked = sorted(
            literal
            for literal in literals
            if re.search(rf"\b{re.escape(literal)}\b", question, re.IGNORECASE)
        )
        if leaked:
            raise UnusableCase(
                f"paraphrase case {entry.get('id')!r} names configured domain literal(s) {leaked}; "
                "a paraphrase must reach governed context through the planner, not by surface "
                "wording -- either reword it or mark it `probe: control`"
            )
    family = entry.get("family")
    if family not in FAMILIES:
        raise UnusableCase(
            f"case {entry.get('id')!r} declares family {family!r}, which no scorer handles; "
            f"the families this harness scores are {', '.join(FAMILIES)}"
        )
    expected_domain = entry.get("expected_domain")
    if family == GOVERNED_FETCH and not expected_domain:
        raise UnusableCase(
            f"case {entry.get('id')!r} is a governed fetch that names no domain to expect, so "
            "its domain-selection predicate could not fail"
        )
    if family == NO_MATCH and expected_domain:
        raise UnusableCase(
            f"case {entry.get('id')!r} is a no-match case naming domain {expected_domain!r}; "
            "a no-match case whose answer is a domain is not a no-match case"
        )
    if family == STALE_GOVERNED_CONTEXT and not expected_domain:
        raise UnusableCase(
            f"case {entry.get('id')!r} is a stale-governed-context case that names no domain to "
            "expect, so there is no governed context whose staleness a predicate could check"
        )
    return Case(
        id=str(entry["id"]),
        suite=suite,
        probe=probe,
        family=family,
        question=question,
        expected_domain=expected_domain,
        must_cite=tuple(entry.get("must_cite") or ()),
        must_not_cite=tuple(entry.get("must_not_cite") or ()),
        must_state=tuple(entry.get("must_state") or ()),
        requires_plan_validation=bool(entry.get("requires_plan_validation")),
        reason=str(entry.get("reason") or ""),
    )
