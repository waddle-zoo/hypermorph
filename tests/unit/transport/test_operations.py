"""The one place a request becomes an operation (hy-oih).

Both transports decode through this module, so a rule proved here is proved
for HTTP and MCP at once.
"""

from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace

import pytest

from hyperset.bundle import CATALOG_DEFAULT_LIMIT, CATALOG_MAX_LIMIT
from hyperset.transport import operations
from hyperset.transport.operations import (
    OPERATION_SPECS,
    OPERATIONS,
    OperationError,
    run_operation,
)
from tests.unit.transport.conftest import (
    DIRECTIVE,
    PRIMARY,
    QUESTION,
    catalog,
    governed_bundle,
)


def _run(name, params, session_factory):
    return run_operation(name, params, session_factory=session_factory)


def test_writeback_reads_workspace_scoped_live_feedback_without_exporting_it(monkeypatch):
    lookups = []

    class _FeedbackRepository:
        def __init__(self, _session_factory):
            pass

        def lookup(self, **kwargs):
            lookups.append(kwargs)
            if kwargs.get("review_task_id"):
                return [SimpleNamespace(id="feedback-1", outcome="reject")]
            return [SimpleNamespace(id="feedback-1", outcome="reject")]

        def blocking_ids(self, **kwargs):
            return ["feedback-1"]

    class _DecisionRepository:
        def __init__(self, _session_factory):
            pass

        def for_task(self, **kwargs):
            assert kwargs == {"workspace": "workspace-a", "review_task_id": "rt-1"}
            return [
                SimpleNamespace(decision="exclude", superseded_by=None),
                SimpleNamespace(decision="include", superseded_by="decision-2"),
            ]

    monkeypatch.setattr(operations, "PostgresAnswerFeedbackRepository", _FeedbackRepository)
    monkeypatch.setattr(operations, "PostgresCitationDecisionRepository", _DecisionRepository)
    task = SimpleNamespace(id="rt-1", proposal_payload={"correlation_id": "corr-1"})

    state, blocked = operations._review_feedback_state(
        task, session_factory=object(), workspace="workspace-a"
    )

    assert blocked is True
    assert state == {
        "answer_feedback": {"reject": 1},
        "citation_decisions": {"exclude": 1},
        "feedback_count": 1,
        "decision_count": 1,
        "blocking_feedback_ids": ["feedback-1"],
        "blocking_decision_ids": [],
    }
    assert {lookup["workspace"] for lookup in lookups} == {"workspace-a"}


def test_writeback_reads_correlation_only_negative_decisions(monkeypatch):
    class _FeedbackRepository:
        def __init__(self, _session_factory):
            pass

        def lookup(self, **kwargs):
            return []

        def blocking_ids(self, **kwargs):
            return []

    class _DecisionRepository:
        def __init__(self, _session_factory):
            pass

        def for_task(self, **kwargs):
            return []

        def for_correlation(self, **kwargs):
            assert kwargs == {"workspace": "workspace-a", "correlation_id": "corr-1"}
            return [SimpleNamespace(id="decision-1", decision="reject", superseded_by=None)]

    monkeypatch.setattr(operations, "PostgresAnswerFeedbackRepository", _FeedbackRepository)
    monkeypatch.setattr(operations, "PostgresCitationDecisionRepository", _DecisionRepository)
    task = SimpleNamespace(id="rt-1", proposal_payload={"correlation_id": "corr-1"})

    state, blocked = operations._review_feedback_state(
        task, session_factory=object(), workspace="workspace-a"
    )

    assert blocked is True
    assert state["blocking_decision_ids"] == ["decision-1"]


def test_resolve_returns_the_bundle_the_service_built(resolved, session_factory):
    result = _run(
        "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}, session_factory
    )

    assert result == governed_bundle().to_dict()
    assert resolved[0]["query"] == QUESTION
    assert resolved[0]["session_factory"] is session_factory


def test_the_catalog_is_served_as_the_service_built_it(listed, session_factory):
    result = _run("list_context_catalog", {}, session_factory)

    assert result == catalog().to_dict()
    assert listed[0]["session_factory"] is session_factory


def test_the_catalog_is_bounded_by_default(listed, session_factory):
    """A caller that asks for nothing gets a page, not the corpus: the cap is
    the contract, not a courtesy the caller opts into (hy-aq3)."""
    _run("list_context_catalog", {}, session_factory)

    assert listed[0]["limit"] == CATALOG_DEFAULT_LIMIT
    assert listed[0]["offset"] == 0


def test_the_page_the_caller_asked_for_reaches_the_catalog(listed, session_factory):
    _run("list_context_catalog", {"limit": 5, "offset": 10}, session_factory)

    assert listed[0]["limit"] == 5
    assert listed[0]["offset"] == 10


@pytest.mark.parametrize("limit", [0, -1, CATALOG_MAX_LIMIT + 1, True, "20", 1.5])
def test_a_limit_outside_the_cap_is_refused_not_quietly_clamped(limit, listed, session_factory):
    """Serving a page to a caller that asked for everything, without saying
    so, is the silent-cap failure the bound exists to avoid."""
    with pytest.raises(OperationError) as excinfo:
        _run("list_context_catalog", {"limit": limit}, session_factory)

    assert excinfo.value.code == "invalid_params"
    assert f"between 1 and {CATALOG_MAX_LIMIT}" in excinfo.value.recovery
    assert listed == []


def test_the_directive_reaches_the_resolver_intact(resolved, session_factory):
    _run(
        "resolve_analytics_context",
        {
            "query": QUESTION,
            "directive": {
                "domains": ["revenue"],
                "asset_refs": [PRIMARY],
                "concepts": ["recognized_revenue"],
                "max_hops": 2,
                "context_budget": 12000,
            },
        },
        session_factory,
    )

    directive = resolved[0]["directive"]
    assert directive.domains == ["revenue"]
    assert directive.asset_refs == [PRIMARY]
    assert directive.concepts == ["recognized_revenue"]
    assert directive.max_hops == 2
    assert directive.context_budget == 12000


def test_a_coverage_claim_with_no_domain_to_check_it_against_is_refused(resolved, session_factory):
    """`concepts` says what the named domain must declare (hy-9lct). With no
    domain there is nothing to check it against, and a parameter that is
    accepted, echoed back in `request.directive`, and never acted on reads to
    a caller as a claim that was honoured."""
    with pytest.raises(OperationError) as excinfo:
        _run(
            "resolve_analytics_context",
            {
                "query": QUESTION,
                "directive": {"asset_refs": [PRIMARY], "concepts": ["recognized_revenue"]},
            },
            session_factory,
        )

    assert excinfo.value.code == "invalid_params"
    assert "names no 'domains'" in excinfo.value.message
    assert "list_context_catalog" in excinfo.value.recovery
    assert resolved == []


@pytest.mark.parametrize(
    "directive",
    [
        {"domains": ["revenue"]},
        {"domains": ["revenue"], "concepts": []},
        {"domains": ["revenue"], "asset_refs": [PRIMARY], "concepts": []},
    ],
    ids=["concepts absent", "concepts empty", "with asset_refs"],
)
def test_a_domain_named_with_no_coverage_claim_is_refused_not_answered(
    directive, resolved, session_factory
):
    """The mirror image of the test above, and the same verdict (hy-bdff).

    A required parameter is absent, which is knowable from the request before
    any retrieval runs. Served as a bundle this reached the caller as
    `no_match` -- "no configured Git context covers this request" -- which is
    false here: a configured Git context covers `revenue`, and the caller was
    refused for not saying what it needed. The resolver is never asked.
    """
    with pytest.raises(OperationError) as excinfo:
        _run(
            "resolve_analytics_context",
            {"query": QUESTION, "directive": directive},
            session_factory,
        )

    assert excinfo.value.code == "invalid_params"
    assert "without saying what it must cover" in excinfo.value.message
    assert "list_context_catalog" in excinfo.value.message
    assert resolved == []


def test_a_question_without_a_directive_is_refused_rather_than_interpreted(
    resolved, session_factory
):
    """The deleted behaviour routed on the wording of the question. The
    refusal names the operation that replaces it."""
    with pytest.raises(OperationError) as excinfo:
        _run("resolve_analytics_context", {"query": QUESTION}, session_factory)

    assert excinfo.value.code == "directive_required"
    assert "list_context_catalog" in excinfo.value.recovery
    assert resolved == []


def test_a_directive_that_names_nothing_is_refused_too(resolved, session_factory):
    with pytest.raises(OperationError) as excinfo:
        _run(
            "resolve_analytics_context",
            {"query": QUESTION, "directive": {"max_hops": 2}},
            session_factory,
        )

    assert excinfo.value.code == "directive_required"
    assert "list_context_catalog" in excinfo.value.recovery
    assert resolved == []


def test_validation_re_resolves_the_question_and_checks_the_plan(resolved, session_factory):
    result = _run(
        "validate_analytics_plan",
        {
            "query": QUESTION,
            "directive": DIRECTIVE,
            "bundle_id": governed_bundle().bundle_id,
            "source_refs": [PRIMARY],
            "fields": ["recognized_revenue"],
            "grain": "order_date",
        },
        session_factory,
    )

    assert resolved[0]["query"] == QUESTION
    # This fixture's governed context declares no filters, joins, or checks, so a
    # plan that contradicts nothing is `valid_with_gaps` -- the served operation
    # discloses the sections it could not check rather than a false green (#285).
    assert result["status"] == "valid_with_gaps"
    assert [section["section"] for section in result["sections_not_checkable"]] == [
        "instructions.filters",
        "instructions.joins",
        "instructions.validations",
    ]
    assert result["violations"] == []
    assert result["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }
    # The response says which bundle the plan claimed and which one it was
    # judged against, so a reader can see the check happened.
    assert result["checked_against"]["planned_bundle_id"] == governed_bundle().bundle_id
    assert result["checked_against"]["bundle_id"] == governed_bundle().bundle_id


def test_a_plan_without_the_bundle_it_was_built_from_is_refused(resolved, session_factory):
    """Optional would make the staleness check opt-in, and an agent could
    skip it by accident: it resolved first, so it has the id."""
    with pytest.raises(OperationError) as excinfo:
        _run(
            "validate_analytics_plan",
            {"query": QUESTION, "directive": DIRECTIVE, "source_refs": [PRIMARY]},
            session_factory,
        )

    assert excinfo.value.code == "invalid_params"
    assert "resolve_analytics_context" in excinfo.value.recovery
    assert resolved == []  # refused before the question was re-asked


def test_a_plan_built_against_a_moved_answer_is_reported_not_validated(resolved, session_factory):
    """Bundles are unstored, so the claimed id is checked against a fresh
    resolution rather than trusted."""
    result = _run(
        "validate_analytics_plan",
        {
            "query": QUESTION,
            "directive": DIRECTIVE,
            "bundle_id": "cb-somethingelse",
            "source_refs": [PRIMARY],
        },
        session_factory,
    )

    assert result["status"] == "unverifiable"
    assert [violation["code"] for violation in result["violations"]] == ["stale_bundle"]


def test_an_unknown_operation_names_the_ones_that_exist(session_factory):
    with pytest.raises(OperationError) as excinfo:
        _run("execute_sql", {}, session_factory)

    assert excinfo.value.code == "unknown_operation"
    assert "resolve_analytics_context" in excinfo.value.recovery


@pytest.mark.parametrize("name", OPERATIONS)
def test_a_misspelled_parameter_is_refused_rather_than_ignored(name, session_factory):
    """Silently dropping a typo answers a different question than the agent
    asked, and looks like a correct answer."""
    with pytest.raises(OperationError) as excinfo:
        _run(name, {"query": QUESTION, "quesion": "typo"}, session_factory)

    assert excinfo.value.code == "unknown_parameter"
    assert "quesion" in excinfo.value.message
    assert excinfo.value.recovery


def test_a_misspelled_directive_key_is_refused_too(resolved, session_factory):
    with pytest.raises(OperationError) as excinfo:
        _run(
            "resolve_analytics_context",
            {"query": QUESTION, "directive": {"domain": "revenue"}},
            session_factory,
        )

    assert excinfo.value.code == "unknown_parameter"
    assert "domain" in excinfo.value.message
    assert "domains" in excinfo.value.recovery
    assert resolved == []


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({}, "invalid_params"),
        ({"query": "   ", "directive": DIRECTIVE}, "invalid_params"),
        ({"query": 7, "directive": DIRECTIVE}, "invalid_params"),
        ({"query": QUESTION, "directive": "revenue"}, "invalid_params"),
        ({"query": QUESTION, "directive": {"asset_refs": "one-ref"}}, "invalid_params"),
        ({"query": QUESTION, "directive": {"asset_refs": [7]}}, "invalid_params"),
        ({"query": QUESTION, "directive": {"domains": [7]}}, "invalid_params"),
        # A bound is a count, and `true` is an int in Python.
        ({"query": QUESTION, "directive": {**DIRECTIVE, "max_hops": -1}}, "invalid_params"),
        ({"query": QUESTION, "directive": {**DIRECTIVE, "max_hops": True}}, "invalid_params"),
        ({"query": QUESTION, "directive": {**DIRECTIVE, "context_budget": 0}}, "invalid_params"),
        (
            {"query": QUESTION, "directive": {**DIRECTIVE, "context_budget": "12kb"}},
            "invalid_params",
        ),
    ],
)
def test_bad_parameters_explain_what_to_send(params, code, session_factory):
    with pytest.raises(OperationError) as excinfo:
        _run("resolve_analytics_context", params, session_factory)

    assert excinfo.value.code == code
    assert excinfo.value.recovery
    assert excinfo.value.to_dict()["error"]["recovery"] == excinfo.value.recovery


def test_plan_entries_may_be_names_or_the_bundle_s_own_instructions(resolved, session_factory):
    result = _run(
        "validate_analytics_plan",
        {
            "query": QUESTION,
            "directive": DIRECTIVE,
            "bundle_id": governed_bundle().bundle_id,
            "source_refs": [PRIMARY],
            "grain": "order_date",
            "fields": [
                {"name": "recognized_revenue", "source_ref": PRIMARY, "expression": "SUM(x)"}
            ],
        },
        session_factory,
    )

    # The echoed expression contradicts the governed one, and is caught on
    # the attribute the caller echoed.
    assert [violation["code"] for violation in result["violations"]] == [
        "field_expression_mismatch"
    ]


def test_a_plan_entry_that_is_neither_is_refused(session_factory):
    with pytest.raises(OperationError) as excinfo:
        _run(
            "validate_analytics_plan",
            {"query": QUESTION, "directive": DIRECTIVE, "bundle_id": "cb-whatever", "fields": [7]},
            session_factory,
        )

    assert excinfo.value.code == "invalid_params"


@pytest.mark.parametrize("name", OPERATIONS)
def test_every_operation_ships_a_checked_in_example_its_own_schema_accepts(name):
    spec = OPERATION_SPECS[name]
    schema = spec["input_schema"]

    assert schema["additionalProperties"] is False
    assert set(schema.get("required", ())) <= set(spec["example"])
    assert set(spec["example"]) <= set(schema["properties"])


def test_an_unexpected_failure_still_leaves_here_as_an_answerable_error(
    broken_resolver, session_factory, capsys
):
    """A transport handed a raw exception has nothing to send back, and an
    agent whose call ends in silence cannot recover from it."""
    with pytest.raises(OperationError) as excinfo:
        _run(
            "resolve_analytics_context",
            {"query": QUESTION, "directive": DIRECTIVE},
            session_factory,
        )

    assert excinfo.value.code == "internal_error"
    assert "retry" in excinfo.value.recovery
    # The failure class, never the driver's message: it names the database
    # host and user.
    assert "ResolverExploded" in excinfo.value.message
    assert "no route to host" not in excinfo.value.message
    assert "no route to host" in capsys.readouterr().err


def test_validate_does_not_advertise_the_resolve_time_meaning_of_the_directive():
    """hy-t3am defect (a): the schema said "choose" where only "copy" is correct.

    VALIDATE used to splat `_QUERY_SCHEMA`, so it served RESOLVE's per-field
    advice -- notably a paragraph on how to CHOOSE `asset_refs` ("With a
    domain, these narrow its evidence") -- on an operation whose only correct
    directive is the one already resolved. The tool description's one sentence
    ("send the same 'query' and 'directive' you resolved with") sat under a
    schema spending a paragraph inviting a different one, and a planner fills
    a call in per field.

    The measured consequence: the governed arm added the refs its plan reads to
    the directive, re-resolved to a different bundle, and a CORRECT plan came
    back `stale_bundle`. `planner.md` already carried the same instruction in
    prose and the model did it anyway, so prose was not the lever.

    Asserting on meaning rather than on wording: the two operations must not
    hand the caller the same directive text, and validate's must name the field
    the refs actually belong in.
    """
    resolve_schema = OPERATION_SPECS["resolve_analytics_context"]["input_schema"]["properties"]
    validate_schema = OPERATION_SPECS["validate_analytics_plan"]["input_schema"]["properties"]

    resolve_directive = resolve_schema["directive"]
    validate_directive = validate_schema["directive"]

    assert validate_directive["description"] != resolve_directive["description"], (
        "validate serves resolve's directive description verbatim, which tells the caller "
        "how to CHOOSE a directive on the one operation where it must be copied. This is "
        "what re-splatting _QUERY_SCHEMA into VALIDATE looks like. See hy-t3am."
    )
    assert "source_refs" in validate_directive["description"], (
        "validate's directive description does not name 'source_refs', so a caller holding "
        "refs its plan reads is told where they do not go and never where they do."
    )

    resolve_refs = resolve_directive["properties"]["asset_refs"]["description"]
    validate_refs = validate_directive["properties"]["asset_refs"]["description"]
    assert validate_refs != resolve_refs, (
        "validate's 'asset_refs' still carries the resolve-time paragraph on narrowing a "
        "domain's evidence, which is the exact text the measured arm acted on. See hy-t3am."
    )

    # The shape must stay in step with resolve even as the wording diverges: a
    # directive field that exists at resolve time and not at validate time
    # would be a second contract, and the server accepts one.
    assert set(validate_directive["properties"]) == set(resolve_directive["properties"])


# --- hy-gh-281 items 5 and 6: served-contract gaps on the review-op schemas ---


def test_list_review_tasks_refuses_an_unknown_status_loudly(session_factory):
    """Item 5: a bad status must be refused with the accepted values, never
    answered with an empty list -- a typo must not read as 'no open tasks'. The
    refusal is before the repository, so the stub session_factory is untouched."""
    from hyperset.repositories.postgres import REVIEW_TASK_STATUSES

    with pytest.raises(OperationError) as excinfo:
        _run("list_review_tasks", {"status": "banana"}, session_factory)

    assert excinfo.value.code == "invalid_params"
    assert "banana" in excinfo.value.message
    for status in REVIEW_TASK_STATUSES:
        assert status in excinfo.value.recovery


def test_list_review_tasks_status_schema_enumerates_the_accepted_values(session_factory):
    """Item 5: the schema advertises the enum, so a caller learns the accepted
    statuses without a failed call."""
    from hyperset.repositories.postgres import REVIEW_TASK_STATUSES

    status_schema = OPERATION_SPECS["list_review_tasks"]["input_schema"]["properties"]["status"]
    assert status_schema["enum"] == list(REVIEW_TASK_STATUSES)
    assert status_schema["description"].strip()


def test_search_knowledge_schema_advertises_both_modes():
    mode = OPERATION_SPECS["search_knowledge"]["input_schema"]["properties"]["mode"]

    assert mode["enum"] == ["grep", "semantic"]
    assert mode["description"].strip()


def test_the_edit_draft_schema_documents_every_draft_field(session_factory):
    """Item 6: the edit_review_draft `definition` schema is generated from the
    same constant that enforces it, so a caller can build a valid draft from the
    schema alone -- and it cannot drift from `DRAFT_DEFINITION_FIELDS`."""
    from hyperset.context.schema import DRAFT_DEFINITION_FIELDS

    definition = OPERATION_SPECS["edit_review_draft"]["input_schema"]["properties"]["definition"]
    assert tuple(definition["properties"]) == tuple(DRAFT_DEFINITION_FIELDS)
    assert definition["additionalProperties"] is False
    # Real per-field shapes, not bare descriptions (panel MINOR): every field has
    # a type, list fields carry `items`, and object items reject unknown sub-keys.
    for field, prop in definition["properties"].items():
        assert prop["description"].strip(), field
        assert prop["type"] in ("array", "string"), field
        if prop["type"] == "array":
            assert "items" in prop, field
            if prop["items"].get("type") == "object":
                assert prop["items"]["additionalProperties"] is False, field
    # The one hard structural rule the schema can carry: at least one definition.
    assert definition["required"] == ["definitions"]
    assert definition["properties"]["definitions"]["minItems"] == 1
    assert definition["properties"]["grain"]["type"] == "string"


def test_a_schema_conforming_draft_is_actually_accepted_by_the_validator(session_factory):
    """Item 6's promise made real (panel MINOR): a draft built to the served
    shape is accepted by `validate_definition_draft` -- schema-valid is
    structurally acceptable, the remaining rejections being relational (a field's
    source must be approved), which the top-level description states."""
    from hyperset.context.schema import validate_definition_draft

    source = "table:postgres:analytics.public.orders"
    draft = {
        "definitions": [{"term": "recognized_revenue", "statement": "net of tax"}],
        "approved_sources": [{"ref": source, "role": "primary"}],
        "fields": [{"name": "recognized_revenue", "source_ref": source, "expression": "SUM(net)"}],
        "filters": ["status = 'completed'"],
        "grain": "order_date",
    }

    normalized = validate_definition_draft(draft, domain="revenue")
    assert normalized["definitions"][0]["term"] == "recognized_revenue"


def test_the_review_contract_gaps_do_not_move_the_tools_hash():
    """Items 5-7 touch review-op schemas + propose behaviour, none of which is in
    RESOLVE_PATH_OPERATIONS, so the resolve-path planner tools hash a committed
    benchmark recording is pinned to is unchanged by THEM (hy-gh-281). The pinned
    value is fe930a003b731211 since item 3 added VALIDATE's input-schema field
    descriptions -- a change that is in RESOLVE_PATH_OPERATIONS and did move it;
    these items are not among the movers."""
    from hyperset.planner.loop import tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"


def test_the_principal_identity_is_an_opaque_subject_at_issuer_or_anonymous():
    """The ONE server-side identity computation (hy-mg8p proposer, hy-s8a6 assignee): the
    VERIFIED caller as an opaque `subject@issuer` -- never a raw email or profile claim --
    and `anonymous` when there is no principal (the authz gate off / loopback). Both the
    PR-trail proposer and the self-claimed assignee are computed from THIS, never accepted
    as caller free text, which is what keeps them PII-safe by construction."""
    from hyperset.security.authz import Principal
    from hyperset.transport.operations import _principal_identity

    principal = Principal(
        subject="auth0|abc123", issuer="https://issuer.example", roles=("reviewer",)
    )
    assert _principal_identity(principal) == "auth0|abc123@https://issuer.example"
    assert _principal_identity(None) == "anonymous"


def _set_allowlist(tmp_path, monkeypatch, *identities):
    from hyperset.security.authz import ENABLED_ENV
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV

    path = tmp_path / "reviewers.allow"
    path.write_text("\n".join(identities) + "\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    # An allowlist is only consulted behind the authz gate, so enabling it is part of
    # configuring the policy for a gated feature (hy-38mk8 r2). Harmless where the subject
    # under test does not read authz_enabled().
    monkeypatch.setenv(ENABLED_ENV, "1")


def test_validated_known_assignee_accepts_only_a_listed_identity(tmp_path, monkeypatch):
    """hy-ip8do: assigning ANOTHER user is validated against the KNOWN-principals registry
    (the approved-reviewer allowlist), never accepted as free text. A listed identity is
    returned; an unlisted one, a non-string, and (crucially) any input when the allowlist is
    NOT configured are all refused -- so a caller can never name an arbitrary or PII-shaped
    subject."""
    # Not configured => cannot resolve a known principal => refuse (fail closed).
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import _validated_known_assignee

    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    with pytest.raises(OperationError) as no_registry:
        _validated_known_assignee("auth0|abc123@https://issuer.example")
    assert no_registry.value.code == "invalid_params"

    _set_allowlist(tmp_path, monkeypatch, "auth0|abc123@https://issuer.example")
    assert (
        _validated_known_assignee("  auth0|abc123@https://issuer.example  ")
        == "auth0|abc123@https://issuer.example"
    )
    for bad in ("mallory@https://issuer.example", "user:secret@https://issuer.example", 123, None):
        with pytest.raises(OperationError) as rejected:
            _validated_known_assignee(bad)
        assert rejected.value.code == "invalid_params", bad
        # The denial never echoes the attempted value (no roster/PII leak).
        assert "secret" not in rejected.value.message


def test_set_review_assignee_rejects_an_assignee_when_unassigning():
    # Unassign takes no assignee -- naming someone to clear is a contradiction, refused
    # before any task is loaded.
    from hyperset.transport.operations import SET_REVIEW_ASSIGNEE, _set_review_assignee

    assert SET_REVIEW_ASSIGNEE  # the op exists
    with pytest.raises(OperationError) as caught:
        _set_review_assignee(
            {"task_id": "rt-x", "assigned": False, "assignee": "someone@https://iss"},
            session_factory=None,
        )
    assert caught.value.code == "invalid_params"


# --- hy-38mk8 (S3): assist-class default-owner SUGGESTION for review tasks ---


def _review_record(task_id, *, domain=None, assignee=None, created_at=None, payload=None):
    """A ReviewTaskRecord with the fields `_suggested_owner`/`_review_task_view` read;
    everything else is a harmless default."""
    from datetime import datetime

    from hyperset.repositories import ReviewTaskRecord

    if payload is None:
        payload = {"domain": domain} if domain else {}
    when = created_at or datetime(2026, 1, 1, tzinfo=UTC)
    return ReviewTaskRecord(
        id=task_id,
        reason="a gap",
        priority=2,
        affected_asset_ids=[],
        affected_context_id=None,
        proposal_payload=payload,
        processor_evidence={},
        evaluation_impact=None,
        assignee=assignee,
        status="open",
        idempotency_key=task_id,
        row_version=1,
        created_at=when,
        updated_at=when,
    )


ALICE = "auth0|alice@https://issuer.example"
BOB = "auth0|bob@https://issuer.example"


def test_suggested_owner_is_the_most_recent_prior_in_domain_reviewer(tmp_path, monkeypatch):
    """The hint for an unassigned task is the owner of the most-recently-created OTHER
    task in the SAME domain that already carries an APPROVED assignee -- prior in-domain
    reviews, the assist signal (hy-38mk8)."""
    from datetime import datetime

    from hyperset.transport.operations import _suggested_owner

    _set_allowlist(tmp_path, monkeypatch, ALICE, BOB)
    older = _review_record(
        "rt-old", domain="revenue", assignee=ALICE, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    newer = _review_record(
        "rt-new", domain="revenue", assignee=BOB, created_at=datetime(2026, 2, 1, tzinfo=UTC)
    )
    target = _review_record("rt-open", domain="revenue")

    # The more recent owner (bob) wins over the older one (alice).
    assert _suggested_owner(target, [older, newer, target]) == BOB


def test_suggested_owner_only_suggests_a_known_approved_reviewer(tmp_path, monkeypatch):
    """hy-38mk8 r2: the candidate must be an APPROVED reviewer, not merely a truthy
    assignee. The shared `anonymous` id (authz off) and a legacy PII/credential-shaped row
    are NEVER suggested even though they are the most recent same-domain owners -- only a
    member of the allowlist is. Redaction at the view is not enough; the identity itself
    must be a known principal."""
    from datetime import datetime

    from hyperset.transport.operations import _suggested_owner

    _set_allowlist(tmp_path, monkeypatch, ALICE)  # bob and the junk rows are NOT approved
    anon = _review_record(
        "rt-anon",
        domain="revenue",
        assignee="anonymous",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    legacy_pii = _review_record(
        "rt-pii",
        domain="revenue",
        assignee="alice@example.com",
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    approved = _review_record(
        "rt-ok", domain="revenue", assignee=ALICE, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    target = _review_record("rt-open", domain="revenue")

    # The more RECENT owners (anonymous, then the PII row) are skipped; the older APPROVED
    # reviewer is the only eligible candidate.
    assert _suggested_owner(target, [anon, legacy_pii, approved, target]) == ALICE


def test_suggested_owner_is_none_without_a_configured_allowlist(tmp_path, monkeypatch):
    """No policy, no suggestion (hy-38mk8 r2): with the allowlist UNSET (role-only) there is
    no registry to trust, so even a well-formed prior owner yields NO hint. An empty/
    misconfigured policy (blank env => fail-closed frozenset()) likewise suggests nobody.
    Authz is ON here, so the None is attributable to the missing allowlist, not the gate."""
    from hyperset.security.authz import ENABLED_ENV
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import _suggested_owner

    monkeypatch.setenv(ENABLED_ENV, "1")
    prior = _review_record("rt-prior", domain="revenue", assignee=ALICE)
    target = _review_record("rt-open", domain="revenue")

    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)  # unset => None => role-only
    assert _suggested_owner(target, [prior, target]) is None

    monkeypatch.setenv(ALLOWLIST_ENV, "   ")  # present-but-blank => fail-closed empty
    assert _suggested_owner(target, [prior, target]) is None


def test_suggested_owner_is_none_when_the_authz_gate_is_off(tmp_path, monkeypatch):
    """DEFAULT-OFF (hy-38mk8 r2): with the authz gate DISABLED there are no verified
    identities to trust, so NO suggestion is served -- even when a valid allowlist file
    naming the prior owner is configured. The suggestion feature is gated on
    `authz_enabled()` first, keeping the surface byte-identical on the default/loopback
    path (the allowlist module's own callers-must-gate contract)."""
    from hyperset.security.authz import ENABLED_ENV
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import _suggested_owner

    path = tmp_path / "reviewers.allow"
    path.write_text(ALICE + "\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))  # a valid allowlist naming the prior owner
    monkeypatch.delenv(ENABLED_ENV, raising=False)  # ...but the authz gate is OFF

    prior = _review_record("rt-prior", domain="revenue", assignee=ALICE)
    target = _review_record("rt-open", domain="revenue")
    assert _suggested_owner(target, [prior, target]) is None

    # Flip the gate on and the same inputs now DO yield the suggestion -- proving the gate
    # is the reason, not a broken allowlist.
    monkeypatch.setenv(ENABLED_ENV, "1")
    assert _suggested_owner(target, [prior, target]) == ALICE


def test_suggested_owner_is_none_when_the_task_already_has_an_owner(tmp_path, monkeypatch):
    """An owned task needs no suggestion -- the hint is only for a gap without an owner. The
    prior owner is approved, so the ONLY reason for None is the owned-guard."""
    from hyperset.transport.operations import _suggested_owner

    _set_allowlist(tmp_path, monkeypatch, ALICE)
    owned = _review_record("rt-owned", domain="revenue", assignee="auth0|me@https://issuer.example")
    prior = _review_record("rt-prior", domain="revenue", assignee=ALICE)
    assert _suggested_owner(owned, [owned, prior]) is None


def test_suggested_owner_is_none_without_a_prior_in_domain_owner(tmp_path, monkeypatch):
    """No same-domain owned task, no hint. A same-domain UNassigned task is not a signal,
    and an APPROVED owner in a DIFFERENT domain does not leak across domains."""
    from hyperset.transport.operations import _suggested_owner

    _set_allowlist(tmp_path, monkeypatch, ALICE)  # the other-domain owner IS approved
    target = _review_record("rt-open", domain="revenue")
    same_domain_unassigned = _review_record("rt-sib", domain="revenue")
    other_domain_owned = _review_record("rt-other", domain="marketing", assignee=ALICE)
    no_domain = _review_record("rt-nd", domain=None)

    assert _suggested_owner(target, [target, same_domain_unassigned, other_domain_owned]) is None
    # A task with no domain of its own gets no suggestion at all.
    assert _suggested_owner(no_domain, [no_domain, other_domain_owned]) is None


def test_suggested_owner_breaks_a_created_at_tie_deterministically_by_id(tmp_path, monkeypatch):
    """Two approved same-domain owners created at the same instant: the tie breaks by id,
    so the hint is deterministic rather than dependent on row order."""
    from datetime import datetime

    from hyperset.transport.operations import _suggested_owner

    a_id, b_id = "auth0|a@https://iss", "auth0|b@https://iss"
    _set_allowlist(tmp_path, monkeypatch, a_id, b_id)
    when = datetime(2026, 3, 1, tzinfo=UTC)
    lo = _review_record("rt-a", domain="revenue", assignee=a_id, created_at=when)
    hi = _review_record("rt-b", domain="revenue", assignee=b_id, created_at=when)
    target = _review_record("rt-open", domain="revenue")

    # Higher id wins the tie, regardless of the order the rows arrive in.
    assert _suggested_owner(target, [lo, hi, target]) == b_id
    assert _suggested_owner(target, [hi, lo, target]) == b_id


def test_review_task_view_carries_the_suggestion_and_rationale_only_when_present():
    """The view adds `suggested_assignee` + `suggested_assignee_rationale` ONLY when a hint
    is passed (byte-identical otherwise), redacts the id at the boundary like `assignee`,
    and the rationale names the deterministic signal, assist-labeled (hy-38mk8 r2)."""
    from hyperset.transport.operations import (
        SUGGESTION_SIGNAL,
        SUGGESTION_SUMMARY,
        _review_task_view,
    )

    task = _review_record("rt-open", domain="revenue")

    # No hint => neither key, so an un-suggested task is exactly what it was before.
    view_none = _review_task_view(task)
    assert "suggested_assignee" not in view_none
    assert "suggested_assignee_rationale" not in view_none

    # A hint => id present and redacted (userinfo stripped, host kept) + a served rationale.
    view = _review_task_view(task, suggested_assignee="https://user:supersecret@host/x")
    assert view["suggested_assignee"] == "https://host/x"
    assert "supersecret" not in repr(view)
    assert view["suggested_assignee_rationale"] == {
        "signal": SUGGESTION_SIGNAL,
        "summary": SUGGESTION_SUMMARY,
        "assist": True,
    }


def test_review_task_view_redacts_nested_legacy_url_credentials():
    from hyperset.transport.operations import _review_task_view

    task = _review_record(
        "rt-legacy",
        payload={
            "domain": "revenue",
            "definition": {
                "source_ref": "https://alice:ghp_REVIEWSECRET@example.com/context",
                "notes": ["see https://bob:token@example.net/notes"],
            },
        },
    )

    view = _review_task_view(task)

    rendered = repr(view)
    assert "ghp_REVIEWSECRET" not in rendered
    assert "bob:token@" not in rendered
    assert view["proposal_payload"]["definition"]["source_ref"] == ("https://example.com/context")
