"""HTTP/MCP parity over the real slice (hy-oih, hy-x7f), hy-gh-36's required
check.

Both transports answer the same canonical revenue question against the same
pinned Git commit and the same persisted Superset evidence, and the two
answers are compared as bytes, not as fields: a difference in key order is a
difference in the contract, because a client may hash or diff what it was
served. Every operation is covered, catalog included: a tool that only one
transport serves correctly is a tool an agent cannot rely on.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from hyperset.bundle import ContextDirective, list_context_catalog, resolve_analytics_context
from hyperset.candidates import service as candidate_service
from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.transport.http import ROUTES, build_server
from hyperset.transport.operations import OPERATIONS, serialize
from tests.postgres.conftest import APPROVED_DATASET

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
DIMENSION_REF = "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8"
# What a planner sends after reading the catalog.
DIRECTIVE = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}

PLAN = {
    "source_refs": [APPROVED_REF, DIMENSION_REF],
    "fields": ["recognized_revenue", "region"],
    "joins": [
        {
            "from": "finance_orders_daily.customer_id",
            "to": "customer_dim.customer_id",
            "type": "inner",
        }
    ],
    "filters": ["finance_orders_daily.status = 'completed'", "customer_dim.is_test = false"],
    "grain": "order_date by customer_dim.region",
    "checks": [
        "recognized_revenue is non-negative",
        "monthly totals reconcile within 1% of the fixture close value",
    ],
}


@pytest.fixture
def http_client(session_factory):
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(operation: str, params: dict, *, expect_error: bool = False) -> str:
        path = next(route for route, name in ROUTES.items() if name == operation)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            if not expect_error:
                raise
            return error.read().decode()

    try:
        yield post
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _over_mcp(operation: str, params: dict, session_factory, *, expect_error: bool = False) -> str:
    """Call one operation through a real in-memory MCP client on the SDK server.

    The MCP half of the parity comparison: what a tool-using model receives is
    the `content` text of a real `tools/call`, and it must be the same bytes the
    HTTP body carries."""
    import anyio

    from hyperset.transport.mcp import build_mcp_server

    async def _run():
        from mcp.shared.memory import create_connected_server_and_client_session as connect

        server = build_mcp_server(session_factory=session_factory)
        async with connect(server) as client:
            return await client.call_tool(operation, params)

    result = anyio.run(_run)
    assert result.isError is expect_error
    return result.content[0].text


def _without_the_clock(served: str) -> str:
    """`resolved_at` (and the catalog's `generated_at`) is when the answer was
    served, and is deliberately not part of its identity. Everything else,
    key order included, has to match."""
    payload = json.loads(served)
    for key in ("resolved_at", "generated_at"):
        if key in payload:
            payload[key] = "<served>"
    return serialize(payload)


# The deterministic READ operations: a request answers with the same bytes on
# both transports, so parity is a byte comparison (hy-jis1 adds list_review_tasks
# here -- with no tasks seeded it is a deterministic empty list).
_READ_PARITY = {
    "list_context_catalog": {},
    "discover_analytics_context": {"query": QUESTION},
    "resolve_analytics_context": {"query": QUESTION, "directive": DIRECTIVE},
    "validate_analytics_plan": {
        "query": QUESTION,
        "directive": DIRECTIVE,
        "bundle_id": "cb-0000000000000000",
        **PLAN,
    },
    "expand_analytics_context": {
        "query": QUESTION,
        "domain": "revenue",
        "concepts": ["recognized_revenue"],
    },
    # A deterministic lexical grep over the same configured source on both transports:
    # same content, same order, same version/staleness metadata -> byte-identical (hy-r0szz).
    "search_knowledge": {"query": "revenue"},
    "lookup_answer_feedback": {"correlation_id": "corr-no-feedback"},
    "list_review_tasks": {},
}

# The review WRITE / MODEL / SIDE-EFFECT operations (hy-jis1): a model draft and
# a fresh Git commit are not byte-identical across two live runs, so their
# cross-transport contract is proven by ERROR parity on a shared invalid request
# -- the SAME run_operation, the SAME validation, the SAME serialized error on
# both transports -- plus the dedicated success tests below. A nonexistent
# task_id (and, for propose, an unconfigured target) is a deterministic refusal
# that triggers no model call and no clone.
_ERROR_PARITY = {
    "record_answer_feedback": {"outcome": "ignore", "source_ref": "src:docs/a.md"},
    "get_review_task": {"task_id": "rt-does-not-exist"},
    "edit_review_draft": {"task_id": "rt-does-not-exist", "definition": {}},
    "refine_review_draft": {"task_id": "rt-does-not-exist", "feedback": "tighten it"},
    "propose_review_to_git": {"task_id": "rt-does-not-exist"},
    "set_review_assignee": {"task_id": "rt-does-not-exist", "assigned": True},
}


@pytest.mark.postgres
def test_every_operation_is_served_by_both_transports(
    session_factory, revenue_slice, http_client, monkeypatch
):
    """Parity is per operation, not per suite: every served operation is compared
    the same way. The deterministic reads are byte-identical; the review write
    ops -- whose live output is a model draft or a fresh commit -- are proven by
    identical serialized errors on a shared invalid request, with their success
    paths covered by the dedicated tests below. Both halves account for every op
    in OPERATIONS, so a new served op cannot be added without landing in one."""
    # Explicit test double injection; the served factory refuses deterministic
    # configuration so production discovery cannot silently avoid OpenAI.
    monkeypatch.setattr(
        candidate_service,
        "configured_embedding_provider",
        lambda: DeterministicEmbeddingProvider(),
    )
    assert set(OPERATIONS) == set(_READ_PARITY) | set(_ERROR_PARITY)

    for operation, params in _READ_PARITY.items():
        assert _without_the_clock(http_client(operation, params)) == _without_the_clock(
            _over_mcp(operation, params, session_factory)
        ), operation

    for operation, params in _ERROR_PARITY.items():
        over_http = http_client(operation, params, expect_error=True)
        over_mcp = _over_mcp(operation, params, session_factory, expect_error=True)
        assert over_http == over_mcp, operation
        assert json.loads(over_http)["error"]["code"] in {"invalid_request", "invalid_params"}


@pytest.mark.postgres
def test_the_catalog_lists_the_same_domains_over_both_transports(
    session_factory, revenue_slice, http_client
):
    over_http = json.loads(http_client("list_context_catalog", {}))
    over_mcp = json.loads(_over_mcp("list_context_catalog", {}, session_factory))

    assert [entry["domain"] for entry in over_http["domains"]] == ["revenue"]
    assert over_http["domains"] == over_mcp["domains"]
    assert over_http["observed"] == over_mcp["observed"]


@pytest.mark.postgres
def test_a_truncated_catalog_is_disclosed_identically_by_both_transports(
    session_factory, wide_context, http_client
):
    """A partial catalog that reads as complete on one transport is worse
    than no cap at all (hy-aq3). Served over a corpus that exceeds both
    bounds, so the disclosure being compared is a real one."""
    params = {"limit": 1}

    over_http = http_client("list_context_catalog", params)
    over_mcp = _over_mcp("list_context_catalog", params, session_factory)

    assert _without_the_clock(over_http) == _without_the_clock(over_mcp)
    payload = json.loads(over_http)
    page = payload["page"]
    (first,) = payload["domains"]
    assert {"list": f"{first['domain']}.evidence_refs", "reason": "withheld"} in page["truncated"]
    assert {"list": f"{first['domain']}.owner_refs", "reason": "cut"} in page["truncated"]
    assert page["limit"] == 1
    assert page["next_offset"] == 1
    assert page["recovery"]
    # And neither transport reshaped what the shared service produced. This is
    # what carries the withheld `evidence_refs` key to the wire: an absent key
    # is a difference no field-by-field assertion would have to be written for,
    # and the same holds for every field added later.
    assert _without_the_clock(over_http) == _without_the_clock(
        serialize(
            list_context_catalog(session_factory=session_factory, limit=params["limit"]).to_dict()
        )
    )


@pytest.mark.postgres
def test_a_catalog_limit_past_the_cap_is_refused_by_both_transports(
    session_factory, revenue_slice, http_client
):
    params = {"limit": 100_000}

    over_http = json.loads(http_client("list_context_catalog", params, expect_error=True))
    over_mcp = json.loads(
        _over_mcp("list_context_catalog", params, session_factory, expect_error=True)
    )

    assert over_http == over_mcp
    assert over_http["error"]["code"] == "invalid_params"
    assert "between 1 and 500" in over_http["error"]["recovery"]


@pytest.mark.postgres
def test_a_question_without_a_directive_is_refused_by_both_transports(
    session_factory, revenue_slice, http_client
):
    """Neither transport may fall back to reading the question: the refusal
    and its recovery are the same on both (GitHub #70)."""
    params = {"query": QUESTION}

    over_http = json.loads(http_client("resolve_analytics_context", params, expect_error=True))
    over_mcp = json.loads(
        _over_mcp("resolve_analytics_context", params, session_factory, expect_error=True)
    )

    assert over_http == over_mcp
    assert over_http["error"]["code"] == "directive_required"
    assert "list_context_catalog" in over_http["error"]["recovery"]


@pytest.mark.postgres
def test_both_transports_serve_the_same_bundle_bytes(session_factory, revenue_slice, http_client):
    params = {"query": QUESTION, "directive": DIRECTIVE}

    over_http = http_client("resolve_analytics_context", params)
    over_mcp = _over_mcp("resolve_analytics_context", params, session_factory)

    assert _without_the_clock(over_http) == _without_the_clock(over_mcp)
    # And neither transport reshaped what the shared resolver produced.
    assert _without_the_clock(over_http) == _without_the_clock(
        serialize(
            resolve_analytics_context(
                query=QUESTION,
                directive=ContextDirective(domains=["revenue"], concepts=["recognized_revenue"]),
                session_factory=session_factory,
            ).to_dict()
        )
    )
    assert json.loads(over_http)["bundle_id"] == json.loads(over_mcp)["bundle_id"]


@pytest.mark.postgres
def test_the_bundle_served_is_the_governed_one_from_the_pinned_commit(
    session_factory, revenue_slice, http_client
):
    bundle = json.loads(
        http_client("resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE})
    )

    assert bundle["resolution"]["status"] == "governed"
    assert bundle["context_authority"]["commit_sha"] == revenue_slice["context"].commit_sha
    assert bundle["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


@pytest.mark.postgres
def test_both_transports_validate_the_same_plan_identically(
    session_factory, revenue_slice, http_client
):
    bundle_id = json.loads(
        http_client("resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE})
    )["bundle_id"]
    params = {"query": QUESTION, "directive": DIRECTIVE, "bundle_id": bundle_id, **PLAN}

    over_http = http_client("validate_analytics_plan", params)
    over_mcp = _over_mcp("validate_analytics_plan", params, session_factory)

    assert over_http == over_mcp
    result = json.loads(over_http)
    assert result["status"] == "valid"
    # The bundle the plan claimed and the bundle it was judged against, both
    # in the response: the staleness check is visible, not assumed.
    assert result["checked_against"]["planned_bundle_id"] == bundle_id
    assert result["checked_against"]["bundle_id"] == bundle_id
    assert result["checked_against"]["commit_sha"] == revenue_slice["context"].commit_sha


@pytest.mark.postgres
def test_the_same_request_twice_is_the_same_answer_twice(
    session_factory, revenue_slice, http_client
):
    """Determinism is the product requirement, not a test convenience: an
    unchanged commit and unchanged sources answer identically."""
    params = {"query": QUESTION, "directive": DIRECTIVE}
    first = http_client("resolve_analytics_context", params)
    second = http_client("resolve_analytics_context", params)

    assert json.loads(first)["bundle_id"] == json.loads(second)["bundle_id"]


@pytest.mark.postgres
def test_a_plan_built_against_a_moved_bundle_is_refused_over_both_transports(
    session_factory, revenue_slice, http_client
):
    params = {
        "query": QUESTION,
        "directive": DIRECTIVE,
        "bundle_id": "cb-0000000000000000",
        **PLAN,
    }

    over_http = json.loads(http_client("validate_analytics_plan", params))
    over_mcp = json.loads(_over_mcp("validate_analytics_plan", params, session_factory))

    assert over_http == over_mcp
    assert over_http["status"] == "unverifiable"
    assert [violation["code"] for violation in over_http["violations"]] == ["stale_bundle"]


# --- The review ops over MCP: success + boundary (hy-jis1) ---

from hyperset.flywheel.authoring import PROPOSE_CONTEXT_DEFINITION  # noqa: E402
from hyperset.planner.runtime import ScriptedRuntime, ToolCall  # noqa: E402
from hyperset.repositories.postgres import (  # noqa: E402
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport import operations as operations_module  # noqa: E402
from tests.integration.test_git_context_source import (  # noqa: E402
    CONTEXT_PATH,
    git,
    make_repository,
)
from tests.postgres.test_interactive_review import DRAFT_PAYLOAD  # noqa: E402


def _governed_counts(session_factory) -> dict[str, int]:
    from sqlalchemy import text

    with session_factory() as session:
        return {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("governed_context", "governed_context_versions", "review_decisions")
        }


@pytest.mark.postgres
def test_get_review_task_is_byte_identical_over_both_transports(session_factory, http_client):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="jis1:get", proposal_payload=DRAFT_PAYLOAD
    )
    params = {"task_id": task.id}

    over_http = http_client("get_review_task", params)
    over_mcp = _over_mcp("get_review_task", params, session_factory)

    assert over_http == over_mcp
    assert json.loads(over_http)["task"]["id"] == task.id


@pytest.mark.postgres
def test_edit_review_draft_over_mcp_touches_only_the_unapproved_draft(session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="jis1:edit", proposal_payload=DRAFT_PAYLOAD
    )
    before = _governed_counts(session_factory)
    edited = {
        "definitions": [{"term": "churn", "statement": "customers lost in a period, edited"}],
        "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
        "fields": [
            {
                "name": "churn_rate",
                "source_ref": "table:postgres:analytics.public.churn",
                "expression": "lost / total",
            }
        ],
    }

    result = json.loads(
        _over_mcp("edit_review_draft", {"task_id": task.id, "definition": edited}, session_factory)
    )

    # The draft is replaced and marked human-edited, the task stays unapproved,
    # and no governed row was written -- the same boundary over MCP as over HTTP.
    assert result["task"]["proposal_payload"]["edited_by_human"] is True
    assert result["task"]["proposal_payload"]["governance"] == "unapproved"
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_refine_draft_has_the_same_success_shape_over_http_and_mcp(
    session_factory, revenue_slice, http_client, monkeypatch
):
    refined = {
        "definitions": [{"term": "churn", "statement": "refined by expert feedback"}],
        "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
        "fields": [
            {
                "name": "churn_rate",
                "source_ref": "table:postgres:analytics.public.churn",
                "expression": "lost / total",
            }
        ],
    }
    monkeypatch.setattr(
        operations_module,
        "_authoring_runtime",
        lambda: ScriptedRuntime(
            script=[ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": refined})]
        ),
    )
    repository = PostgresReviewRepository(session_factory)
    http_task = repository.create_task(
        reason="draft", idempotency_key="jis1:refine-http", proposal_payload=DRAFT_PAYLOAD
    )
    mcp_task = repository.create_task(
        reason="draft", idempotency_key="jis1:refine-mcp", proposal_payload=DRAFT_PAYLOAD
    )
    before = _governed_counts(session_factory)
    params = {"feedback": "make the period explicit"}

    over_http = json.loads(http_client("refine_review_draft", {"task_id": http_task.id, **params}))
    over_mcp = json.loads(
        _over_mcp("refine_review_draft", {"task_id": mcp_task.id, **params}, session_factory)
    )

    assert set(over_http) == set(over_mcp) == {"schema_version", "task", "attribution"}
    assert over_http["attribution"] == over_mcp["attribution"]
    assert over_http["task"]["proposal_payload"] == over_mcp["task"]["proposal_payload"]
    for result, task in ((over_http, http_task), (over_mcp, mcp_task)):
        payload = result["task"]["proposal_payload"]
        assert result["task"]["id"] == task.id
        assert payload["definition"] == refined
        assert payload["definition"] != DRAFT_PAYLOAD["definition"]
        assert payload["feedback"] == params["feedback"]
        assert payload["governance"] == "unapproved"
        assert payload["provenance"]["runtime"] == {"runtime": "scripted", "model": None}
        assert payload["provenance"]["prompt_hash"] == result["attribution"]["prompt_hash"]
        assert result["task"]["status"] == "open"
    assert len(repository.list_tasks()) == 2
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_propose_review_to_git_over_mcp_opens_a_branch_and_touches_no_authority(
    session_factory, tmp_path
):
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="agent-drafted candidate definition for 'revenue' (unapproved)",
        idempotency_key="jis1:propose",
        proposal_payload=DRAFT_PAYLOAD,
    )
    before = _governed_counts(session_factory)

    result = json.loads(_over_mcp("propose_review_to_git", {"task_id": task.id}, session_factory))

    proposal = result["proposal"]
    assert proposal["head_branch"].startswith("hyperset/proposal/")
    # Proposal-only over MCP too: a new branch, the base ref untouched, no merge,
    # the task still unapproved, and no governed row written (ADR 0012).
    assert git("rev-parse", "main", cwd=repo) == base_before
    assert git("rev-parse", proposal["head_branch"], cwd=repo) == proposal["commit_sha"]
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert reloaded.proposal_payload["governance"] == "unapproved"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_the_pii_guard_fails_closed_on_the_mcp_propose_boundary(
    session_factory, tmp_path, monkeypatch
):
    """The PII guard on the proposal boundary (hy-hbtz) holds over MCP: with the
    guard engaged and Presidio unhostable, propose_review_to_git over MCP FAILS
    CLOSED -- an isError result, no branch pushed, base ref untouched -- rather
    than committing unredacted content (hy-jis1 requires this over MCP too)."""
    from hyperset.security import pii

    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="jis1:propose-pii", proposal_payload=DRAFT_PAYLOAD
    )
    monkeypatch.setattr(pii, "_engines", False)  # force the can't-host state on any seat
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")

    error = json.loads(
        _over_mcp("propose_review_to_git", {"task_id": task.id}, session_factory, expect_error=True)
    )

    assert "could not open a proposal" in error["error"]["message"]
    assert git("rev-parse", "main", cwd=repo) == base_before
    assert git("branch", "--list", "hyperset/proposal/*", cwd=repo).strip() == ""


@pytest.mark.postgres
def test_a_caller_supplied_assignee_that_is_not_known_is_refused_over_mcp_and_never_echoed(
    session_factory, revenue_slice
):
    """hy-ip8do: a caller may name an 'assignee' only as a KNOWN approved reviewer. With no
    allowlist configured, a supplied assignee (here a credential URL) is an isError result
    (`invalid_params`) whose text never echoes the secret -- the reject holds on MCP, not
    only HTTP, because both go through the one shared executor."""
    text = _over_mcp(
        "set_review_assignee",
        {"task_id": "rt-x", "assigned": True, "assignee": "https://user:supersecret@host/repo"},
        session_factory,
        expect_error=True,
    )
    assert json.loads(text)["error"]["code"] == "invalid_params"
    assert "supersecret" not in text
