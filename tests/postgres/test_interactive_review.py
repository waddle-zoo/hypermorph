"""Interactive review: write-back config + proposal-only Propose (hy-8o8m).

Real server, real Postgres, a real local target repository. Proves the two
write paths the admin panel drives -- set the target, propose a task's draft --
and the hard boundary (ADR 0012): the propose trigger may OPEN A PR PROPOSAL
only. It never advances the base ref, approves, or writes a governed table.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from sqlalchemy import text

from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.repositories.postgres import (
    PostgresAnswerFeedbackRepository,
    PostgresInteractionTraceRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.review_api import (
    EDIT_REVIEW_DRAFT_PATH,
    PROPOSE_REVIEW_TO_GIT_PATH,
    REFINE_REVIEW_DRAFT_PATH,
    SET_REVIEW_ASSIGNEE_PATH,
)

DRAFT_PAYLOAD = {
    "governance": "unapproved",
    "domain": "revenue",
    "miss": {"question": "churn?", "domain": "revenue"},
    "definition": {
        "definitions": [{"term": "churn", "statement": "customers lost in a period"}],
        "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
        "fields": [
            {
                "name": "churn_rate",
                "source_ref": "table:postgres:analytics.public.churn",
                "expression": "lost / total",
            }
        ],
    },
    "produced_by": {"producer": "authoring/1", "model": None},
}


@pytest.fixture(autouse=True)
def clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


@pytest.fixture
def server_url(session_factory, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url):
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _post(url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _governed_counts(session_factory):
    with session_factory() as session:
        return {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("governed_context", "governed_context_versions", "review_decisions")
        }


@pytest.mark.postgres
def test_the_writeback_config_is_read_null_then_set_then_read(server_url, session_factory):
    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert status == 200 and payload["config"] is None

    # Setting the target is an ADMIN-surface write (hy-529x): it goes through the
    # admin api prefix, not the public one, which refuses it.
    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "/tmp/customer-repo",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
        },
    )
    assert status == 200
    assert payload["config"]["repository"] == "/tmp/customer-repo"

    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert payload["config"]["manifest_path"] == "domains/revenue"
    # Setting the target wrote NO governed row -- it configures the target only.
    assert _governed_counts(session_factory) == {
        "governed_context": 0,
        "governed_context_versions": 0,
        "review_decisions": 0,
    }


@pytest.mark.postgres
def test_a_missing_field_is_a_400_naming_what_to_set(server_url):
    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {"repository": "/tmp/x"},
    )
    assert status == 400
    assert "base_ref" in payload["error"]["message"]


@pytest.mark.postgres
def test_propose_without_a_configured_repo_tells_the_operator_to_set_one(
    server_url, session_factory
):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:none", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 400
    assert "admin" in payload["error"]["recovery"]


@pytest.mark.postgres
def test_a_url_target_without_a_token_fails_closed_before_any_clone(
    server_url, session_factory, monkeypatch
):
    """A URL target authenticates with a server-side token (hy-eji4). When the
    configured token reference resolves to nothing in the environment, propose
    FAILS CLOSED with a human message and NEVER clones -- the writer is not even
    reached."""
    from hyperset.flywheel import git_pr

    monkeypatch.delenv("HYPERSET_WRITEBACK_TOKEN", raising=False)
    PostgresWritebackConfigRepository(session_factory).set(
        repository="https://github.com/acme/context",
        base_ref="main",
        manifest_path="domains/revenue",
        token_ref="HYPERSET_WRITEBACK_TOKEN",
    )

    def _no_writer(**_kwargs):
        raise AssertionError("no clone or propose may run without a token")

    monkeypatch.setattr(git_pr, "propose_context_change", _no_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:url", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 400
    assert "server-side token" in payload["error"]["message"]


@pytest.mark.postgres
def test_a_url_target_with_a_token_opens_a_pr_and_returns_its_url(
    server_url, session_factory, monkeypatch
):
    """URL target + token present: the token is read from the environment by the
    configured NAME and handed to the writer, which opens a real PR and returns
    its URL. The raw token never appears in the API response (hy-eji4). The
    writer is faked here so the test issues no real GitHub call."""
    from hyperset.flywheel import git_pr
    from hyperset.flywheel.git_pr import ContextProposal

    monkeypatch.setenv("DEMO_WRITEBACK_TOKEN", "ghp_secret_xyz")
    PostgresWritebackConfigRepository(session_factory).set(
        repository="https://github.com/acme/context",
        base_ref="main",
        manifest_path="domains/revenue",
        token_ref="DEMO_WRITEBACK_TOKEN",
    )
    seen: dict = {}

    def _fake_writer(*, token=None, opener=None, **_kwargs):
        seen["token"] = token
        return ContextProposal(
            repository="https://github.com/acme/context",
            base_ref="main",
            head_branch="hyperset/proposal/revenue-abc123def456",
            path="domains/revenue",
            title="t",
            body="b",
            commit_sha="deadbeefcafe",
            manifest="definitions: []\n",
            pr_url="https://github.com/acme/context/pull/7",
        )

    monkeypatch.setattr(git_pr, "propose_context_change", _fake_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:urltoken", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200
    assert payload["proposal"]["pr_url"] == "https://github.com/acme/context/pull/7"
    # The token was read from the env by the configured NAME and handed to the writer...
    assert seen["token"] == "ghp_secret_xyz"
    # ...and it never appears anywhere in what the browser receives.
    assert "ghp_secret_xyz" not in json.dumps(payload)


@pytest.mark.postgres
def test_a_stale_proposal_lease_is_fenced_before_the_remote_writer(
    session_factory, tmp_path, monkeypatch
):
    """A writer paused through lease expiry must not reach the remote side effect."""
    from hyperset.repositories.errors import OptimisticConcurrencyError
    from hyperset.transport import operations

    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(tmp_path / "context-repo"),
        base_ref="main",
        manifest_path="domains/revenue",
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="lease-fence-operation", proposal_payload=DRAFT_PAYLOAD
    )
    calls: list[bool] = []

    def _writer(**_kwargs):
        calls.append(True)
        raise AssertionError("a stale proposal must not reach the remote writer")

    def _lease_lost(self, *_args, **_kwargs):
        raise OptimisticConcurrencyError("proposal reservation is no longer owned")

    monkeypatch.setattr(operations, "_PROPOSE_WRITER", _writer)
    monkeypatch.setattr(PostgresReviewRepository, "assert_proposal_lease", _lease_lost)

    with pytest.raises(operations.OperationError, match="no longer owned"):
        operations.run_operation(
            "propose_review_to_git", {"task_id": task.id}, session_factory=session_factory
        )
    assert calls == []


@pytest.mark.postgres
def test_blank_refinement_cannot_acknowledge_negative_feedback(session_factory, monkeypatch):
    from hyperset.transport import operations

    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="refine-feedback-required",
        proposal_payload={**DRAFT_PAYLOAD, "correlation_id": "corr-refine-feedback"},
    )
    PostgresInteractionTraceRepository(session_factory).record(
        workspace="default",
        principal_identity="anonymous",
        session_id="sess-refine-feedback",
        turn_id=None,
        tool_call_id=None,
        correlation_id="corr-refine-feedback",
        intent="refine",
        query="churn",
        tool_name="search_knowledge",
        search_mode="grep",
        filters={},
        hit_ids=["src-feedback:docs/churn.md:1"],
        duration_ms=1,
        source_staleness={},
        miss=None,
        answer_bundle_id=None,
        status="hit",
    )
    set_trace_context(
        TraceContext(session_id="sess-refine-feedback", correlation_id="corr-refine-feedback")
    )
    PostgresAnswerFeedbackRepository(session_factory).record(
        workspace="default",
        principal_identity="anonymous",
        session_id="sess-refine-feedback",
        correlation_id="corr-refine-feedback",
        outcome="reject",
        bundle_id=None,
        source_ref="src-feedback:docs/churn.md:1",
        review_task_id=task.id,
        notes=None,
    )
    monkeypatch.setattr(
        operations,
        "_authoring_runtime",
        lambda: pytest.fail("blank feedback must be refused before the model runs"),
    )

    with pytest.raises(operations.OperationError, match="human feedback is required"):
        operations.run_operation(
            "refine_review_draft",
            {"task_id": task.id, "feedback": "   "},
            session_factory=session_factory,
        )


@pytest.mark.postgres
def test_the_token_reference_is_stored_as_a_name_never_a_value(server_url, session_factory):
    """The config row and both API shapes carry the token REFERENCE (a name),
    never a token value (hy-eji4). There is no column or field for a value."""
    from hyperset.db.models import WRITEBACK_SINGLETON_ID, WritebackConfig

    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_ref": "HYPERSET_WRITEBACK_TOKEN",
        },
    )
    assert status == 200
    assert payload["config"]["token_ref"] == "HYPERSET_WRITEBACK_TOKEN"

    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert payload["config"]["token_ref"] == "HYPERSET_WRITEBACK_TOKEN"

    with session_factory() as session:
        row = session.get(WritebackConfig, WRITEBACK_SINGLETON_ID)
        assert row.token_ref == "HYPERSET_WRITEBACK_TOKEN"  # the NAME is stored
        # There is no attribute holding a raw token value.
        assert not hasattr(row, "token")
        assert not hasattr(row, "token_value")


@pytest.mark.postgres
def test_propose_opens_a_branch_and_never_touches_base_or_governance(
    server_url, session_factory, tmp_path
):
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="agent-drafted candidate definition for 'revenue' (unapproved)",
        idempotency_key="authoring:revenue:local",
        proposal_payload=DRAFT_PAYLOAD,
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )

    assert status == 200, payload
    proposal = payload["proposal"]
    assert proposal["head_branch"].startswith("hyperset/proposal/")
    # The proposal is a new branch; the base ref is untouched -- no merge.
    assert git("rev-parse", "main", cwd=repo) == base_before
    assert git("rev-parse", proposal["head_branch"], cwd=repo) == proposal["commit_sha"]
    manifest = git("show", f"{proposal['head_branch']}:{CONTEXT_PATH}/manifest.yaml", cwd=repo)
    assert "churn" in manifest and "recognized_revenue" in manifest
    # The task stays UNAPPROVED and nothing governed was written.
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert reloaded.proposal_payload["governance"] == "unapproved"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_public_propose_fails_closed_when_the_pii_guard_cannot_run(
    server_url, session_factory, tmp_path, monkeypatch
):
    """The reviewer PROPOSE is public now (hy-529x), so the PII guard on the
    proposal boundary (hy-hbtz) must hold on the PUBLIC path: with the guard
    engaged but Presidio unhostable, a propose through the public api prefix
    FAILS CLOSED -- no branch is pushed and the base ref is untouched -- rather
    than commit unredacted content. Forces the can't-host state so it runs on any
    seat (Overseer ruling (a): engage the guard on the public path, fail closed
    if configured-on-but-missing)."""
    from hyperset.security import pii

    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="agent-drafted candidate definition for 'revenue' (unapproved)",
        idempotency_key="authoring:revenue:pii",
        proposal_payload=DRAFT_PAYLOAD,
    )
    monkeypatch.setattr(pii, "_engines", False)  # force the can't-host state on any seat
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")

    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )

    assert status == 400
    assert "could not open a proposal" in payload["error"]["message"]
    # Fail closed: no proposal branch was pushed and the base ref never moved.
    assert git("rev-parse", "main", cwd=repo) == base_before
    branches = git("branch", "--list", "hyperset/proposal/*", cwd=repo).strip()
    assert branches == ""


# --- Interactive review c+d: edit + ask-to-refine (hy-murb) ---

from hyperset.flywheel.authoring import PROPOSE_CONTEXT_DEFINITION  # noqa: E402
from hyperset.planner.runtime import ScriptedRuntime, ToolCall  # noqa: E402
from hyperset.transport import operations as operations_module  # noqa: E402

EDITED_DEFINITION = {
    "definitions": [
        {"term": "churn", "statement": "customers lost in a period, edited by a human"}
    ],
    "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
    "fields": [
        {
            "name": "churn_rate",
            "source_ref": "table:postgres:analytics.public.churn",
            "expression": "lost / total",
        }
    ],
}


@pytest.mark.postgres
def test_editing_the_draft_persists_it_and_writes_no_governed_row(server_url, session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:edit", proposal_payload=DRAFT_PAYLOAD
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{EDIT_REVIEW_DRAFT_PATH}",
        {"task_id": task.id, "definition": EDITED_DEFINITION},
    )

    assert status == 200
    served = payload["task"]["proposal_payload"]
    assert served["definition"] == EDITED_DEFINITION
    assert served["edited_by_human"] is True
    # Still a draft, still assist-class, and no governed row was written.
    assert served["governance"] == "unapproved"
    assert payload["task"]["status"] == "open"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_editing_to_an_invalid_definition_is_refused_and_leaves_the_draft(
    server_url, session_factory
):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:badedit", proposal_payload=DRAFT_PAYLOAD
    )
    invalid = {
        "definitions": [{"term": "churn", "statement": "x"}],
        "approved_sources": [{"ref": "table:postgres:a.b.c", "role": "primary"}],
        "fields": [{"name": "f", "source_ref": "table:postgres:z.z.z", "expression": "e"}],
    }
    status, payload = _post(
        f"{server_url}/playground/api{EDIT_REVIEW_DRAFT_PATH}",
        {"task_id": task.id, "definition": invalid},
    )
    assert status == 400
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.proposal_payload["definition"] == DRAFT_PAYLOAD["definition"]


@pytest.mark.postgres
def test_refine_re_runs_authoring_and_replaces_the_draft_on_the_same_task(
    server_url, session_factory, revenue_slice, monkeypatch
):
    refined = {
        "definitions": [{"term": "churn", "statement": "refined after expert feedback"}],
        "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
        "fields": [
            {
                "name": "churn_rate",
                "source_ref": "table:postgres:analytics.public.churn",
                "expression": "lost / total",
            }
        ],
    }
    # Inject a scripted model so the whole assist path runs -- real gather, real
    # validate, real persist -- with no model server.
    monkeypatch.setattr(
        operations_module,
        "_authoring_runtime",
        lambda: ScriptedRuntime(
            script=[ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": refined})]
        ),
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:refine", proposal_payload=DRAFT_PAYLOAD
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{REFINE_REVIEW_DRAFT_PATH}",
        {"task_id": task.id, "feedback": "be more precise about the period"},
    )

    assert status == 200, payload
    # Same task, draft REPLACED, still unapproved.
    assert payload["task"]["id"] == task.id
    served = payload["task"]["proposal_payload"]
    assert served["definition"] == refined
    assert served["definition"] != DRAFT_PAYLOAD["definition"]
    assert served["governance"] == "unapproved"
    assert served["feedback"] == "be more precise about the period"
    # Attributed (Trace), and no new task, no governed row. The authoring tool
    # hash is recorded on the replacement draft; the resolve-path hash remains
    # covered by the dedicated boundary test below.
    assert payload["attribution"]["model"] == ""  # scripted runtime reports no model
    assert "prompt_hash" in payload["attribution"]
    assert set(served["provenance"]) == {"prompt_hash", "tools_hash", "model", "runtime"}
    # The draft carries the runtime's OWN provenance report under `runtime` (authoring.py
    # stores `trace.provenance`), which is the `{runtime, model}` shape the runtime reports
    # (a scripted runtime reports no model).
    assert served["provenance"]["runtime"] == {"runtime": "scripted", "model": None}
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == 1
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_refine_runtime_failure_leaves_the_original_draft_unchanged(
    server_url, session_factory, revenue_slice, monkeypatch
):
    class BrokenRuntime:
        def provenance(self):
            return {"runtime": "broken", "model": None}

        def run(self, question, *, on_message, call_tool):
            raise RuntimeError("authoring endpoint unavailable")

        def close(self):
            pass

    monkeypatch.setattr(operations_module, "_authoring_runtime", lambda: BrokenRuntime())
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:runtime-failure",
        proposal_payload=DRAFT_PAYLOAD,
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{REFINE_REVIEW_DRAFT_PATH}",
        {"task_id": task.id, "feedback": "tighten the definition"},
    )

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "no usable draft" in payload["error"]["message"]
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert reloaded.proposal_payload == DRAFT_PAYLOAD
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == 1
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_refine_of_a_task_with_no_miss_is_refused(server_url, session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="finding task", idempotency_key="proc:x", proposal_payload={}
    )
    status, payload = _post(
        f"{server_url}/playground/api{REFINE_REVIEW_DRAFT_PATH}",
        {"task_id": task.id, "feedback": "x"},
    )
    assert status == 400
    assert "miss" in payload["error"]["message"]


def test_the_resolve_path_tools_hash_is_unchanged_by_the_authoring_surface():
    """hy-murb boundary: the assist authoring surface uses its OWN tool
    declarations, so it never moves the pinned resolve-path tools_hash. The pinned
    value is fe930a003b731211 since hy-gh-281 item 3 added VALIDATE's input-schema field
    descriptions -- a resolve-path change that did move it; the authoring surface
    did not."""
    from hyperset.planner.loop import tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"


# --- Encrypted-at-rest write-back token (hy-up4k) ---

import base64  # noqa: E402


def _secret_key() -> str:
    import os

    return base64.b64encode(os.urandom(32)).decode()


@pytest.mark.postgres
def test_an_encrypted_token_is_stored_as_ciphertext_and_never_returned(
    server_url, session_factory, monkeypatch
):
    """Paste-a-token mode: the token is encrypted server-side and stored as
    ciphertext; neither the value nor the ciphertext is ever returned, and the
    plaintext never lands in the config row (hy-up4k)."""
    from hyperset.db.models import WRITEBACK_SINGLETON_ID, WritebackConfig

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    token = "ghp_secret_xyz"

    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "encrypted",
            "token": token,
        },
    )
    assert status == 200
    assert payload["config"]["token_source"] == "encrypted"
    assert payload["config"]["token_set"] is True
    assert token not in json.dumps(payload)  # value never returned

    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert payload["config"]["token_source"] == "encrypted"
    assert payload["config"]["token_set"] is True
    assert token not in json.dumps(payload)
    assert "token_ciphertext" not in payload["config"]  # ciphertext never returned either

    with session_factory() as session:
        row = session.get(WritebackConfig, WRITEBACK_SINGLETON_ID)
        assert row.token_ciphertext is not None  # ciphertext IS stored
        assert token.encode() not in row.token_ciphertext  # but never the plaintext
        assert not hasattr(row, "token")  # no plaintext column exists


@pytest.mark.postgres
def test_an_encrypted_token_is_decrypted_and_handed_to_the_writer(
    server_url, session_factory, monkeypatch
):
    """At propose time the encrypted token is decrypted in memory and handed to
    the git writer (which threads it via env, never argv -- hy-6haz); the value
    never appears in the API response (hy-up4k)."""
    from hyperset.flywheel import git_pr
    from hyperset.flywheel.git_pr import ContextProposal

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    token = "ghp_secret_xyz"
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "encrypted",
            "token": token,
        },
    )
    seen: dict = {}

    def _spy_writer(*, token=None, **_kwargs):
        seen["token"] = token
        return ContextProposal(
            repository="https://github.com/acme/context",
            base_ref="main",
            head_branch="hyperset/proposal/revenue-abc123",
            path="domains/revenue",
            title="t",
            body="b",
            commit_sha="deadbeef",
            manifest="definitions: []\n",
            pr_url="https://github.com/acme/context/pull/7",
        )

    monkeypatch.setattr(git_pr, "propose_context_change", _spy_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="up4k:enc", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )

    assert status == 200
    assert seen["token"] == token  # decrypted from the stored ciphertext
    assert payload["proposal"]["pr_url"] == "https://github.com/acme/context/pull/7"
    assert token not in json.dumps(payload)  # never in the response


@pytest.mark.postgres
def test_an_encrypted_propose_fails_closed_when_the_key_does_not_match(
    server_url, session_factory, monkeypatch
):
    """Wrong or missing KEK at propose time: refuse, never push unauthenticated
    (hy-up4k). The writer is not even reached."""
    from hyperset.flywheel import git_pr

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "encrypted",
            "token": "ghp_secret_xyz",
        },
    )
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())  # a different key

    def _no_writer(**_kwargs):
        raise AssertionError("no push may run when the token cannot be decrypted")

    monkeypatch.setattr(git_pr, "propose_context_change", _no_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="up4k:wrongkey", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 400
    assert "could not be decrypted" in payload["error"]["message"]


def _rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.mark.postgres
def test_a_github_app_key_is_stored_encrypted_and_never_returned(
    server_url, session_factory, monkeypatch
):
    """GitHub App mode: the App id is config (returned), but the App private key
    is encrypted server-side and stored as ciphertext -- neither the key nor its
    ciphertext is ever returned, and the plaintext never lands in the row
    (hy-bdhg)."""
    from hyperset.db.models import WRITEBACK_SINGLETON_ID, WritebackConfig

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    pem = _rsa_pem()

    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "github_app",
            "app_id": "123456",
            "app_private_key": pem,
        },
    )
    assert status == 200
    assert payload["config"]["token_source"] == "github_app"
    assert payload["config"]["app_id"] == 123456  # the id is config, not a secret
    assert payload["config"]["token_set"] is True
    assert pem not in json.dumps(payload)  # the key is never returned

    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert payload["config"]["token_source"] == "github_app"
    assert payload["config"]["app_id"] == 123456
    assert payload["config"]["token_set"] is True
    assert pem not in json.dumps(payload)
    assert "app_key_ciphertext" not in payload["config"]  # ciphertext never returned

    with session_factory() as session:
        row = session.get(WritebackConfig, WRITEBACK_SINGLETON_ID)
        assert row.app_key_ciphertext is not None  # ciphertext IS stored
        assert pem.encode() not in row.app_key_ciphertext  # but never the plaintext key
        assert not hasattr(row, "app_private_key")  # no plaintext column exists


@pytest.mark.postgres
def test_a_github_app_propose_mints_and_hands_the_installation_token_to_the_writer(
    server_url, session_factory, monkeypatch
):
    """At propose time the App private key is decrypted, a per-op installation
    token is minted from it, and THAT token (not the key) is handed to the git
    writer via env (hy-6haz); neither the key nor the minted token appears in the
    response (hy-bdhg)."""
    from hyperset.flywheel import git_pr
    from hyperset.flywheel.git_pr import ContextProposal
    from hyperset.security import github_app

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    pem = _rsa_pem()
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "github_app",
            "app_id": "123456",
            "app_private_key": pem,
        },
    )
    minted: dict = {}

    def _fake_mint(*, app_id, private_key, repository):
        # The decrypted private key reaches the mint; the App id and repo too.
        minted["app_id"] = app_id
        minted["private_key"] = private_key
        minted["repository"] = repository
        return "ghs_minted_installation_token"

    monkeypatch.setattr(github_app, "mint_installation_token", _fake_mint)

    seen: dict = {}

    def _spy_writer(*, token=None, **_kwargs):
        seen["token"] = token
        return ContextProposal(
            repository="https://github.com/acme/context",
            base_ref="main",
            head_branch="hyperset/proposal/revenue-abc123",
            path="domains/revenue",
            title="t",
            body="b",
            commit_sha="deadbeef",
            manifest="definitions: []\n",
            pr_url="https://github.com/acme/context/pull/9",
        )

    monkeypatch.setattr(git_pr, "propose_context_change", _spy_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="bdhg:app", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )

    assert status == 200
    assert minted["app_id"] == 123456
    assert minted["private_key"] == pem  # decrypted from the stored ciphertext
    assert minted["repository"] == "https://github.com/acme/context"
    assert seen["token"] == "ghs_minted_installation_token"  # the MINTED token, not the key
    assert payload["proposal"]["pr_url"] == "https://github.com/acme/context/pull/9"
    body = json.dumps(payload)
    assert pem not in body and "ghs_minted_installation_token" not in body


@pytest.mark.postgres
def test_a_github_app_propose_fails_closed_when_the_key_cannot_decrypt(
    server_url, session_factory, monkeypatch
):
    """Wrong or missing KEK at propose time: refuse before minting, never push
    unauthenticated (hy-bdhg). Neither the mint nor the writer is reached."""
    from hyperset.flywheel import git_pr
    from hyperset.security import github_app

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "github_app",
            "app_id": "123456",
            "app_private_key": _rsa_pem(),
        },
    )
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())  # a different key

    def _no_mint(**_kwargs):
        raise AssertionError("no token may be minted when the key cannot be decrypted")

    def _no_writer(**_kwargs):
        raise AssertionError("no push may run when the key cannot be decrypted")

    monkeypatch.setattr(github_app, "mint_installation_token", _no_mint)
    monkeypatch.setattr(git_pr, "propose_context_change", _no_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="bdhg:wrongkey", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 400
    assert "could not be decrypted" in payload["error"]["message"]


@pytest.mark.postgres
def test_a_github_app_propose_fails_closed_when_the_mint_fails(
    server_url, session_factory, monkeypatch
):
    """A mint failure (bad key, App not installed, GitHub error) refuses the
    propose and never pushes unauthenticated (hy-bdhg)."""
    from hyperset.flywheel import git_pr
    from hyperset.security import github_app
    from hyperset.security.github_app import GitHubAppError

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": "domains/revenue",
            "token_source": "github_app",
            "app_id": "123456",
            "app_private_key": _rsa_pem(),
        },
    )

    def _mint_refuses(**_kwargs):
        raise GitHubAppError("the Hyperset GitHub App is not installed on acme/context")

    def _no_writer(**_kwargs):
        raise AssertionError("no push may run when the token cannot be minted")

    monkeypatch.setattr(github_app, "mint_installation_token", _mint_refuses)
    monkeypatch.setattr(git_pr, "propose_context_change", _no_writer)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="bdhg:mintfail", proposal_payload=DRAFT_PAYLOAD
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 400
    assert "could not mint" in payload["error"]["message"]
    assert "not installed" in payload["error"]["message"]


@pytest.mark.postgres
def test_propose_with_a_nonexistent_task_diagnoses_the_task_not_the_writeback_config(
    server_url, session_factory
):
    """Item 7 (hy-gh-281): input is validated BEFORE deployment config. A bad
    task_id is a typo and must be diagnosed as 'no review task ...', like the
    sibling task-scoped tools -- not 'no write-back repository is configured',
    which sends the caller off to fix the wrong thing. No write-back config is
    set here, so the OLD order returned the config error for this same input."""
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": "rt-nope"}
    )

    assert status == 400
    assert "no review task" in payload["error"]["message"]
    assert "rt-nope" in payload["error"]["message"]
    assert "write-back" not in payload["error"]["message"]


@pytest.mark.postgres
def test_list_review_tasks_treats_empty_status_as_all_not_a_filter(session_factory):
    """Item 5 nit (hy-gh-281): an empty (or whitespace) status means 'all tasks',
    normalized to agree with the enum schema that has no empty member -- never a
    filter that returns nothing. Omitting the field and sending '' are the same."""
    from hyperset.transport.operations import run_operation

    PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="list:empty-status", proposal_payload=DRAFT_PAYLOAD
    )

    all_tasks = run_operation("list_review_tasks", {}, session_factory=session_factory)["tasks"]
    empty = run_operation("list_review_tasks", {"status": ""}, session_factory=session_factory)
    blank = run_operation("list_review_tasks", {"status": "  "}, session_factory=session_factory)

    assert len(all_tasks) == 1
    assert empty["tasks"] == all_tasks  # '' is 'all', not an empty result
    assert blank["tasks"] == all_tasks  # whitespace too


@pytest.mark.postgres
def test_the_verified_proposer_stays_local_to_the_guarded_pr_flow(
    server_url, session_factory, tmp_path, monkeypatch
):
    """The task remains traceable without exporting the verified caller to a remote PR."""
    from hyperset.flywheel import git_pr
    from hyperset.flywheel.git_pr import ContextProposal
    from hyperset.security.authz import Principal
    from hyperset.transport.operations import PROPOSE_REVIEW_TO_GIT, run_operation

    repo = make_repository(tmp_path)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    seen: dict = {}

    def _spy(*, review=None, **_kwargs):
        seen["review"] = review
        return ContextProposal(
            repository=str(repo),
            base_ref="main",
            head_branch="hyperset/proposal/rt-x-abc123",
            path=CONTEXT_PATH,
            title="t",
            body=git_pr._proposal_body(review, base_commit="c0ffee", base_ref="main"),
            commit_sha="deadbeefcafe",
            manifest="definitions: []\n",
            pr_url="https://example.test/customer/context/pull/1",
        )

    monkeypatch.setattr(git_pr, "propose_context_change", _spy)

    verified = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:proposer", proposal_payload=DRAFT_PAYLOAD
    )
    principal = Principal("sub-x", "https://iss.example", roles=("reviewer",))
    run_operation(
        PROPOSE_REVIEW_TO_GIT,
        {"task_id": verified.id},
        session_factory=session_factory,
        principal=principal,
    )
    assert "proposer" not in seen["review"]

    anon = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="authoring:revenue:anon", proposal_payload=DRAFT_PAYLOAD
    )
    run_operation(
        PROPOSE_REVIEW_TO_GIT,
        {"task_id": anon.id},
        session_factory=session_factory,
        principal=None,
    )
    assert "proposer" not in seen["review"]


@pytest.mark.postgres
def test_self_claim_and_unclaim_through_the_executor(session_factory):
    """hy-s8a6 S1 (mayor rework): set_review_assignee SELF-CLAIMS -- the owner is the
    caller's OWN verified identity, computed by the server, never caller free text. assigned
    true claims for the caller, false unassigns; the surfaced owner is exactly the caller's
    opaque subject@issuer. Assignment is metadata: the status is NOT advanced and no governed
    row is written."""
    from hyperset.bundle.schema import SCHEMA_VERSION
    from hyperset.security.authz import Principal
    from hyperset.transport.operations import GET_REVIEW_TASK, SET_REVIEW_ASSIGNEE, run_operation

    repo = PostgresReviewRepository(session_factory)
    task = repo.create_task(
        reason="draft", idempotency_key="authoring:revenue:assign", proposal_payload=DRAFT_PAYLOAD
    )
    before = run_operation(GET_REVIEW_TASK, {"task_id": task.id}, session_factory=session_factory)
    assert before["task"]["assignee"] is None
    assert before["task"]["status"] == "open"

    caller = Principal(subject="auth0|abc123", issuer="https://issuer.example", roles=("reviewer",))
    result = run_operation(
        SET_REVIEW_ASSIGNEE,
        {"task_id": task.id, "assigned": True},
        session_factory=session_factory,
        principal=caller,
    )
    assert result["schema_version"] == SCHEMA_VERSION
    # The owner is the CALLER's computed identity -- not anything the caller typed.
    assert result["task"]["assignee"] == "auth0|abc123@https://issuer.example"
    assert result["task"]["status"] == "open"  # metadata, not an approval
    assert repo.get_task(task.id).assignee == "auth0|abc123@https://issuer.example"

    cleared = run_operation(
        SET_REVIEW_ASSIGNEE,
        {"task_id": task.id, "assigned": False},
        session_factory=session_factory,
        principal=caller,
    )
    assert cleared["task"]["assignee"] is None
    assert repo.get_task(task.id).assignee is None


@pytest.mark.postgres
def test_list_review_tasks_suggests_a_prior_in_domain_owner(session_factory, tmp_path, monkeypatch):
    """hy-38mk8 (S3): an unassigned task is served with a `suggested_assignee` HINT --
    the prior in-domain reviewer -- plus a served `suggested_assignee_rationale`, while an
    owned task and a task in a domain with no prior owner carry NO suggestion
    (byte-identical to before). The candidate is filtered through the reviewer allowlist,
    so only a KNOWN approved reviewer is ever suggested (r2). The hint is assist-only: it
    never assigns anyone; the task stays unowned until a human self-claims (S1)."""
    from hyperset.bundle.schema import SCHEMA_VERSION
    from hyperset.security.authz import ENABLED_ENV, Principal
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import (
        LIST_REVIEW_TASKS,
        SET_REVIEW_ASSIGNEE,
        SUGGESTION_SIGNAL,
        run_operation,
    )

    # The prior owner must be an APPROVED reviewer to be suggested, and the feature is
    # gated on the authz gate being ON (r2).
    allow = tmp_path / "reviewers.allow"
    allow.write_text("auth0|abc123@https://issuer.example\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(allow))
    monkeypatch.setenv(ENABLED_ENV, "1")

    marketing_payload = {
        **DRAFT_PAYLOAD,
        "domain": "marketing",
        "miss": {"question": "spend?", "domain": "marketing"},
    }
    repo = PostgresReviewRepository(session_factory)
    owned = repo.create_task(
        reason="draft", idempotency_key="suggest:revenue:owned", proposal_payload=DRAFT_PAYLOAD
    )
    gap = repo.create_task(
        reason="draft", idempotency_key="suggest:revenue:gap", proposal_payload=DRAFT_PAYLOAD
    )
    other_domain = repo.create_task(
        reason="draft",
        idempotency_key="suggest:marketing:gap",
        proposal_payload=marketing_payload,
    )

    caller = Principal(subject="auth0|abc123", issuer="https://issuer.example", roles=("reviewer",))
    run_operation(
        SET_REVIEW_ASSIGNEE,
        {"task_id": owned.id, "assigned": True},
        session_factory=session_factory,
        principal=caller,
    )

    # Authz is ON, so the LIST is made by the same approved reviewer.
    listed = run_operation(LIST_REVIEW_TASKS, {}, session_factory=session_factory, principal=caller)
    assert listed["schema_version"] == SCHEMA_VERSION
    by_id = {task["id"]: task for task in listed["tasks"]}

    # The revenue gap is hinted at the prior revenue owner -- a suggestion, not an assignment.
    assert by_id[gap.id]["suggested_assignee"] == "auth0|abc123@https://issuer.example"
    assert by_id[gap.id]["suggested_assignee_rationale"]["signal"] == SUGGESTION_SIGNAL
    assert by_id[gap.id]["suggested_assignee_rationale"]["assist"] is True
    assert by_id[gap.id]["assignee"] is None  # still UNOWNED; the human must confirm
    # An already-owned task gets no hint; a domain with no prior owner gets no hint.
    assert "suggested_assignee" not in by_id[owned.id]
    assert "suggested_assignee" not in by_id[other_domain.id]


@pytest.mark.postgres
def test_list_review_tasks_never_suggests_an_unapproved_prior_owner(
    session_factory, tmp_path, monkeypatch
):
    """hy-38mk8 r2: a prior owner who is NOT on the reviewer allowlist (e.g. a legacy
    `anonymous` id an authz-off self-claim stored before the gate was enabled) is never
    suggested. With authz ON and the allowlist naming only the reviewer doing the listing,
    the gap carries NO suggestion -- the candidate itself must be an approved principal, not
    merely redacted."""
    from hyperset.security.authz import ENABLED_ENV, Principal
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import LIST_REVIEW_TASKS, run_operation

    lister = "auth0|lister@https://issuer.example"
    allow = tmp_path / "reviewers.allow"
    allow.write_text(lister + "\n", encoding="utf-8")  # names the LISTER, not the legacy owner
    monkeypatch.setenv(ALLOWLIST_ENV, str(allow))
    monkeypatch.setenv(ENABLED_ENV, "1")

    repo = PostgresReviewRepository(session_factory)
    owned = repo.create_task(
        reason="draft", idempotency_key="unapproved:revenue:owned", proposal_payload=DRAFT_PAYLOAD
    )
    gap = repo.create_task(
        reason="draft", idempotency_key="unapproved:revenue:gap", proposal_payload=DRAFT_PAYLOAD
    )
    # A legacy unapproved owner sitting in the DB (not on the allowlist).
    repo.set_assignee(owned.id, "anonymous")
    assert repo.get_task(owned.id).assignee == "anonymous"

    principal = Principal(
        subject="auth0|lister", issuer="https://issuer.example", roles=("reviewer",)
    )
    listed = run_operation(
        LIST_REVIEW_TASKS, {}, session_factory=session_factory, principal=principal
    )
    by_id = {task["id"]: task for task in listed["tasks"]}
    assert "suggested_assignee" not in by_id[gap.id]


@pytest.mark.postgres
def test_set_assignee_requires_the_assigned_boolean(session_factory):
    from hyperset.transport.operations import SET_REVIEW_ASSIGNEE, OperationError, run_operation

    # Missing 'assigned' is a clear 400, not a silent default.
    with pytest.raises(OperationError) as missing:
        run_operation(SET_REVIEW_ASSIGNEE, {"task_id": "rt-x"}, session_factory=session_factory)
    assert missing.value.code == "invalid_params"
    # A non-bool 'assigned' is refused too.
    with pytest.raises(OperationError) as not_bool:
        run_operation(
            SET_REVIEW_ASSIGNEE,
            {"task_id": "rt-x", "assigned": "yes"},
            session_factory=session_factory,
        )
    assert not_bool.value.code == "invalid_params"


@pytest.mark.postgres
def test_set_assignee_on_a_nonexistent_task_is_a_clean_refusal(session_factory):
    from hyperset.transport.operations import SET_REVIEW_ASSIGNEE, OperationError, run_operation

    with pytest.raises(OperationError) as caught:
        run_operation(
            SET_REVIEW_ASSIGNEE,
            {"task_id": "rt-nope", "assigned": True},
            session_factory=session_factory,
        )
    assert caught.value.code == "invalid_request"
    assert "review task" in caught.value.message


@pytest.mark.postgres
def test_the_assign_route_self_claims_over_http(server_url, session_factory):
    # Over HTTP on loopback (no verified principal) the caller identity is the opaque
    # 'anonymous' -- the server computes it; the caller never supplies it. Claim then unclaim.
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:httpassign",
        proposal_payload=DRAFT_PAYLOAD,
    )
    status, payload = _post(
        f"{server_url}/playground/api{SET_REVIEW_ASSIGNEE_PATH}",
        {"task_id": task.id, "assigned": True},
    )
    assert status == 200
    assert payload["task"]["assignee"] == "anonymous"
    assert PostgresReviewRepository(session_factory).get_task(task.id).assignee == "anonymous"

    status, payload = _post(
        f"{server_url}/playground/api{SET_REVIEW_ASSIGNEE_PATH}",
        {"task_id": task.id, "assigned": False},
    )
    assert status == 200 and payload["task"]["assignee"] is None


@pytest.mark.postgres
def test_a_caller_supplied_assignee_that_is_not_a_known_reviewer_is_refused_over_http(
    server_url, session_factory
):
    """hy-ip8do: a caller MAY name an 'assignee' only as a KNOWN approved reviewer. With no
    allowlist configured (this test) there is no registry to resolve against, so ANY
    supplied assignee -- a credential URL, an email, a PII-shaped opaque subject -- is
    refused (`invalid_params`), stores nothing, and is never echoed. The hy-s8a6 property
    holds: a caller can never pin an arbitrary/typed subject onto a task."""
    repo = PostgresReviewRepository(session_factory)
    task = repo.create_task(
        reason="draft",
        idempotency_key="authoring:revenue:badassign",
        proposal_payload=DRAFT_PAYLOAD,
    )
    for bad in (
        "https://user:supersecret@host/repo",
        "123-45-6789@https://issuer.example",  # well-formed, but on NO allowlist
        "alice@example.com",
    ):
        status, payload = _post(
            f"{server_url}/playground/api{SET_REVIEW_ASSIGNEE_PATH}",
            {"task_id": task.id, "assigned": True, "assignee": bad},
        )
        assert status == 400, bad
        assert payload["error"]["code"] == "invalid_params", bad
        blob = json.dumps(payload)
        assert "supersecret" not in blob and "123-45-6789" not in blob

    assert repo.get_task(task.id).assignee is None


@pytest.mark.postgres
def test_a_legacy_credential_assignee_is_redacted_in_the_served_view(session_factory):
    """Defense in depth: even a value written BYPASSING the op validator (a legacy row, or
    any other write path) is redacted at the served boundary `_review_task_view`, which
    both HTTP and MCP share -- so a credential in `assignee` can never be served."""
    from hyperset.transport.operations import GET_REVIEW_TASK, run_operation

    repo = PostgresReviewRepository(session_factory)
    task = repo.create_task(
        reason="draft", idempotency_key="authoring:revenue:legacy", proposal_payload=DRAFT_PAYLOAD
    )
    # Direct repo write -- the op validator never saw this.
    repo.set_assignee(task.id, "https://user:supersecret@host/repo")

    view = run_operation(GET_REVIEW_TASK, {"task_id": task.id}, session_factory=session_factory)[
        "task"
    ]
    assert "supersecret" not in view["assignee"]
    assert "user:supersecret@" not in view["assignee"]
    assert view["assignee"] == "https://host/repo"  # userinfo stripped, host preserved


@pytest.mark.postgres
def test_assign_a_task_to_another_known_reviewer(
    server_url, session_factory, monkeypatch, tmp_path
):
    """hy-ip8do: a caller may assign a task to ANOTHER user ONLY when that user is a KNOWN
    approved identity in the reviewer allowlist -- never typed free text. An unlisted target
    is refused and changes nothing; and without the allowlist configured, assigning another
    is impossible at all (no registry to resolve against)."""
    from hyperset.transport.operations import (
        SET_REVIEW_ASSIGNEE,
        OperationError,
        run_operation,
    )

    target = "alice@https://issuer.example"
    allow = tmp_path / "reviewers.allow"
    allow.write_text(target + "\n", encoding="utf-8")
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(allow))

    repo = PostgresReviewRepository(session_factory)
    task = repo.create_task(
        reason="draft",
        idempotency_key="authoring:revenue:assignother",
        proposal_payload=DRAFT_PAYLOAD,
    )

    # Assign to the KNOWN target.
    result = run_operation(
        SET_REVIEW_ASSIGNEE,
        {"task_id": task.id, "assigned": True, "assignee": target},
        session_factory=session_factory,
    )
    assert result["task"]["assignee"] == target
    assert repo.get_task(task.id).assignee == target

    # An UNLISTED target is refused, and the assignment is unchanged.
    with pytest.raises(OperationError) as unknown:
        run_operation(
            SET_REVIEW_ASSIGNEE,
            {"task_id": task.id, "assigned": True, "assignee": "mallory@https://issuer.example"},
            session_factory=session_factory,
        )
    assert unknown.value.code == "invalid_params"
    assert repo.get_task(task.id).assignee == target

    # WITHOUT the allowlist configured, assigning another user is impossible.
    monkeypatch.delenv("HYPERSET_REVIEWER_ALLOWLIST", raising=False)
    with pytest.raises(OperationError) as no_registry:
        run_operation(
            SET_REVIEW_ASSIGNEE,
            {"task_id": task.id, "assigned": True, "assignee": target},
            session_factory=session_factory,
        )
    assert no_registry.value.code == "invalid_params"
    # Self-claim still works with no allowlist (the caller's own identity, not the registry).
    self_claim = run_operation(
        SET_REVIEW_ASSIGNEE, {"task_id": task.id, "assigned": True}, session_factory=session_factory
    )
    assert self_claim["task"]["assignee"] == "anonymous"


@pytest.mark.postgres
def test_the_assign_route_assigns_a_known_reviewer_over_http(
    server_url, session_factory, monkeypatch, tmp_path
):
    target = "alice@https://issuer.example"
    allow = tmp_path / "reviewers.allow"
    allow.write_text(target + "\n", encoding="utf-8")
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(allow))
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:httpother",
        proposal_payload=DRAFT_PAYLOAD,
    )

    status, payload = _post(
        f"{server_url}/playground/api{SET_REVIEW_ASSIGNEE_PATH}",
        {"task_id": task.id, "assigned": True, "assignee": target},
    )
    assert status == 200 and payload["task"]["assignee"] == target

    # An unlisted/credential-shaped target: refused, value never echoed.
    status, payload = _post(
        f"{server_url}/playground/api{SET_REVIEW_ASSIGNEE_PATH}",
        {
            "task_id": task.id,
            "assigned": True,
            "assignee": "user:supersecret@https://issuer.example",
        },
    )
    assert status == 400 and payload["error"]["code"] == "invalid_params"
    assert "supersecret" not in json.dumps(payload)


@pytest.mark.postgres
def test_get_review_task_shows_current_meaning_uncertainty_and_the_exact_diff(session_factory):
    """hy-z6zv (V1 gap Reviewer/2): task detail carries the governed CURRENT meaning beside the
    proposed draft, the unresolved uncertainty, and the EXACT current-vs-proposed diff -- the
    diff that today only materialises inside the PR. With nothing governed for the domain yet,
    the current meaning is null and every proposed entry reads as added."""
    from hyperset.bundle.schema import SCHEMA_VERSION
    from hyperset.transport.operations import GET_REVIEW_TASK, run_operation

    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="z6zv:new",
        proposal_payload={**DRAFT_PAYLOAD, "undeclared_concepts": ["expansion_revenue"]},
    )

    served = run_operation(GET_REVIEW_TASK, {"task_id": task.id}, session_factory=session_factory)
    assert served["schema_version"] == SCHEMA_VERSION == 26
    view = served["task"]

    # Nothing governed for 'revenue' yet -> no current meaning to show beside the draft.
    assert view["current_meaning"] is None
    # The unresolved uncertainty is the miss's undeclared concepts, assist-labelled.
    assert view["uncertainty"] == {"undeclared_concepts": ["expansion_revenue"], "assist": True}
    # The exact diff: against no current meaning, every proposed entry is added.
    diff = view["proposed_diff"]
    assert [e["term"] for e in diff["sections"]["definitions"]["added"]] == ["churn"]
    assert [e["name"] for e in diff["sections"]["fields"]["added"]] == ["churn_rate"]
    assert diff["sections"]["definitions"]["changed"] == []
    # The proposed draft itself is still served beside it (unchanged).
    assert view["proposal_payload"]["definition"] == DRAFT_PAYLOAD["definition"]


@pytest.mark.postgres
def test_detail_puts_the_governed_current_meaning_beside_the_draft_and_diffs_the_change(
    session_factory,
):
    """The side-by-side case: a governed definition already declares `churn`, and the proposal
    RESTATES it. Detail shows the governed current meaning, and the diff reports `churn` as a
    CHANGE (before/after), not an add -- by the same entry identity the proposal PR merges on.
    list_review_tasks carries the same detail, since the UI renders the queue from it."""
    from hyperset.repositories.postgres import PostgresGovernedContextRepository
    from hyperset.transport.operations import GET_REVIEW_TASK, LIST_REVIEW_TASKS, run_operation

    PostgresGovernedContextRepository(session_factory).propose_version(
        context_type="metric",
        domain="revenue",
        name="churn",
        title="Churn",
        definition={"definitions": [{"term": "churn", "statement": "old governed meaning"}]},
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="z6zv:restate", proposal_payload=DRAFT_PAYLOAD
    )

    view = run_operation(GET_REVIEW_TASK, {"task_id": task.id}, session_factory=session_factory)[
        "task"
    ]
    current = view["current_meaning"]
    assert current is not None
    assert current["definitions"] == [{"term": "churn", "statement": "old governed meaning"}]

    (changed,) = view["proposed_diff"]["sections"]["definitions"]["changed"]
    assert changed["identity"] == "churn"
    assert changed["before"]["statement"] == "old governed meaning"
    assert changed["after"]["statement"] == "customers lost in a period"

    # The list view feeds the UI queue, so it carries the same detail for the task.
    listed = run_operation(LIST_REVIEW_TASKS, {}, session_factory=session_factory)["tasks"]
    (row,) = [t for t in listed if t["id"] == task.id]
    assert row["current_meaning"] == current
    assert row["proposed_diff"] == view["proposed_diff"]
    assert row["uncertainty"] == view["uncertainty"]


@pytest.mark.postgres
def test_review_detail_does_not_cross_workspace_for_current_meaning(session_factory):
    """A task in tenant-a must not display a governed context owned by tenant-b."""
    from hyperset.repositories.postgres import PostgresGovernedContextRepository
    from hyperset.security.authz import Principal
    from hyperset.transport.operations import GET_REVIEW_TASK, run_operation

    PostgresGovernedContextRepository(session_factory).propose_version(
        context_type="metric",
        domain="revenue",
        name="churn",
        title="Churn",
        definition={"definitions": [{"term": "churn", "statement": "tenant b meaning"}]},
        workspace="tenant-b",
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="workspace-current-meaning",
        workspace="tenant-a",
        proposal_payload=DRAFT_PAYLOAD,
    )

    view = run_operation(
        GET_REVIEW_TASK,
        {"task_id": task.id},
        session_factory=session_factory,
        principal=Principal(subject="a", issuer="issuer", workspace="tenant-a"),
    )["task"]
    assert view["current_meaning"] is None


@pytest.mark.postgres
def test_preview_renders_current_proposed_questions_and_checks_and_writes_no_governed_row(
    server_url, session_factory
):
    """hy-nauw (V1 gap Reviewer/4): the ephemeral preview HTTP route renders current-vs-proposed
    meaning, representative questions, and regression checks for a task's UNAPPROVED draft. It is
    NOT SERVING -- `not_serving` rides on the payload and no governed row is written."""
    before = _governed_counts(session_factory)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="nauw:preview", proposal_payload=DRAFT_PAYLOAD
    )

    status, payload = _get(f"{server_url}/v0/review/tasks/preview?task_id={task.id}")
    assert status == 200
    assert payload["not_serving"] is True
    assert payload["task_id"] == task.id
    assert payload["current_meaning"] is None  # nothing governed for 'revenue' yet
    assert payload["proposed_meaning"] == DRAFT_PAYLOAD["definition"]
    # The diff a reviewer reads (reused from the task detail): against no current, all added.
    assert [e["term"] for e in payload["diff"]["sections"]["definitions"]["added"]] == ["churn"]
    # Representative questions: the miss question, then one per proposed term.
    assert payload["representative_questions"][0] == "churn?"
    # Deterministic regression checks, both passing for a valid add-only draft.
    checks = {c["check"]: c["status"] for c in payload["regression_checks"]}
    assert checks == {
        "proposed_definition_validates": "pass",
        "preserves_existing_governed_meaning": "pass",
    }
    # NOT SERVING: rendering a preview wrote no governed row.
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_preview_flags_a_change_to_an_existing_governed_meaning_as_a_regression(
    server_url, session_factory
):
    """When the domain already governs the term, restating it is a regression WARN, and the
    governed current meaning is shown beside the proposed draft -- all without a governed write."""
    from hyperset.repositories.postgres import PostgresGovernedContextRepository

    PostgresGovernedContextRepository(session_factory).propose_version(
        context_type="metric",
        domain="revenue",
        name="churn",
        title="Churn",
        definition={"definitions": [{"term": "churn", "statement": "old governed meaning"}]},
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="nauw:regress", proposal_payload=DRAFT_PAYLOAD
    )

    status, payload = _get(f"{server_url}/v0/review/tasks/preview?task_id={task.id}")
    assert status == 200
    assert payload["current_meaning"]["definitions"] == [
        {"term": "churn", "statement": "old governed meaning"}
    ]
    preserves = next(
        c
        for c in payload["regression_checks"]
        if c["check"] == "preserves_existing_governed_meaning"
    )
    assert preserves["status"] == "warn"
    assert preserves["detail"] == ["definitions: changed churn"]


@pytest.mark.postgres
def test_preview_needs_a_task_id_and_404s_a_missing_task(server_url):
    status, payload = _get(f"{server_url}/v0/review/tasks/preview")
    assert status == 400
    assert "task_id" in payload["error"]["message"]

    status, payload = _get(f"{server_url}/v0/review/tasks/preview?task_id=rt-nope")
    assert status == 400
    assert "no review task" in payload["error"]["message"]


@pytest.mark.postgres
def test_request_evidence_regathers_and_replaces_the_tasks_gathered_sources(
    server_url, session_factory, revenue_slice
):
    """hy-to8m (V1 gap Reviewer/3): request-evidence re-runs the DETERMINISTIC step-2 gather for
    a task and REPLACES its assist gathered_sources. NOT SERVING -- it writes only the
    UNAPPROVED payload, advances no status, and writes no governed row."""
    stale = {**DRAFT_PAYLOAD, "gathered_sources": [{"rank": 99, "ref": "table:stale:gone"}]}
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="to8m:evidence", proposal_payload=stale
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api/v0/review/tasks/request-evidence", {"task_id": task.id}
    )
    assert status == 200, payload

    served = payload["task"]["proposal_payload"]
    gathered = served["gathered_sources"]
    assert isinstance(gathered, list)
    # The stale, hand-set entry is GONE -- the gather was re-run, not appended to.
    assert all(entry.get("ref") != "table:stale:gone" for entry in gathered)
    # Fresh candidates are observed assist evidence (revenue_slice seeds an observed estate).
    assert gathered, "re-gather over the seeded estate found no candidates"
    assert all(entry.get("governance") == "observed" for entry in gathered)
    # NOT SERVING: same task, still unapproved, no governed row written.
    assert payload["task"]["id"] == task.id
    assert served["governance"] == "unapproved"
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == 1
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_request_evidence_of_a_task_with_no_domain_is_refused(server_url, session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="finding", idempotency_key="to8m:nodomain", proposal_payload={}
    )
    status, payload = _post(
        f"{server_url}/playground/api/v0/review/tasks/request-evidence", {"task_id": task.id}
    )
    assert status == 400
    assert "no domain" in payload["error"]["message"]


@pytest.mark.postgres
def test_request_evidence_does_not_exist_when_the_playground_is_off(session_factory, monkeypatch):
    monkeypatch.delenv("HYPERSET_PLAYGROUND_ENABLED", raising=False)
    import threading

    from hyperset.transport.http import build_server

    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status, _payload = _post(f"{base}/v0/review/tasks/request-evidence", {"task_id": "rt-x"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.postgres
def test_correlation_id_is_fresh_per_request_returned_in_the_header_and_stamped_on_the_audit_row(
    server_url, session_factory
):
    """hy-w9ntg: each admin action carries a per-request correlation id, returned as
    X-Correlation-Id and stamped on its audit row. Two admin mutations on ONE keep-alive
    connection get DISTINCT ids -- the id is minted PER REQUEST, never once per connection, so a
    reused socket cannot bleed the first request's id into the second (the cross-request-leak
    hazard)."""
    import http.client

    from hyperset.repositories.postgres import PostgresAdminAuditRepository
    from hyperset.repositories.scope import ALL_WORKSPACES

    host, port = server_url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    returned = []
    for repo_path in ("/tmp/customer-a", "/tmp/customer-b"):
        body = json.dumps(
            {"repository": repo_path, "base_ref": "main", "manifest_path": "domains/revenue"}
        )
        conn.request(
            "POST",
            "/admin/api/v0/review/writeback-config",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        header_id = response.getheader("X-Correlation-Id")
        response.read()  # drain so the socket stays clean and keep-alive holds
        assert response.status == 200
        assert header_id, "every admin response returns its correlation id"
        returned.append(header_id)
    conn.close()

    # Two requests, one connection, DIFFERENT ids -> minted per request, no connection-level leak.
    assert returned[0] != returned[1]
    # Each returned id is stamped on the audit row that request wrote.
    rows = PostgresAdminAuditRepository(session_factory).list(workspace=ALL_WORKSPACES)
    set_rows = [r for r in rows if r.action == "writeback_config.set"]
    assert {r.correlation_id for r in set_rows} >= set(returned)


@pytest.mark.postgres
def test_audit_export_is_configure_gated_redacts_userinfo_and_surfaces_the_correlation_id(
    server_url, session_factory
):
    """hy-w9ntg: the export re-redacts URL userinfo (defense in depth over an unredacted legacy
    row) and surfaces the correlation id, CONFIGURE-gated on the admin surface."""
    from hyperset.observability.correlation import set_correlation_id
    from hyperset.repositories.postgres import PostgresAdminAuditRepository

    # A row written directly with a credential-bearing detail (bypassing the write-site
    # redaction) and a known correlation id.
    set_correlation_id("req-export-1")
    PostgresAdminAuditRepository(session_factory).record(
        actor="alice",
        action="writeback_config.set",
        result="ok",
        target="https://user:supersecret@github.com/org/repo",
        detail="repo=https://user:supersecret@github.com/org/repo@main",
    )
    set_correlation_id(None)

    status, payload = _get(f"{server_url}/admin/api/v0/audit/export")
    assert status == 200
    assert payload["export"] == "admin_audit"
    record = next(r for r in payload["records"] if r["actor"] == "alice")
    # The export STRIPPED the URL userinfo from both free-text fields.
    assert "supersecret" not in json.dumps(record)
    assert record["target"] == "https://github.com/org/repo"
    assert record["detail"] == "repo=https://github.com/org/repo@main"
    # ...and surfaces the correlation id.
    assert record["correlation_id"] == "req-export-1"


@pytest.mark.postgres
def test_audit_export_is_admin_surface_only(server_url):
    # The public playground prefix does not answer the export (it is a config surface).
    status, _payload = _get(f"{server_url}/playground/api/v0/audit/export")
    assert status == 404
