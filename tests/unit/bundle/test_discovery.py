"""Ranked candidate sources: the ordering, the rationale, and the boundary
(hy-01e0, hy-gh-124 slice 1).

Pure -- `candidate_sources` takes values and returns a section -- so the
ranking itself is tested here at full width, and
`tests/postgres/test_context_bundle.py` covers only what the wiring adds:
that the real estate and the real commit reach it.
"""

from __future__ import annotations

# Restored on the rebase onto b13f2f2, deliberately and not by accident. hy-ezc3
# replaced this file's `_REF` regex with prefix matching and correctly deleted
# `import re` for ITS tree; this branch's ADR-0019-floor-1 test still calls
# `re.search`, and because only one side touched the line the three-way merge
# took the deletion with no conflict marker for a reviewer to see (hy-npx7).
import re
from datetime import UTC, datetime

import pytest

from hyperset.bundle.discovery import (
    CANDIDATE_LIMIT,
    CANDIDATE_SOURCES,
    DECLARED_REFERENCES,
    EVERY_CANDIDATE_DISAGREES,
    EXPRESSION_AGREEMENT,
    GIT_ENGAGEMENT,
    NO_GIT_RELATIVE_SIGNAL,
    NO_GOVERNING_DOMAIN,
    NOT_SEPARATED,
    OBSERVED,
    OPEN_FINDING,
    PROHIBITED_BY_CONTEXT,
    PROPOSAL_OUTCOMES,
    PROPOSED,
    SIGNALS,
    SOURCE_DELETED,
    SOURCE_FRESHNESS,
    GovernedFacts,
    ObservedSource,
    _declined,
    candidate_sources,
)
from hyperset.context.schema import EVIDENCE_ASSET_TYPES

PRIMARY = "superset:dataset:ae48881d"
DIMENSION = "superset:dataset:5bcf01e3"
UNGOVERNED = "superset:dataset:6f4976c2"

REVENUE_FIELDS = (
    {"name": "recognized_revenue", "expression": "SUM(gross_amount - tax_amount)"},
    {"name": "region", "expression": "customer_dim.region"},
)


def source(ref: str, **overrides) -> ObservedSource:
    payload = {
        "ref": ref,
        "connector": "superset",
        "asset_type": "dataset",
        "external_id": ref.rsplit(":", 1)[-1],
        "asset_id": f"oa-{ref.rsplit(':', 1)[-1]}",
        "connection_id": "conn-1",
        "observed_version_id": f"oav-{ref.rsplit(':', 1)[-1]}",
    }
    payload.update(overrides)
    return ObservedSource(**payload)


def rank(sources, governed=None, undeclared=("churn",), domain_is_configured=True, **kwargs):
    """The coverage refusal by default: 'revenue' is a real configured domain
    that does not declare 'churn'. hy-xq55's case passes
    `domain_is_configured=False`, and the arms that do are the only ones that
    should -- every other test here is about a domain that exists."""
    return candidate_sources(
        domain="revenue",
        undeclared=list(undeclared),
        sources=list(sources),
        governed=governed or GovernedFacts(),
        domain_is_configured=domain_is_configured,
        **kwargs,
    )


def refs(section) -> list[str]:
    return [candidate["ref"] for candidate in section["candidates"]]


def signal(candidate, name) -> dict:
    return next(entry for entry in candidate["signals"] if entry["signal"] == name)


def test_nothing_observed_is_no_section_rather_than_an_empty_one():
    """ "Assist ran and found nothing" and "assist did not run" are different
    facts, and a client reading for the key can only tell them apart if the
    empty case has no key."""
    assert rank([]) is None


def test_git_engagement_outranks_a_source_no_commit_names():
    governed = GovernedFacts(
        approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)},
    )

    section = rank([source(UNGOVERNED), source(PRIMARY)], governed)

    assert refs(section) == [PRIMARY, UNGOVERNED]
    assert [candidate["rank"] for candidate in section["candidates"]] == [1, 2]


def test_expression_agreement_breaks_a_tie_on_the_computation_not_the_name():
    """Both sources are approved by the same domain in the same role, and both
    are named nothing like the field. What separates them is that one of them
    actually computes what the commit declares."""
    governed = GovernedFacts(
        approved={
            PRIMARY: ({"domain": "revenue", "role": "primary"},),
            DIMENSION: ({"domain": "revenue", "role": "primary"},),
        },
        field_expressions=REVENUE_FIELDS,
    )
    computes = source(PRIMARY, metric_expressions=("sum( gross_amount-tax_amount )",))

    section = rank([source(DIMENSION), computes], governed)

    assert refs(section) == [PRIMARY, DIMENSION]
    matched = signal(section["candidates"][0], EXPRESSION_AGREEMENT)
    assert matched["value"] == {"fields": ["recognized_revenue"]}
    assert "never on a name" in matched["statement"]


def test_an_undecidable_difference_is_not_counted_as_agreement():
    """`SUM(o.amount)` against `SUM(amount)` is UNDECIDED, not EQUIVALENT: the
    warehouse would have to settle it, and a rank resting on a difference
    Hyperset could not decide is a guess with a number on it."""
    governed = GovernedFacts(field_expressions=({"name": "total", "expression": "SUM(amount)"},))
    undecidable = source(PRIMARY, metric_expressions=("SUM(o.amount)",))

    section = rank([undecidable], governed)

    assert signal(section["candidates"][0], EXPRESSION_AGREEMENT)["value"] == {"fields": []}


def test_a_prohibited_source_sinks_to_the_bottom_and_says_why():
    """Never filtered. ADR 0019 floor 6 forbids ranking a prohibition out of
    view, and a source hidden for being prohibited is one the caller finds
    again on its own with the reason missing."""
    governed = GovernedFacts(
        prohibited={UNGOVERNED: ({"domain": "revenue", "reason": "double-counts captures"},)},
        approved={UNGOVERNED: ({"domain": "revenue", "role": "primary"},)},
    )

    section = rank([source(UNGOVERNED), source(PRIMARY)], governed)

    assert refs(section) == [PRIMARY, UNGOVERNED]
    disagreement = section["candidates"][1]["disagrees_with_git"][0]
    assert disagreement["kind"] == PROHIBITED_BY_CONTEXT
    assert "double-counts captures" in disagreement["statement"]


def test_a_deleted_source_is_still_served_and_still_discloses_the_deletion():
    section = rank([source(PRIMARY, deleted_at=datetime(2026, 7, 1, tzinfo=UTC))])

    kinds = [entry["kind"] for entry in section["candidates"][0]["disagrees_with_git"]]
    assert kinds == [SOURCE_DELETED]


def test_a_deleted_source_sinks_below_a_live_one_no_signal_favours():
    """Position, not just disclosure. The deleted source here wins every signal
    -- Git names it, it is the fresher of the two -- and still sorts last,
    because a source that stopped reporting is not a better answer for having
    been the better source while it existed.

    Red before this change: `_order` sank only `prohibited`, so this candidate
    was served at rank 1 with its deletion disclosed underneath it.
    """
    governed = GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)})
    gone = source(
        PRIMARY,
        deleted_at=datetime(2026, 7, 1, tzinfo=UTC),
        source_modified_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    live = source(DIMENSION, source_modified_at=datetime(2025, 1, 1, tzinfo=UTC))

    section = rank([gone, live], governed)

    assert refs(section) == [DIMENSION, PRIMARY]


def test_an_open_finding_sinks_a_candidate_below_one_that_carries_none():
    """The third disagreement, and the same rule: cited from the governed
    processor, never authored here, and it costs the candidate its rank rather
    than its place in the list."""
    flagged = source(
        PRIMARY,
        findings=(
            {"finding_id": "fnd-1", "finding_type": "definition_drift", "severity": "critical"},
        ),
    )
    governed = GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)})

    section = rank([flagged, source(DIMENSION)], governed)

    assert refs(section) == [DIMENSION, PRIMARY]
    assert [entry["kind"] for entry in section["candidates"][1]["disagrees_with_git"]] == [
        OPEN_FINDING
    ]


def finding(finding_id, finding_type, severity) -> dict:
    return {"finding_id": finding_id, "finding_type": finding_type, "severity": severity}


# ADR 0021 decision 2's two non-agreeing verdicts, as the processor grades them.
# PRIMARY carries the UNDECIDED one and DIMENSION the contradiction, ON PURPOSE:
# `_order`'s last key is `ref`, and 'superset:dataset:5bcf01e3' sorts before
# 'superset:dataset:ae48881d'. So the tiebreak favours the WRONG answer and only
# the tier can produce the expected order. With the pair the other way round
# every arm below passes without any tiering at all.
def undecided_source() -> ObservedSource:
    return source(
        PRIMARY, findings=(finding("f-und", "approved_expression_undecidable", "warning"),)
    )


def contradicting_source() -> ObservedSource:
    return source(DIMENSION, findings=(finding("f-dif", "approved_expression_drift", "error"),))


def test_an_undecided_finding_ranks_above_a_contradiction():
    """ADR 0021 decision 2's third outcome, surviving into the order (hy-qbii).

    The pair the bead measured, against one Git expression: `DIFFERENT` becomes
    an `error` and `UNDECIDED` becomes a `warning` under its own finding type.
    Before this change `_order`'s first key was `bool(disagrees_with_git)`, so
    both sank into one tier and the module whose entire output is an ordering
    said nothing about the difference it had just been given.
    """
    section = rank([contradicting_source(), undecided_source()])

    assert refs(section) == [PRIMARY, DIMENSION]


def test_an_undecided_finding_still_sinks_below_a_clean_source():
    """The other side of the same change, and the one that keeps it from being a
    lift. ADR 0019 floor 6 wants a disagreement sunk-and-stated, so the middle
    tier has to sit strictly between agreement and contradiction -- never merge
    upward into agreement.

    Sharper than "not rank 1": the undecided source is the one the commit
    APPROVES, so `git_engagement` favours it and every key below the tier would
    put it first. Only the tier holds it down. Without this arm, deleting the
    middle tier's floor -- treating a warning as agreement -- would leave the
    test above green.
    """
    governed = GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)})

    section = rank([undecided_source(), source(UNGOVERNED)], governed)

    assert refs(section) == [UNGOVERNED, PRIMARY]
    assert signal(section["candidates"][1], GIT_ENGAGEMENT)["value"]["approved_by"] != []


def test_severity_and_not_the_finding_type_places_a_disagreement():
    """What the tier reads, stated as a test because it is the decision the bead
    asked whoever took it to make.

    `discovery` does not know `approved_expression_undecidable` from any other
    finding type, and deliberately: the processor grades its own findings and
    this reads that grade. So the SAME type graded `error` sinks to the bottom
    tier, and a type this module has never heard of graded `warning` does not. A
    second copy of the processor's type register here would be two components
    answering "how much does this disagreement count" at different granularities.

    Both halves are needed. Keying on the type instead would put `escalated` in
    the middle tier and, on the `ref` tiebreak, first overall -- so this arm is
    red against that implementation as well as against the collapsed one.
    """
    escalated = source(
        DIMENSION, findings=(finding("f-3", "approved_expression_undecidable", "error"),)
    )
    unknown_type = source(UNGOVERNED, findings=(finding("f-4", "some_future_rule", "warning"),))

    section = rank([escalated, undecided_source(), unknown_type])

    assert refs(section) == [UNGOVERNED, PRIMARY, DIMENSION]


def test_a_prohibition_is_the_worst_disagreement_among_sources_that_all_disagree():
    """Sinking is not flattening. Once every candidate disagrees the order still
    has to say something, and it says that the customer's own refusal outranks a
    fact the connector merely observed."""
    prohibited = source(PRIMARY)
    deleted = source(DIMENSION, deleted_at=datetime(2026, 7, 1, tzinfo=UTC))
    governed = GovernedFacts(
        prohibited={PRIMARY: ({"domain": "revenue", "reason": "double-counts captures"},)}
    )

    section = rank([prohibited, deleted], governed)

    assert refs(section) == [DIMENSION, PRIMARY]


def test_freshness_orders_what_the_signals_leave_tied_and_unknown_is_not_old():
    fresh = source(PRIMARY, source_modified_at=datetime(2026, 7, 20, tzinfo=UTC))
    stale = source(DIMENSION, source_modified_at=datetime(2025, 1, 1, tzinfo=UTC))
    silent = source(UNGOVERNED)

    section = rank([silent, stale, fresh])

    assert refs(section) == [PRIMARY, DIMENSION, UNGOVERNED]
    unknown = signal(section["candidates"][2], SOURCE_FRESHNESS)
    assert unknown["value"] is None
    assert "unknown rather than old" in unknown["statement"]


def test_every_candidate_states_a_signal_even_when_nothing_engages_it():
    """A rank with no stated signal is not acceptable output. The uncovered
    estate is the whole point, so "no configured Git context names this" has
    to be a statement rather than an omission."""
    section = rank([source(UNGOVERNED)])

    stated = {entry["signal"] for entry in section["candidates"][0]["signals"]}
    assert stated == set(SIGNALS)
    assert (
        "outside governance entirely"
        in signal(section["candidates"][0], GIT_ENGAGEMENT)["statement"]
    )


def test_the_order_is_stable_for_sources_no_signal_separates():
    identical = [source(UNGOVERNED), source(PRIMARY), source(DIMENSION)]

    assert refs(rank(identical)) == refs(rank(list(reversed(identical))))


def test_the_bound_is_disclosed_with_the_count_it_bounded():
    section = rank(
        [source(f"superset:dataset:{index:02d}") for index in range(CANDIDATE_LIMIT + 3)]
    )

    assert section["returned"] == CANDIDATE_LIMIT
    assert section["considered"] == CANDIDATE_LIMIT + 3
    assert section["bound"] == CANDIDATE_LIMIT


def test_no_candidate_can_hold_a_declared_ref_beside_the_observed_one():
    """The pair rule, as a property of the shape rather than of this ranking
    (ADR 0019 decision 2). A governed link is the ordered pair (declared ref,
    observed asset version); a candidate carries exactly one ref and it is the
    observed asset's own identity, so the pair is not expressible however the
    ranking is fed.

    Run against the widest candidate this module can build rather than the
    quietest: one that discloses a freshness timestamp, a Git prohibition and an
    open finding, so every string-bearing field a candidate has is actually
    populated when the walker reads it. The earlier fixture disclosed none of
    the three, which meant the guard passed over mostly empty lists.
    """
    governed = GovernedFacts(
        approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)},
        prohibited={PRIMARY: ({"domain": "revenue", "reason": "double-counts captures"},)},
        field_expressions=REVENUE_FIELDS,
    )
    computes = source(
        PRIMARY,
        metric_expressions=("SUM(gross_amount - tax_amount)",),
        source_modified_at=datetime(2026, 7, 20, tzinfo=UTC),
        findings=({"finding_id": "fnd-1", "finding_type": "definition_drift", "severity": "high"},),
    )

    section = rank([computes], governed)
    candidate = section["candidates"][0]

    # The fixture is only worth what it discloses, so prove it disclosed it.
    assert {entry["kind"] for entry in candidate["disagrees_with_git"]} == {
        PROHIBITED_BY_CONTEXT,
        OPEN_FINDING,
    }
    assert signal(candidate, SOURCE_FRESHNESS)["value"] == "2026-07-20T00:00:00+00:00"

    assert [key for key in candidate if key.endswith("ref")] == ["ref"]
    assert candidate["ref"] == computes.ref
    # And no nested value smuggles one in: the only ref-shaped string anywhere
    # under the candidate is the observed asset's own.
    assert _ref_shaped_strings(candidate) == {computes.ref}


def test_nothing_a_candidate_carries_can_be_read_as_governed():
    section = rank([source(PRIMARY)])

    assert section["candidates"][0]["governance"] == OBSERVED
    assert section["kind"] == CANDIDATE_SOURCES
    assert "not approved, canonical, or validated" in section["disclosure"]


def test_the_section_names_what_produced_it_and_that_no_model_did():
    """ADR 0019 floor 9: assist need not be reproducible, it must be
    accountable. This one happens to be both."""
    section = rank([source(PRIMARY)])

    assert section["produced_by"]["model"] is None
    assert section["produced_by"]["signals"] == list(SIGNALS)


def test_the_section_carries_its_own_identity_and_it_moves_with_its_content():
    quiet = rank([source(PRIMARY)])
    louder = rank([source(PRIMARY, source_modified_at=datetime(2026, 7, 20, tzinfo=UTC))])

    assert quiet["assist_id"].startswith("as-")
    assert quiet["assist_id"] != louder["assist_id"]
    assert rank([source(PRIMARY)])["assist_id"] == quiet["assist_id"]


@pytest.mark.parametrize("undeclared", [["churn"], ["churn", "arr", "churn"]])
def test_the_section_names_the_request_it_answers(undeclared):
    section = rank([source(PRIMARY)], undeclared=undeclared)

    assert section["answers"] == {
        "domain": "revenue",
        "undeclared_concepts": sorted(set(undeclared)),
    }


# A ref is `<connector>:<asset_type>:<external_id>`, and its first two segments
# are a closed vocabulary the parser already enumerates -- so the predicate reads
# that vocabulary rather than guessing a shape.
#
# Guessing the shape is what the previous pattern did, and it had to decide what
# an external_id may contain. The answer is "colons": a DataHub external_id IS a
# urn, so `datahub:glossary_term:urn:li:glossaryTerm:recognized_revenue` -- which
# the shipped recording `governed/revenue_by_region/unidentified.json` under
# `hyperset/evals/recordings/` carries in `source_refs` -- matched nothing. The
# store is keyed on the run, so the case owns a directory (hy-qc4u). That is the
# family this guard most needs to see, because a glossary term is the
# declared-concept side of the very pair the rule below forbids (hy-ezc3).
#
# Counting colons is not the test either, and the numbers are measured on the
# widest candidate this module builds rather than recalled: the ISO timestamp
# `2026-07-20T00:00:00+00:00` carries three, the freshness sentence quoting it
# carries three, and the Git prohibition sentence carries one. So a `>= 2` count
# flagged two strings that are not refs and would have missed a prohibition
# sentence that named one. Anchoring on the vocabulary rejects all three on their
# first segment.
_REF_PREFIXES = tuple(
    f"{connector}:{asset_type}:"
    for connector, asset_types in EVIDENCE_ASSET_TYPES.items()
    for asset_type in asset_types
)


def _is_ref(value: str) -> bool:
    """Whether a string is a ref the context parser would accept -- a known
    connector, one of that connector's known asset types, and a non-empty
    external_id, which may itself contain colons."""
    return any(
        value.startswith(prefix) and value[len(prefix) :].strip() for prefix in _REF_PREFIXES
    )


def _ref_shaped_strings(payload) -> set[str]:
    """Every `<connector>:<asset_type>:<external_id>` string anywhere in a
    nested payload."""
    found: set[str] = set()
    if isinstance(payload, str):
        if _is_ref(payload):
            found.add(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            found |= _ref_shaped_strings(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _ref_shaped_strings(value)
    return found


def reference(ref: str, relation: str = "queries") -> dict:
    """One live incoming reference, shaped as the resolver hands them over.

    `asset_type` travels with the ref rather than being parsed back out of it:
    the ranking never splits a ref, and a test that did would be asserting on a
    string format instead of on the signal.
    """
    return {"ref": ref, "asset_type": ref.split(":")[1], "relation": relation}


CHART_A = "superset:chart:73995395"
CHART_B = "superset:chart:6f4976c2"
CHART_C = "superset:chart:12345678"

# The real counts from tests/fixtures/superset/6.1.0/usage/manifest.json's
# `dataset_reference_counts`, keyed by the dataset uuids this file already uses:
# ae48881d -> 2, 6f4976c2 -> 1, 5bcf01e3 -> 0. Pinned evidence rather than a
# hand-picked spread, so the ordering asserted here is the ordering the captured
# estate actually produces (hy-g1y8, PR #148).
FIXTURE_REFERENCE_COUNTS = {PRIMARY: 2, UNGOVERNED: 1, DIMENSION: 0}


def referenced(ref: str, count: int, relation: str = "queries") -> ObservedSource:
    charts = (CHART_A, CHART_B, CHART_C)
    return source(ref, referenced_by=tuple(reference(charts[i], relation) for i in range(count)))


def test_declared_references_decide_among_sources_git_says_nothing_about():
    """The signal doing work, in the only place it is allowed to: Git names
    neither source, so nothing the customer stated is being overruled.

    This is the fixture the bead asked for -- one where the reference count and
    the other signals DISAGREE. Both sources are ungoverned, compute nothing the
    domain declares, and disclose no modification time, so all three older
    signals are exactly equal and the count is the only thing separating them.
    """
    section = rank([referenced(DIMENSION, 0), referenced(PRIMARY, 2)])

    assert refs(section) == [PRIMARY, DIMENSION]
    counted = signal(section["candidates"][0], DECLARED_REFERENCES)
    assert counted["value"]["count"] == 2


def test_moving_only_the_reference_count_changes_places():
    """The independent instrument hq-xneo asks for, and the reason it is not a
    comparison that cannot come out differently.

    A reference count derived from the same graph the ranking reads, compared
    against itself, always agrees. So the count is moved and NOTHING else is:
    same two refs, same absent Git facts, same absent expressions, same absent
    timestamps -- only which of the two carries the charts. If the order did not
    follow, the input would be decorative.
    """
    primary_leads = rank([referenced(DIMENSION, 0), referenced(PRIMARY, 2)])
    dimension_leads = rank([referenced(DIMENSION, 2), referenced(PRIMARY, 0)])

    assert refs(primary_leads) == [PRIMARY, DIMENSION]
    assert refs(dimension_leads) == [DIMENSION, PRIMARY]


def test_the_fixture_reference_counts_order_the_three_captured_datasets():
    """The captured estate's own numbers, not a spread chosen to pass.

    `dataset_reference_counts` in the usage fixture manifest is 2/1/0 across the
    three revenue datasets, so the ranking of the real capture is derivable here
    without Postgres: most-referenced first, and the dataset no seeded chart
    queries last.
    """
    section = rank(
        [referenced(ref, count) for ref, count in sorted(FIXTURE_REFERENCE_COUNTS.items())]
    )

    assert refs(section) == [PRIMARY, UNGOVERNED, DIMENSION]
    assert [
        signal(candidate, DECLARED_REFERENCES)["value"]["count"]
        for candidate in section["candidates"]
    ] == [2, 1, 0]


def test_git_engagement_outranks_a_more_referenced_source():
    """The position of the signal is a claim, so it is tested as one.

    A reference count is the estate's opinion of itself; approval is the
    customer's stated one. Three charts querying a source the commit never names
    does not outrank the source the commit approved, or the ranking would be
    telling a human their own context is wrong.
    """
    governed = GovernedFacts(approved={DIMENSION: ({"domain": "revenue", "role": "primary"},)})

    section = rank([referenced(PRIMARY, 3), referenced(DIMENSION, 0)], governed)

    assert refs(section) == [DIMENSION, PRIMARY]


def test_many_references_do_not_lift_a_source_that_disagrees_with_git():
    """The disagreement fixture, and the property that must survive the new
    input: no count rescues a source the customer's own context prohibits, or a
    source the connector stopped seeing.

    Both sunk sources are the most-referenced in the set, which is what makes
    the assertion worth making -- if the count graded before the disagreement,
    each would place above the clean source it is compared against.
    """
    governed = GovernedFacts(
        prohibited={PRIMARY: ({"domain": "revenue", "reason": "double-counts captures"},)}
    )
    prohibited = source(
        PRIMARY, referenced_by=tuple(reference(chart) for chart in (CHART_A, CHART_B, CHART_C))
    )
    deleted = source(
        UNGOVERNED,
        deleted_at=datetime(2026, 7, 1, tzinfo=UTC),
        referenced_by=(reference(CHART_A), reference(CHART_B)),
    )

    section = rank([prohibited, deleted, referenced(DIMENSION, 0)], governed)

    assert refs(section) == [DIMENSION, UNGOVERNED, PRIMARY]
    assert section["candidates"][0]["disagrees_with_git"] == []


def test_the_reference_signal_names_what_references_it_rather_than_stating_a_number():
    """Rationale discipline: a rank a reader cannot check is a confidence number
    wearing an ordinal, so the count names the refs it counted and the reader can
    go to the source and see the reference.
    """
    section = rank([source(PRIMARY, referenced_by=(reference(CHART_A), reference(CHART_B)))])

    counted = signal(section["candidates"][0], DECLARED_REFERENCES)
    assert counted["value"]["referenced_by"] == [
        {"ref": CHART_A, "relation": "queries"},
        {"ref": CHART_B, "relation": "queries"},
    ]
    assert CHART_A in counted["statement"] and CHART_B in counted["statement"]


def test_the_reference_signal_refuses_the_word_the_bead_forbids():
    """The honest name, asserted rather than trusted to review (hy-d7xh).

    No execution count exists in either source, so neither the signal's name nor
    its statement may imply one. Asserted on both the populated and the empty
    statement, because the empty one is where "nobody queries this" is easiest
    to say by accident.
    """
    populated = signal(
        rank([source(PRIMARY, referenced_by=(reference(CHART_A),))])["candidates"][0],
        DECLARED_REFERENCES,
    )
    empty = signal(rank([source(DIMENSION)])["candidates"][0], DECLARED_REFERENCES)

    for statement in (populated["statement"], empty["statement"]):
        assert "no execution count exists" in statement
        # Phrases that cannot appear inside a denial, so finding one means the
        # statement is asserting usage rather than disclaiming it. "how often" is
        # deliberately absent from this list: both statements DENY it in those
        # words, and a substring check cannot tell a claim from its refusal.
        for forbidden in ("popular", "query frequency", "queried often", "usage count"):
            assert forbidden not in statement
    assert "not how often anyone queried it" in populated["statement"]
    assert "declare a reference to this source" in populated["statement"]
    assert "not evidence that nobody queries it" in empty["statement"]
    assert DECLARED_REFERENCES == "declared_references"


def test_the_relation_the_connector_chose_survives_into_the_value():
    """Two charts querying a dataset and one chart plus one dashboard are
    different facts about a source, and a single total states neither. The
    connector's own word is carried through, never mapped to a house vocabulary.
    """
    mixed = source(
        PRIMARY,
        referenced_by=(
            reference(CHART_A, "queries"),
            reference(CHART_B, "queries"),
            reference("superset:dashboard:aa11", "contains"),
        ),
    )

    counted = signal(rank([mixed])["candidates"][0], DECLARED_REFERENCES)

    assert counted["value"]["count"] == 3
    assert counted["value"]["by_relation"] == {"contains": 1, "queries": 2}


def test_naming_a_referring_asset_adds_no_second_half_to_the_pair():
    """The pair rule, re-derived because this slice added ref-shaped strings to a
    candidate for the first time (ADR 0019 decision 2, hy-01e0's guard).

    The earlier guard could assert that the ONLY ref-shaped string under a
    candidate was the candidate's own. That is no longer true and the weaker
    reading -- "some refs are fine now" -- would retire the guard rather than
    keep it. What actually matters is unchanged: the forbidden pair is (declared
    ref, observed asset version). Every ref named here is an OBSERVED asset's own
    identity, read from `asset_relationships`, and none of them is paired with a
    version id, so no second half exists to pair with a first.

    A referring asset is resolved within the same connection as the asset it
    points at (`hyperset/connectors/sync.py`), so the connector in a referring
    ref is the candidate's own connector and not a second identity space.
    """
    governed = GovernedFacts(
        approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)},
        prohibited={PRIMARY: ({"domain": "revenue", "reason": "double-counts captures"},)},
        field_expressions=REVENUE_FIELDS,
    )
    widest = source(
        PRIMARY,
        metric_expressions=("SUM(gross_amount - tax_amount)",),
        source_modified_at=datetime(2026, 7, 20, tzinfo=UTC),
        findings=({"finding_id": "fnd-1", "finding_type": "definition_drift", "severity": "high"},),
        referenced_by=(reference(CHART_A), reference("superset:dashboard:aa11", "contains")),
    )

    section = rank([widest], governed)
    candidate = section["candidates"][0]

    # The fixture is only worth what it discloses, so prove it disclosed it.
    assert signal(candidate, DECLARED_REFERENCES)["value"]["count"] == 2

    # Every ref-shaped string is an observed asset's, and the candidate's own is
    # the only one the candidate speaks for.
    assert _ref_shaped_strings(candidate) == {widest.ref, CHART_A, "superset:dashboard:aa11"}
    assert [key for key in candidate if key.endswith("ref")] == ["ref"]
    assert candidate["ref"] == widest.ref

    # And no referring asset carries a version, so it cannot be half of a pair.
    referring = signal(candidate, DECLARED_REFERENCES)["value"]["referenced_by"]
    assert all(set(entry) == {"ref", "relation"} for entry in referring)
    assert _version_bearing_keys(signal(candidate, DECLARED_REFERENCES)) == set()


def _version_bearing_keys(payload) -> set[str]:
    """Every key anywhere under `payload` that names a version."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if "version" in key:
                found.add(key)
            found |= _version_bearing_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _version_bearing_keys(value)
    return found


@pytest.mark.parametrize(
    "value",
    [
        "superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2",
        # Shipped, under `source_refs`, in
        # hyperset/evals/recordings/governed/revenue_by_region/unidentified.json.
        "datahub:glossary_term:urn:li:glossaryTerm:recognized_revenue",
        "datahub:dataset:urn:li:dataset:(urn:li:dataPlatform:postgres,revenue.orders,PROD)",
    ],
)
def test_the_ref_predicate_sees_a_urn_bearing_ref(value):
    """The guard is worth only what it can recognise, and a DataHub external_id
    is a urn: two of these carry five and eight colons, and a pattern that
    forbade colons inside the external_id matched neither (hy-ezc3)."""
    assert _is_ref(value)


@pytest.mark.parametrize(
    "value",
    [
        # The three colon-bearing strings the widest candidate above discloses.
        "2026-07-20T00:00:00+00:00",
        "the source last reported a modification at 2026-07-20T00:00:00+00:00",
        "the 'revenue' domain's Git context prohibits this source: double-counts captures",
        # The right shape, an unknown vocabulary on each side in turn.
        "tableau:dataset:abc",
        "superset:metric:abc",
        # Nothing after the prefix.
        "superset:dataset:",
    ],
)
def test_the_ref_predicate_rejects_what_a_candidate_actually_discloses(value):
    assert not _is_ref(value)


# --- The proposal (hy-gh-124 slice 2) ---------------------------------------
#
# hy-gh-124's acceptance asks for a "canonical suggestion" and ADR 0019 floor 1
# forbids that word in a field or in prose, so what is tested here is the thing
# the issue wanted under the name the ADR permits: one candidate put forward
# when the evidence separates it, and a stated refusal to name one when it does
# not. The declines are tested as thoroughly as the proposal, because a
# mechanism that proposes something on thin evidence is the failure mode.


def proposal(section) -> dict:
    return section["proposal"]


def test_the_source_git_already_approves_is_the_one_put_forward():
    section = rank(
        [source(PRIMARY), source(UNGOVERNED)],
        GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)}),
    )

    assert proposal(section)["outcome"] == PROPOSED
    assert proposal(section)["ref"] == PRIMARY
    assert proposal(section)["basis"] == [GIT_ENGAGEMENT]


def test_the_proposal_is_labelled_observed_like_everything_else_here():
    """ADR 0019 floor 1, on the one field most likely to grow a better word."""
    section = rank(
        [source(PRIMARY)],
        GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)}),
    )

    assert proposal(section)["governance"] == OBSERVED
    assert not re.search(r"canonical|approved for|trusted", proposal(section)["statement"])


def test_expression_agreement_alone_can_carry_a_proposal():
    """The commit declares an expression this source computes, and says nothing
    else about it. That is Git-relative evidence and it is enough."""
    section = rank(
        [
            source(PRIMARY, metric_expressions=("SUM(gross_amount - tax_amount)",)),
            source(UNGOVERNED),
        ],
        GovernedFacts(field_expressions=REVENUE_FIELDS),
    )

    assert proposal(section)["ref"] == PRIMARY
    assert proposal(section)["basis"] == [EXPRESSION_AGREEMENT]


def test_a_lone_candidate_git_engages_is_proposed():
    """Nothing to be separated from, so separation is vacuous and the
    Git-relative floor is what decides."""
    section = rank(
        [source(PRIMARY)],
        GovernedFacts(evidence={PRIMARY: ({"domain": "revenue", "term": "revenue"},)}),
    )

    assert proposal(section)["outcome"] == PROPOSED
    assert proposal(section)["ref"] == PRIMARY


def test_freshness_alone_never_carries_a_proposal():
    """The newest source in an estate the commit says nothing about is rank 1
    and is NOT put forward: a timestamp says a source moved, not that it is the
    one this claim needs."""
    section = rank(
        [
            source(PRIMARY, source_modified_at=datetime(2026, 7, 1, tzinfo=UTC)),
            source(UNGOVERNED, source_modified_at=datetime(2020, 1, 1, tzinfo=UTC)),
        ]
    )

    assert refs(section)[0] == PRIMARY
    assert proposal(section)["outcome"] == NO_GIT_RELATIVE_SIGNAL
    assert proposal(section)["ref"] is None
    assert proposal(section)["basis"] == []


def test_two_equally_engaged_sources_are_left_as_a_tie():
    """Breaking it would mean promoting the freshness tiebreak that merely
    ordered them into a finding."""
    both = ({"domain": "revenue", "role": "primary"},)
    section = rank(
        [
            source(PRIMARY, source_modified_at=datetime(2026, 7, 1, tzinfo=UTC)),
            source(DIMENSION, source_modified_at=datetime(2020, 1, 1, tzinfo=UTC)),
        ],
        GovernedFacts(approved={PRIMARY: both, DIMENSION: both}),
    )

    assert len(section["candidates"]) == 2
    assert proposal(section)["outcome"] == NOT_SEPARATED
    assert proposal(section)["ref"] is None
    assert PRIMARY in proposal(section)["statement"]
    assert DIMENSION in proposal(section)["statement"]


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_the_serving_bound_cannot_turn_a_tie_into_a_proposal(limit):
    """The same tie, served at three bounds. A proposal is a claim about the
    estate, so it must not depend on how many candidates were shown (hy-ica2).

    Decided from the truncated list it did depend on one. Measured at 25c0608,
    two runs differing only in `limit`: at 5 the outcome was
    `not_separated_from_the_next_candidate` with `ref` null, and at 1 the tie
    partner fell off the list, the separation scan found nothing to compare
    against, and the outcome was `proposed` with a statement reading "is the one
    observed source the configured Git context separates from the rest" -- false
    on an estate where nothing separated it. The ref put forward was whichever
    side won the stable freshness tiebreak.

    Not reachable through the served surface today: `candidate_sources` has one
    caller and it passes no limit. `limit` is a parameter of this function, so
    the property is tested where it lives rather than through a caller that
    cannot yet vary it -- and hy-uh9q, which wants assist to honour
    `context_budget`, is the change that would make it reachable.
    """
    both = ({"domain": "revenue", "role": "primary"},)
    section = rank(
        [
            source(PRIMARY, source_modified_at=datetime(2026, 7, 1, tzinfo=UTC)),
            source(DIMENSION, source_modified_at=datetime(2020, 1, 1, tzinfo=UTC)),
        ],
        GovernedFacts(approved={PRIMARY: both, DIMENSION: both}),
        limit=limit,
    )

    assert section["returned"] == min(limit, 2)
    assert section["considered"] == 2
    assert proposal(section)["outcome"] == NOT_SEPARATED
    assert proposal(section)["ref"] is None
    # The cost, asserted rather than left as a comment: at limit=1 the runner-up
    # that blocked the proposal is named by a statement the client cannot check
    # against the served list. A decline that names what it weighed is the right
    # side to fail on; a confident sentence about evidence the bound hid is not.
    assert DIMENSION in proposal(section)["statement"]
    assert (DIMENSION in refs(section)) == (limit >= 2)


def test_the_bound_still_cannot_serve_a_proposal_for_a_candidate_it_hid():
    """The other direction of the same fix: deciding over the whole ranking must
    not put forward a ref the reader was never given. At every bound >= 1 the
    leader is served by construction, so this only bites at zero -- which is why
    it is asserted rather than assumed."""
    section = rank(
        [source(PRIMARY)],
        GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)}),
        limit=0,
    )

    assert refs(section) == []
    assert proposal(section)["ref"] is None


def test_a_prohibited_source_is_ranked_and_never_proposed():
    """Floor 6 keeps it on the list with its reason; what it does not get is
    Hyperset putting it forward."""
    section = rank(
        [source(PRIMARY)],
        GovernedFacts(
            approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)},
            prohibited={PRIMARY: ({"domain": "revenue", "reason": "deprecated"},)},
        ),
    )

    assert refs(section) == [PRIMARY]
    assert proposal(section)["outcome"] == EVERY_CANDIDATE_DISAGREES
    assert proposal(section)["ref"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"deleted_at": datetime(2026, 7, 1, tzinfo=UTC)},
        {
            "findings": (
                {"finding_id": "f-1", "finding_type": "expression_drift", "severity": "error"},
            )
        },
        {
            "findings": (
                {
                    "finding_id": "f-2",
                    "finding_type": "approved_expression_undecidable",
                    "severity": "warning",
                },
            )
        },
    ],
    ids=["deleted", "open_finding", "undecided_finding"],
)
def test_any_disagreement_withholds_the_proposal_not_just_prohibition(overrides):
    """Every disagreement withholds, including the undecided one, which `_order`
    no longer sinks as far as the rest (hy-qbii).

    The docstring used to give the reason as "`_order` sinks all three the same
    way", and that reason stopped being true when the middle tier arrived. The
    assertion did not, and the two must not be confused: an undecided pair is
    ranked ahead of a contradiction because it is a smaller disagreement, and is
    still not PROPOSED because Hyperset could not settle whether this source
    computes what Git declares. Putting it forward would be proposing on
    evidence the module itself could not read -- the exact thing the proposal
    rule exists to refuse -- so the ordering change deliberately stops at the
    order."""
    section = rank(
        [source(PRIMARY, **overrides)],
        GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)}),
    )

    assert proposal(section)["outcome"] == EVERY_CANDIDATE_DISAGREES
    assert proposal(section)["ref"] is None


def test_a_clean_source_outranks_a_disagreeing_one_and_is_proposed():
    """The pair that shows the withholding is about the LEADER and not about
    the list: the same disagreeing source, with a clean engaged one beside it,
    and now a proposal exists."""
    section = rank(
        [
            source(DIMENSION, deleted_at=datetime(2026, 7, 1, tzinfo=UTC)),
            source(PRIMARY),
        ],
        GovernedFacts(
            approved={
                PRIMARY: ({"domain": "revenue", "role": "primary"},),
                DIMENSION: ({"domain": "revenue", "role": "dimension"},),
            }
        ),
    )

    assert refs(section) == [PRIMARY, DIMENSION]
    assert proposal(section)["outcome"] == PROPOSED
    assert proposal(section)["ref"] == PRIMARY


def test_an_unpublished_outcome_fails_at_construction():
    """The gate `FINDING_TYPES` already has: a value that would reach a client
    without being published fails where it is built rather than on the wire.

    Called directly, because every call site inside this module passes a
    published constant -- so the gate is unreachable from the public function
    and testing it through one would be testing nothing."""
    with pytest.raises(ValueError) as raised:
        _declined("looks_plausible", "invented outcome")

    assert "looks_plausible" in str(raised.value)


def test_every_published_outcome_is_reachable():
    """The other direction, and the honest name for it: not that the gate
    fires, but that no published outcome is a word with no world behind it."""
    engaged = GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)})
    tied = ({"domain": "revenue", "role": "primary"},)
    sections = [
        rank([source(PRIMARY)], engaged),
        rank([source(PRIMARY)]),
        rank(
            [source(PRIMARY), source(DIMENSION)],
            GovernedFacts(approved={PRIMARY: tied, DIMENSION: tied}),
        ),
        rank(
            [source(PRIMARY, deleted_at=datetime(2026, 7, 1, tzinfo=UTC))],
            engaged,
        ),
        rank([source(PRIMARY)], engaged, domain_is_configured=False),
    ]

    assert {proposal(section)["outcome"] for section in sections} == set(PROPOSAL_OUTCOMES)


def test_an_unconfigured_domain_declines_the_proposal_the_same_estate_would_have_carried():
    """hy-xq55's decisive pair, and the reason the mayor's named mechanism was
    not usable.

    ONE estate, ONE set of governed facts, ONE flag moved. `domain_is_configured`
    is the only difference between these two calls, so a green here cannot come
    from the sources, the ranking, or an accident of the fixture.

    MEASURED, not asserted (the mayor's negative control on this bead). Replacing
    `_proposal`'s `if not domain_is_configured` with `if False` -- the whole gate
    and nothing else -- kills four arms in this file: this one, the two below it,
    and `test_every_published_outcome_is_reachable`, which goes red because the
    fifth outcome stops being producible at all. The wired arm dies with them:
    `tests/postgres/.../test_an_unknown_domain_grows_a_candidate_list_that_can
    _never_carry_a_proposal` fails with
    `'not_separated_from_the_next_candidate' == 'no_governing_domain'`, which is
    the real estate returning an estate-shaped decline for a request-shaped
    fact. Source restored from a copy taken before the mutation and verified
    byte-identical, never from `HEAD`.

    The `finance` approval is the whole point: the ruling asked whether
    engagement by a domain the caller did NOT name can license a proposal for a
    question no domain governs, and the answer is no (ADR 0019 decisions 1 and
    2(c)). It is a governed fact about `finance`.
    """
    estate = [source(PRIMARY)]
    elsewhere = GovernedFacts(approved={PRIMARY: ({"domain": "finance", "role": "primary"},)})

    configured = proposal(rank(estate, elsewhere))
    unconfigured = proposal(rank(estate, elsewhere, domain_is_configured=False))

    assert configured["outcome"] == PROPOSED
    assert configured["ref"] == PRIMARY
    assert unconfigured["outcome"] == NO_GOVERNING_DOMAIN
    assert unconfigured["ref"] is None


def test_the_unconfigured_decline_is_not_the_no_git_relative_signal_one():
    """The correction stated as a test rather than only in a docstring.

    `NO_GIT_RELATIVE_SIGNAL` was the outcome the ruling named for this case, on
    the premise that both proposing signals go structurally zero without a named
    domain. `git_engagement` does not: it is built from every configured source,
    so it is readable and nonzero here. Serving that outcome would attach the
    sentence "no observed source here is engaged by ... configured Git context"
    to a candidate whose own `git_engagement` value names the domain that
    engages it -- one section contradicting itself in two fields.

    So this asserts BOTH halves: the outcome is the new one, and the signal the
    old outcome would have denied is present and populated.
    """
    section = rank(
        [source(PRIMARY)],
        GovernedFacts(approved={PRIMARY: ({"domain": "finance", "role": "primary"},)}),
        domain_is_configured=False,
    )

    assert proposal(section)["outcome"] != NO_GIT_RELATIVE_SIGNAL
    assert proposal(section)["outcome"] == NO_GOVERNING_DOMAIN
    engagement = signal(section["candidates"][0], GIT_ENGAGEMENT)
    assert engagement["value"]["approved_by"] == [{"domain": "finance", "role": "primary"}]


def test_an_unconfigured_domain_still_gets_the_ranked_list():
    """ "A LIST, never a PROPOSAL" -- the decline is attached to a served list,
    not used to suppress one. Silence where governance is silent is the defect
    ADR 0019 exists to fix, and this refusal is maximal governance silence: the
    caller named a domain nothing declares, which is hy-gh-124's headline case.
    """
    section = rank([source(PRIMARY), source(DIMENSION)], domain_is_configured=False)

    # Neither carries a governed fact and neither discloses a modification time,
    # so `_order` falls through to its last component and sorts on `ref` -- which
    # is why `DIMENSION` leads. A stable order that carries no claim about which
    # is right is the correct output here, not an accident of the fixture.
    assert refs(section) == [DIMENSION, PRIMARY]
    assert section["considered"] == 2
    assert [candidate["rank"] for candidate in section["candidates"]] == [1, 2]
    assert all(candidate["governance"] == OBSERVED for candidate in section["candidates"])


def test_the_unconfigured_decline_outranks_every_estate_shaped_reason():
    """Ordered ahead of the other declines, because it is the only one that is
    a property of the REQUEST rather than of how the ranking came out.

    Both estates below would decline anyway -- one where every candidate
    disagrees with Git, one where two sources are engaged equally -- so a test
    that only checked "it declines" would pass with the gate removed. Checking
    WHICH decline is what discriminates: with the gate gone these report
    `every_candidate_disagrees_with_git` and `not_separated...`, both of which
    describe the estate and neither of which says the question has no governed
    subject.
    """
    tied = ({"domain": "finance", "role": "primary"},)
    every_candidate_disagrees = rank(
        [source(PRIMARY, deleted_at=datetime(2026, 7, 1, tzinfo=UTC))],
        GovernedFacts(approved={PRIMARY: tied}),
        domain_is_configured=False,
    )
    not_separated = rank(
        [source(PRIMARY), source(DIMENSION)],
        GovernedFacts(approved={PRIMARY: tied, DIMENSION: tied}),
        domain_is_configured=False,
    )

    assert proposal(every_candidate_disagrees)["outcome"] == NO_GOVERNING_DOMAIN
    assert proposal(not_separated)["outcome"] == NO_GOVERNING_DOMAIN
    # And the fact each one would have reported is still on the wire, which is
    # what makes the ordering free: the reader loses no evidence, only a label.
    assert every_candidate_disagrees["candidates"][0]["disagrees_with_git"]


def test_the_disclosure_says_the_domain_is_unconfigured_rather_than_thin():
    """A reader told "no governed context covers 'churn' in the 'revenue'
    domain" goes looking for a `revenue` domain to add `churn` to. When nothing
    declares `revenue` at all there is no such place, and the disclosure has to
    say so or it sends them after a domain that does not exist."""
    unconfigured = rank([source(PRIMARY)], domain_is_configured=False)["disclosure"]
    covered = rank([source(PRIMARY)])["disclosure"]

    assert "declares the 'revenue' domain at all" in unconfigured
    assert "No governed context covers" not in unconfigured
    assert "No governed context covers 'churn' in the 'revenue' domain" in covered
    # The ADR-mandated tail is one string reached both ways, so it cannot drift.
    tail = "these are observed sources, ranked by stated signal"
    assert tail in unconfigured and tail in covered


def test_the_proposal_is_inside_the_assist_identity():
    """It is assist content, so it hashes into `assist_id` and never into
    `bundle_id` (ADR 0019 floor 8). Two estates differing only in whether a
    proposal was made must not share an identity."""
    engaged = GovernedFacts(approved={PRIMARY: ({"domain": "revenue", "role": "primary"},)})

    assert rank([source(PRIMARY)], engaged)["assist_id"] != rank([source(PRIMARY)])["assist_id"]
