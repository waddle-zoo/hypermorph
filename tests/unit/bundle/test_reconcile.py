"""Disagreements the resolver builds from two sides it already holds (hy-llk4).

Grouped by the answer, the way `test_equivalence.py` is: what is reported, what
is refused, and what a value that reaches a client cannot be. The refusals carry
the weight here -- ADR 0021 decision 2 is a rule about what NOT to call a
contradiction, and a dimension that reports an absence is worse than one that
reports nothing, because a reader cannot tell the two apart from the payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hyperset.bundle import AnalyticsPlan, ContextBundle, validate_analytics_plan
from hyperset.bundle.reconcile import (
    BUNDLE_RECONCILIATION,
    COMPARATORS,
    CONFLICT_PRODUCERS,
    NAMED_REFERENCES,
    PROCESSOR_FINDING,
    PROHIBITED_BUT_REFERENCED,
    RECONCILED_KINDS,
    SEVERITIES,
    SOURCE_DELETED_WHILE_GOVERNED,
    conflict,
    prohibited_but_referenced,
    source_deleted_while_governed,
)

BANNED = "superset:dataset:partial-captures"
OTHER_BANNED = "superset:dataset:test-fixtures"
GOVERNED = "superset:dataset:orders"
COMMIT = "abc123"

CHART = "superset:chart:executive-revenue"
DASHBOARD = "superset:dashboard:board-pack"


def _prohibited(*refs) -> list[dict]:
    return [{"ref": ref, "reason": "Double-counts partial captures."} for ref in refs]


# What is reported.


def test_a_prohibited_source_the_estate_still_points_at_states_both_sides():
    """The join ADR 0021 decision 4 named and nobody built: Git's side is the
    prohibition and its reason, the estate's side is the live references, and
    neither sentence is the other's summary."""
    (entry,) = prohibited_but_referenced(
        _prohibited(BANNED),
        referenced_by={BANNED: [DASHBOARD, CHART]},
        commit_sha=COMMIT,
    )

    assert entry["kind"] == PROHIBITED_BUT_REFERENCED
    assert entry["ref"] == BANNED
    assert entry["context_says"] == "prohibited: Double-counts partial captures."
    # Named in sorted order, not in the order the estate was walked: this
    # sentence is inside `bundle_id`, so a re-sync that returns the same edges
    # in a different order has to produce the same bundle.
    assert entry["source_says"] == f"2 live references: {CHART}, {DASHBOARD}"
    assert entry["unresolved_since_commit"] == COMMIT


def test_a_governed_source_the_connector_stopped_reporting_states_both_sides():
    """Served today as a `deprecation` whose reason ends "; Git still approves
    it" -- a reader has to parse prose to learn a governed ref is involved, and
    `deprecations` carries no Git side to check it against."""
    (entry,) = source_deleted_while_governed(
        [{"ref": GOVERNED, "asset_type": "dataset", "deleted_at": "2026-07-30T00:00:00Z"}],
        prohibited_refs=[],
        commit_sha=COMMIT,
    )

    assert entry["kind"] == SOURCE_DELETED_WHILE_GOVERNED
    assert entry["ref"] == GOVERNED
    assert entry["context_says"] == "approved as a source of this domain"
    assert entry["source_says"] == "the dataset stopped being reported at 2026-07-30T00:00:00Z"
    assert entry["unresolved_since_commit"] == COMMIT


def test_each_prohibited_source_is_judged_on_its_own_references():
    """One prohibition is not evidence about another, so a referenced one and
    an unreferenced one in the same context produce one entry, not two and not
    none."""
    entries = prohibited_but_referenced(
        _prohibited(BANNED, OTHER_BANNED),
        referenced_by={BANNED: [CHART]},
        commit_sha=COMMIT,
    )

    assert [entry["ref"] for entry in entries] == [BANNED]


# What is refused, and why each refusal is not silence about a real thing.


def test_a_prohibited_source_nothing_references_is_the_prohibition_working():
    """Agreement, not a difference: the customer said not to use it and the
    estate is not using it. It stays disclosed as a `prohibited_by_context`
    deprecation, which is where a prohibition with no dispute belongs."""
    assert prohibited_but_referenced(_prohibited(BANNED), referenced_by={}, commit_sha=COMMIT) == []


def test_a_prohibited_ref_no_observation_carries_is_an_absence():
    """Decision 2 applied to presence. A ref that resolves to no observed asset
    has no second side, and an engine that reported it would be reporting that
    it looked, which `uncorroborated` already does honestly."""
    assert (
        prohibited_but_referenced(
            _prohibited(BANNED), referenced_by={GOVERNED: [CHART]}, commit_sha=COMMIT
        )
        == []
    )


def test_a_deleted_asset_no_commit_approves_is_an_observation():
    """The caller passes only refs the commit declared, so an unapproved
    deletion never reaches here -- asserted because the alternative reading, that
    every deletion is a conflict, is the one a future editor will reach for."""
    assert source_deleted_while_governed([], prohibited_refs=[], commit_sha=COMMIT) == []


def test_a_deleted_source_the_commit_prohibits_is_agreement_twice_over():
    """A commit DECLARES what it forbids -- that is how a prohibition names a
    ref -- so a prohibited source is git-linked evidence and arrives here on
    deletion like any other governed ref. Calling that "approved as a source of
    this domain" would put a false sentence on the Git side, and the pair is
    agreement anyway: the customer said not to use it and the estate stopped
    carrying it.

    Found by `tests/postgres/test_context_bundle.py` sweeping a whole connection,
    not by reading the code, which is why the prohibited set is a keyword the
    caller cannot omit.
    """
    deleted = [
        {"ref": BANNED, "asset_type": "dataset", "deleted_at": "2026-07-30T00:00:00Z"},
        {"ref": GOVERNED, "asset_type": "dataset", "deleted_at": "2026-07-30T00:00:00Z"},
    ]

    entries = source_deleted_while_governed(deleted, prohibited_refs=[BANNED], commit_sha=COMMIT)

    assert [entry["ref"] for entry in entries] == [GOVERNED]


# What a value that reaches a client cannot be.


def test_an_entry_names_the_producer_that_built_it():
    """`conflicts` is mixed-provenance from here on, and ADR 0019 obliges a
    served section to say which input each entry came from. Both producers go
    through one constructor so the label cannot be attached on one path and
    forgotten on the other."""
    (reconciled,) = source_deleted_while_governed(
        [{"ref": GOVERNED, "asset_type": "dataset", "deleted_at": "2026-07-30T00:00:00Z"}],
        prohibited_refs=[],
        commit_sha=COMMIT,
    )
    projected = conflict(
        kind="expression_drift",
        produced_by=PROCESSOR_FINDING,
        severity="error",
        finding_id="fnd-1",
        ref=GOVERNED,
        field="recognized_revenue",
        context_says="SUM(gross_amount)",
        source_says="SUM(gross_amount - tax_amount)",
        unresolved_since_commit=COMMIT,
    )

    assert reconciled["produced_by"] == BUNDLE_RECONCILIATION
    assert reconciled["finding_id"] is None
    assert projected["produced_by"] == PROCESSOR_FINDING
    assert projected["finding_id"] == "fnd-1"
    assert set(reconciled) == set(projected), "both producers serve one entry shape"
    # Every entry carries severity, by provenance (hy-xfhh): the reconciled
    # deletion is its kind's fixed warning; the projection inherited the finding's.
    assert reconciled["severity"] == "warning"
    assert projected["severity"] == "error"


def test_a_producer_no_client_can_read_fails_where_the_entry_is_built():
    with pytest.raises(ValueError, match="unpublished conflict producer"):
        conflict(
            kind=PROHIBITED_BUT_REFERENCED,
            produced_by="curator",
            severity="error",
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )

    for producer in CONFLICT_PRODUCERS:
        finding_id = "fnd-1" if producer == PROCESSOR_FINDING else None
        assert (
            conflict(
                kind=PROHIBITED_BUT_REFERENCED,
                produced_by=producer,
                severity="error",
                finding_id=finding_id,
                ref=BANNED,
                context_says="",
                source_says="",
                unresolved_since_commit=COMMIT,
            )["produced_by"]
            == producer
        )


def test_a_reconciled_kind_no_client_can_read_fails_where_the_entry_is_built():
    """Gated only for the reconciled producer. A projected finding's kind was
    already gated against `FINDING_TYPES` where the candidate was built, and a
    second copy of that register here is the duplication ADR 0021 was written
    about."""
    with pytest.raises(ValueError, match="unpublished reconciled conflict kind"):
        conflict(
            kind="prohibited_but_popular",
            produced_by=BUNDLE_RECONCILIATION,
            severity="error",
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )

    for kind, kind_severity in RECONCILED_KINDS.items():
        entry = conflict(
            kind=kind,
            produced_by=BUNDLE_RECONCILIATION,
            severity=kind_severity,
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )
        assert entry["kind"] == kind
        assert entry["severity"] == kind_severity

    # The processor's kinds are not this register's business, and are passed.
    assert (
        conflict(
            kind="expression_drift",
            produced_by=PROCESSOR_FINDING,
            severity="error",
            finding_id="fnd-1",
            ref=GOVERNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )["kind"]
        == "expression_drift"
    )


def test_an_entry_cannot_claim_a_finding_that_does_not_stand_behind_it():
    """Both directions, because `finding_id` is the field the version number
    moved for: an id invented for a computed entry sends a reader to a review
    task that does not exist, and a projection that dropped its id makes a real
    one unreachable."""
    with pytest.raises(ValueError, match="no finding behind it"):
        conflict(
            kind=PROHIBITED_BUT_REFERENCED,
            produced_by=BUNDLE_RECONCILIATION,
            severity="error",
            finding_id="fnd-1",
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )

    with pytest.raises(ValueError, match="must carry its id"):
        conflict(
            kind="expression_drift",
            produced_by=PROCESSOR_FINDING,
            severity="error",
            ref=GOVERNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )


# The count is the claim; the names are what lets a reader start checking it.


@pytest.mark.parametrize(
    ("referrers", "expected"),
    [
        (["c1"], "1 live reference: c1"),
        (["c2", "c1"], "2 live references: c1, c2"),
        ([f"c{index}" for index in range(1, NAMED_REFERENCES + 1)], None),
    ],
)
def test_the_references_a_prohibited_source_has_are_counted_and_sampled(referrers, expected):
    (entry,) = prohibited_but_referenced(
        _prohibited(BANNED), referenced_by={BANNED: referrers}, commit_sha=COMMIT
    )
    said = entry["source_says"]

    assert said.startswith(f"{len(referrers)} live reference")
    assert "more" not in said, "nothing is withheld at or under the sample bound"
    if expected:
        assert said == expected


def test_a_source_with_more_referrers_than_the_sample_states_the_remainder():
    """The case this dimension most wants to report is the one that would blow
    the context budget reporting it, so the payload is bounded and says so
    rather than being cut whole by `context_budget`."""
    referrers = [f"superset:chart:c{index:02d}" for index in range(NAMED_REFERENCES + 3)]

    (entry,) = prohibited_but_referenced(
        _prohibited(BANNED), referenced_by={BANNED: list(reversed(referrers))}, commit_sha=COMMIT
    )

    assert entry["source_says"] == (
        f"{len(referrers)} live references: {', '.join(referrers[:NAMED_REFERENCES])}, and 3 more"
    )


# What these deliberately do not reach.


def _bundle_with(conflicts) -> ContextBundle:
    return ContextBundle(
        request={"query": "recognized revenue by region"},
        resolution={"status": "governed", "summary": "", "warnings": []},
        context_authority={
            "type": "git",
            "commit_sha": COMMIT,
            "context_snapshot_id": "ctxsnap-1",
        },
        instructions={
            "definitions": [],
            "approved_sources": [{"ref": GOVERNED, "role": "primary", "reason": "Orders."}],
            "prohibited_sources": [],
            "fields": [
                {
                    "name": "recognized_revenue",
                    "source_ref": GOVERNED,
                    "expression": "SUM(gross_amount)",
                }
            ],
            "joins": [],
            "filters": [],
            "grain": None,
            "caveats": [],
            "validations": [],
            "context_doc": "...",
        },
        linked_evidence={
            "observed_assets": [{"ref": GOVERNED, "governance": "git_linked"}],
            "findings": [],
            "conflicts": conflicts,
        },
        domain_graph={"nodes": [], "edges": []},
        provenance_refs=[f"git_context:ctxsnap-1@{COMMIT}"],
        resolved_at=datetime(2026, 7, 28, tzinfo=UTC),
    )


def test_a_reconciled_conflict_cannot_become_a_disputed_field():
    """Neither dimension is about a field, so neither carries one, and plan
    validation selects `disputed_field` by matching `conflict["field"]` against
    the planned fields. Asserted rather than reasoned because that selection
    lives in another owner's module: if it ever stops filtering on `field`, a
    prohibition would be served as a warning about a measure it says nothing
    about.

    The canary is the second arm. Without it the first passes just as well
    against a validator that emits no `disputed_field` at all.
    """
    plan = AnalyticsPlan(source_refs=[GOVERNED], fields=["recognized_revenue"])
    reconciled = prohibited_but_referenced(
        _prohibited(BANNED), referenced_by={BANNED: [CHART]}, commit_sha=COMMIT
    )

    silent = validate_analytics_plan(bundle=_bundle_with(reconciled), plan=plan)
    assert [violation.code for violation in silent.violations] == []

    disputed = validate_analytics_plan(
        bundle=_bundle_with(
            [
                conflict(
                    kind="expression_drift",
                    produced_by=PROCESSOR_FINDING,
                    severity="error",
                    finding_id="fnd-1",
                    ref=GOVERNED,
                    field="recognized_revenue",
                    context_says="SUM(gross_amount)",
                    source_says="SUM(gross_amount - tax_amount)",
                    unresolved_since_commit=COMMIT,
                )
            ]
        ),
        plan=plan,
    )
    assert [violation.code for violation in disputed.violations] == ["disputed_field"]


def test_severity_is_gated_and_stamped_by_provenance():
    """conflicts[].severity (hy-xfhh): every entry carries it, gated against the
    published register, and assigned by provenance -- a reconciled kind's FIXED
    severity, never a computed one."""
    # error is the most-severe value, first in the register -- the default-deny
    # anchor (an unrecognised severity is treated as this one).
    assert SEVERITIES[0] == "error"

    # An unpublished severity fails at CONSTRUCTION, not on the wire, the same
    # shape that gates the producer and kind registers.
    with pytest.raises(ValueError, match="unpublished conflict severity"):
        conflict(
            kind=PROHIBITED_BUT_REFERENCED,
            produced_by=BUNDLE_RECONCILIATION,
            severity="catastrophic",
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )

    # THE SHARED CONSTRUCTOR IS THE INVARIANT BOUNDARY: a reconciled kind's
    # severity is a governance constant, so constructing one with the WRONG (but
    # published) severity is refused here, not trusted to the emitters. Without
    # this a caller could serve a prohibited-but-referenced conflict as a mere
    # `warning` and bypass its declared `error`.
    with pytest.raises(ValueError, match="fixed severity"):
        conflict(
            kind=PROHIBITED_BUT_REFERENCED,  # declared `error`
            produced_by=BUNDLE_RECONCILIATION,
            severity="warning",
            ref=BANNED,
            context_says="",
            source_says="",
            unresolved_since_commit=COMMIT,
        )

    # The fixed per-kind severity is stamped end to end by the reconciler, not
    # passed in by a caller who could vary it.
    (prohibited,) = prohibited_but_referenced(
        _prohibited(BANNED), referenced_by={BANNED: [CHART]}, commit_sha=COMMIT
    )
    assert prohibited["severity"] == RECONCILED_KINDS[PROHIBITED_BUT_REFERENCED] == "error"
    (deleted,) = source_deleted_while_governed(
        [{"ref": GOVERNED, "asset_type": "dataset", "deleted_at": "2026-07-30T00:00:00Z"}],
        prohibited_refs=[],
        commit_sha=COMMIT,
    )
    assert deleted["severity"] == RECONCILED_KINDS[SOURCE_DELETED_WHILE_GOVERNED] == "warning"


def test_the_join_dispatch_refuses_an_unpublished_value_kind():
    """The dispatch is gated like the other registers (hy-gl39): a value kind
    with no comparator fails at construction, not on the wire. A new joinable
    FIELD needs no change; a new value KIND is a reviewed change to COMPARATORS."""
    from hyperset.bundle.reconcile import JoinPair, reconcile

    with pytest.raises(ValueError, match="unpublished conflict value kind"):
        reconcile([JoinPair(value_kind="fuzzy_match", declared="a", observed="b", entry={})])


def test_a_persisted_undecidable_finding_still_travels_as_a_conflict():
    """Regression (hy-gl39): a persisted processor finding is PROJECTED, not
    re-decided. `approved_expression_undecidable` (SUM(o.amount) vs SUM(amount)
    -> UNDECIDED under compare_fragments) stays in `.findings`, so dropping it
    from `linked_evidence.conflicts` -- which routing the projection through the
    DIFFERENT-only expression comparator did -- is a silent served-output change.
    The projection trusts the finding, exactly as it did before the mechanism."""
    from types import SimpleNamespace

    from hyperset.bundle.equivalence import UNDECIDED, compare_fragments
    from hyperset.bundle.resolver import _conflict

    git_expr, observed_expr = "SUM(o.amount)", "SUM(amount)"
    assert compare_fragments(git_expr, observed_expr) == UNDECIDED  # the premise

    finding = SimpleNamespace(
        id="fnd-undecided",
        finding_type="approved_expression_undecidable",
        severity="warning",
        evidence={
            "git": {"expression": git_expr, "commit_sha": COMMIT},
            "observed": {"expression": observed_expr},
            "field": "recognized_revenue",
        },
    )

    entry = _conflict(GOVERNED, finding)

    assert entry is not None, "an UNDECIDED persisted finding must still travel as a conflict"
    assert entry["produced_by"] == PROCESSOR_FINDING
    assert entry["finding_id"] == "fnd-undecided"
    assert entry["context_says"] == git_expr
    assert entry["source_says"] == observed_expr


def test_a_projected_pair_always_emits_regardless_of_the_values():
    """The PROJECTED comparator trusts the persisted decision, so it emits even
    when the two sides would be UNDECIDED or EQUIVALENT as a bundle join."""
    from hyperset.bundle.reconcile import PROJECTED, JoinPair, reconcile

    pair = JoinPair(
        value_kind=PROJECTED,
        declared="SUM(o.amount)",
        observed="SUM(amount)",  # UNDECIDED as a join, but this is a projection
        entry=dict(
            kind="approved_expression_undecidable",
            produced_by=PROCESSOR_FINDING,
            severity="warning",
            finding_id="fnd-1",
            ref=GOVERNED,
            context_says="SUM(o.amount)",
            source_says="SUM(amount)",
            unresolved_since_commit=COMMIT,
        ),
    )
    assert len(reconcile([pair])) == 1


def test_ownership_mismatch_reconciles_only_a_declared_bridge():
    """hy-ocbd (ADR-0021 dec4 unlock): ownership is reconciled ONLY where the
    customer DECLARED an identity bridge in Git; never inferred across identifier
    spaces (team:finance-data vs urn:li:corpuser:...), which would be a false
    error finding. EQUALITY via the `identity` value kind."""
    from hyperset.bundle.reconcile import IDENTITY, OWNERSHIP_MISMATCH, ownership_mismatch

    TEAM = "team:finance-data"

    # DECLARED bridge, estate reports a DIFFERENT identity -> one ownership_mismatch.
    (entry,) = ownership_mismatch(
        {TEAM: "urn:li:corpuser:a"},
        observed_owner={TEAM: "urn:li:corpuser:b"},
        commit_sha=COMMIT,
    )
    assert entry["kind"] == OWNERSHIP_MISMATCH
    assert entry["produced_by"] == BUNDLE_RECONCILIATION
    assert entry["severity"] == "warning"
    assert entry["ref"] == TEAM
    assert "urn:li:corpuser:a" in entry["context_says"]
    assert "urn:li:corpuser:b" in entry["source_says"]
    assert entry["unresolved_since_commit"] == COMMIT

    # DECLARED bridge, estate MATCHES it -> agreement, nothing.
    assert (
        ownership_mismatch(
            {TEAM: "urn:li:corpuser:a"},
            observed_owner={TEAM: "urn:li:corpuser:a"},
            commit_sha=COMMIT,
        )
        == []
    )

    # UNDECLARED bridge (empty), estate reports an owner -> NOTHING. No inference
    # across identifier spaces; default-deny is the whole point of the unlock.
    assert (
        ownership_mismatch({}, observed_owner={TEAM: "urn:li:corpuser:b"}, commit_sha=COMMIT) == []
    )

    # DECLARED bridge, but the estate reports no owner for that ref -> absence,
    # not a conflict (ADR-0021 decision 2).
    assert (
        ownership_mismatch({TEAM: "urn:li:corpuser:a"}, observed_owner={}, commit_sha=COMMIT) == []
    )

    # The comparator is exact equality of two declared identity strings.
    assert IDENTITY in COMPARATORS
    assert COMPARATORS[IDENTITY]("urn:li:corpuser:a", "urn:li:corpuser:b") is True
    assert COMPARATORS[IDENTITY]("urn:li:corpuser:a", "urn:li:corpuser:a") is False


def test_grain_mismatch_reconciles_only_a_declared_grain():
    """hy-868w (ADR-0021 dec4 unlock): grain is reconciled ONLY from a Git-DECLARED
    grain -- never inferred from column names. Exact reuses the `identity` value
    kind; a declared `rollup` relation uses the `grain_rollup` kind. Both decide
    from the two declared descriptors only."""
    from hyperset.bundle.reconcile import (
        GRAIN_MISMATCH,
        GRAIN_ROLLUP,
        GRAIN_ROLLUP_RELATION,
        grain_mismatch,
    )

    SRC = "table:postgres:analytics.public.orders"

    # EXACT: a declared grain the estate does not match -> one grain_mismatch.
    (entry,) = grain_mismatch(
        SRC, ["region", "month"], observed_grain=["region"], commit_sha=COMMIT
    )
    assert entry["kind"] == GRAIN_MISMATCH
    assert entry["produced_by"] == BUNDLE_RECONCILIATION
    assert entry["severity"] == "warning"
    assert entry["ref"] == SRC

    # EXACT agreement: order and case are not part of a grain's meaning.
    assert (
        grain_mismatch(
            SRC, ["Region", "month"], observed_grain=["month", "region"], commit_sha=COMMIT
        )
        == []
    )

    # UNDECLARED grain (empty / None) -> NOTHING (default-deny; never inferred).
    assert grain_mismatch(SRC, [], observed_grain=["region"], commit_sha=COMMIT) == []
    assert grain_mismatch(SRC, None, observed_grain=["region"], commit_sha=COMMIT) == []

    # Absent observed grain -> absence, not a conflict (ADR-0021 dec2).
    assert grain_mismatch(SRC, ["region"], observed_grain=[], commit_sha=COMMIT) == []

    # ROLLUP: observed is a valid rollup (subset -> coarser) of the declared grain
    # -> agreement.
    assert (
        grain_mismatch(
            SRC,
            ["region", "month"],
            observed_grain=["region"],
            relation=GRAIN_ROLLUP_RELATION,
            commit_sha=COMMIT,
        )
        == []
    )
    # ROLLUP mismatch: the estate reports a FINER dimension the declared grain does
    # not name -> not a valid rollup, emit.
    (rollup_entry,) = grain_mismatch(
        SRC,
        ["region"],
        observed_grain=["region", "month"],
        relation=GRAIN_ROLLUP_RELATION,
        commit_sha=COMMIT,
    )
    assert rollup_entry["kind"] == GRAIN_MISMATCH

    # The rollup comparator is set containment over declared descriptors.
    assert COMPARATORS[GRAIN_ROLLUP](frozenset({"a"}), frozenset({"a", "b"})) is True
    assert COMPARATORS[GRAIN_ROLLUP](frozenset({"a", "b"}), frozenset({"a"})) is False

    # FAIL-CLOSED: `relation` is a declared control, so an unknown/typo value must
    # REFUSE, not silently select the exact comparator (a fail-open edge would
    # reconcile under the wrong rule).
    with pytest.raises(ValueError, match="unrecognised declared grain relation"):
        grain_mismatch(
            SRC,
            ["region"],
            observed_grain=["region", "month"],
            relation="rollupp",  # typo
            commit_sha=COMMIT,
        )


def test_freshness_stale_reconciles_only_a_declared_threshold_with_injected_now():
    """hy-d9ys (ADR-0021 dec4 unlock): freshness is reconciled ONLY from a
    Git-DECLARED threshold, against a resolve-clock `now` that is INJECTED --
    never the wall clock -- so the verdict is deterministic and can sit inside
    `bundle_id`. The existing served `freshness` observation is untouched."""
    from hyperset.bundle.reconcile import FRESHNESS_STALE, TEMPORAL, freshness_stale

    SRC = "table:postgres:analytics.public.orders"
    NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    DAY = timedelta(hours=24)

    # DECLARED threshold, observed older than the cutoff (now - threshold) -> stale.
    (entry,) = freshness_stale(SRC, DAY, NOW - timedelta(hours=48), now=NOW, commit_sha=COMMIT)
    assert entry["kind"] == FRESHNESS_STALE
    assert entry["produced_by"] == BUNDLE_RECONCILIATION
    assert entry["severity"] == "warning"
    assert entry["ref"] == SRC

    # Within the threshold -> agreement, nothing.
    assert freshness_stale(SRC, DAY, NOW - timedelta(hours=1), now=NOW, commit_sha=COMMIT) == []

    # NO declared threshold -> NOTHING (default-deny; never fabricated).
    assert freshness_stale(SRC, None, NOW - timedelta(hours=48), now=NOW, commit_sha=COMMIT) == []

    # Absent observed modification time -> absence, not a conflict (dec2).
    assert freshness_stale(SRC, DAY, None, now=NOW, commit_sha=COMMIT) == []

    # An ISO-string observed time parses the same as a datetime.
    (iso_entry,) = freshness_stale(
        SRC, DAY, (NOW - timedelta(hours=48)).isoformat(), now=NOW, commit_sha=COMMIT
    )
    assert iso_entry["kind"] == FRESHNESS_STALE

    # DETERMINISTIC: the same injected `now` always yields the same output.
    stale = (SRC, DAY, NOW - timedelta(hours=48))
    assert freshness_stale(*stale, now=NOW, commit_sha=COMMIT) == freshness_stale(
        *stale, now=NOW, commit_sha=COMMIT
    )

    # `now` is REQUIRED: the mechanism has NO wall-clock fallback, so a caller that
    # forgot the deterministic clock input fails loudly rather than silently reading
    # wall-time. Omitting it is a TypeError.
    with pytest.raises(TypeError):
        freshness_stale(SRC, DAY, NOW - timedelta(hours=48), commit_sha=COMMIT)

    # The temporal comparator is a pure cutoff test: observed before the cutoff.
    assert COMPARATORS[TEMPORAL](NOW, NOW - timedelta(hours=1)) is True
    assert COMPARATORS[TEMPORAL](NOW, NOW + timedelta(hours=1)) is False
