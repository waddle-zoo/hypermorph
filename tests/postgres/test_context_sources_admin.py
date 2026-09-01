"""Admin management of context (read) SOURCES over the existing model + sync path
(hq-3fjt, multi-repo slice 4 -- the read-side analog of slice 2).

Real server, real Postgres, a real local Git repository. Proves the admin manage
surface -- list / add / validate / sync / enable-disable / remove -- over the
EXISTING ContextSource model and `sync_git_context` (not a reimplementation): the
list is admin CONFIGURE-gated and surfaces the estate domain-CONFLICT the resolver
refuses on (never silently merged); a disabled source is excluded from serving;
removal is non-destructive of governed history (ADR 0012). No secret value is ever
returned.
"""

from __future__ import annotations

import json
import threading

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.repositories.postgres import (
    PostgresAdminAuditRepository,
    PostgresContextRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.postgres.test_context_sync import (  # noqa: F401 -- imported fixture + helpers
    _collide,
    add_colliding_domain,
    context_source,
    run_sync,
)
from tests.postgres.test_interactive_review import _get, _post

SOURCES = "/admin/api/v0/context/sources"


@pytest.fixture
def server_url(session_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    # The admin sync/validate handlers read the git cache dir from the environment;
    # point it at a per-test temp dir so a sync clones the local fixture repo there.
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.postgres
def test_source_crud_validate_sync_round_trip_and_no_secret(server_url, session_factory, tmp_path):
    repo = make_repository(tmp_path / "repo")

    # ADD.
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    assert status == 200, payload
    source_id = payload["source"]["source_id"]

    # VALIDATE (dry run) -- valid on a NEVER-SYNCED source, so it records NOTHING: no
    # snapshot AND no last_attempt_* change (a dry run must not claim a sync it did not
    # perform). Load-bearing (#446 adversary): asserting only current_snapshot is None
    # would let a future record_unchanged-for-every-valid-commit regression pass while
    # flipping last_attempt_status to 'unchanged' and pinning a commit -- so pin the whole
    # last_attempt_* tuple to its untouched, never-synced state.
    status, payload = _post(f"{server_url}{SOURCES}/validate", {"source_id": source_id})
    assert status == 200, payload
    assert payload["result"]["status"] == "valid" and payload["result"]["ok"] is True
    before = PostgresContextRepository(session_factory).get_source(source_id)
    assert before.current_snapshot is None  # the dry run recorded no snapshot
    assert before.last_attempt_status == "never_synced"  # NOT flipped to 'unchanged'
    assert before.last_attempt_at is None
    assert before.last_attempted_commit_sha is None
    assert before.last_error is None
    # The dry run changes NO source status, but the ACTION is still audited: validate
    # probes an external remote, so it is traceable like every other admin CONFIGURE
    # action (add/sync/enable/remove). Pin exactly one ('context_source.validate','ok')
    # row for this source -- intended behavior, ruling A (#446 round 3).
    validate_rows = [
        entry
        for entry in PostgresAdminAuditRepository(session_factory).list(workspace=ALL_WORKSPACES)
        if entry.target == source_id and entry.action == "context_source.validate"
    ]
    assert len(validate_rows) == 1 and validate_rows[0].result == "ok"

    # SYNC -- now a snapshot serves.
    status, payload = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})
    assert status == 200, payload
    assert payload["result"]["status"] == "synced"

    # LIST -- present, serving, and carrying NO secret field.
    status, payload = _get(f"{server_url}{SOURCES}")
    assert status == 200
    src = next(s for s in payload["sources"] if s["source_id"] == source_id)
    assert src["enabled"] is True and src["serving_commit"] and src["domain"] == "revenue"
    assert src["domain_conflict"] is False
    assert "token" not in json.dumps(payload) and "secret" not in json.dumps(payload)

    # DISABLE then ENABLE.
    status, payload = _post(
        f"{server_url}{SOURCES}/enable", {"source_id": source_id, "enabled": False}
    )
    assert status == 200 and payload["source"]["enabled"] is False
    status, payload = _post(
        f"{server_url}{SOURCES}/enable", {"source_id": source_id, "enabled": True}
    )
    assert status == 200 and payload["source"]["enabled"] is True

    # REMOVE a source that HOLDS governed history -> refused (disable-only, ADR 0012).
    status, payload = _post(f"{server_url}{SOURCES}/remove", {"source_id": source_id})
    assert status == 400 and "cannot be removed" in payload["error"]["message"]

    # REMOVE a never-synced pointer -> succeeds (no governed history destroyed).
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": "domains/none"}
    )
    empty_id = payload["source"]["source_id"]
    status, payload = _post(f"{server_url}{SOURCES}/remove", {"source_id": empty_id})
    assert status == 200 and payload["removed"] == empty_id
    status, payload = _get(f"{server_url}{SOURCES}")
    assert empty_id not in {s["source_id"] for s in payload["sources"]}


@pytest.mark.postgres
def test_a_disabled_source_is_excluded_from_serving(server_url, session_factory, tmp_path):
    repo = make_repository(tmp_path / "repo")
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    source_id = payload["source"]["source_id"]
    _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})

    directive = ContextDirective(domains=["revenue"], concepts=["recognized_revenue"])
    served = resolve_analytics_context(
        query="recognized revenue", directive=directive, session_factory=session_factory
    ).to_dict()
    assert served["resolution"]["status"] in ("governed", "mixed")

    # Disable through the admin endpoint -> the domain no longer serves.
    status, _ = _post(f"{server_url}{SOURCES}/enable", {"source_id": source_id, "enabled": False})
    assert status == 200
    after = resolve_analytics_context(
        query="recognized revenue", directive=directive, session_factory=session_factory
    ).to_dict()
    assert after["resolution"]["status"] == "no_match"
    assert after["context_authority"] is None


@pytest.mark.postgres
def test_the_domain_conflict_is_surfaced_in_the_admin_list(
    server_url,
    session_factory,
    tmp_path,
    context_source,  # noqa: F811
):
    """Two enabled sources on one domain -- the ambiguity the resolver refuses on --
    is SURFACED in the admin list, not silently merged."""
    source, second, _ = _collide(session_factory, tmp_path, context_source)

    status, payload = _get(f"{server_url}{SOURCES}")
    assert status == 200
    by_id = {s["source_id"]: s for s in payload["sources"]}
    assert by_id[source.id]["domain_conflict"] is True
    assert by_id[second.id]["domain_conflict"] is True
    assert second.id in by_id[source.id]["conflicting_source_ids"]
    assert source.id in by_id[second.id]["conflicting_source_ids"]


@pytest.mark.postgres
def test_enable_refuses_re_creating_a_domain_collision(
    server_url,
    session_factory,
    tmp_path,
    context_source,  # noqa: F811
):
    """Re-enabling a source into a domain another enabled source already claims is
    REFUSED through the admin endpoint, mirroring the CLI -- the estate is never
    silently made ambiguous."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    contexts.set_enabled(source.id, enabled=False)
    colliding = add_colliding_domain(repository, path_slug="revenue-copy")
    second = contexts.register_source(repository=str(repository), ref="main", path=colliding)
    assert run_sync(session_factory, tmp_path, second.id).status == "synced"

    # `source` is disabled; re-enabling it collides with `second` on 'revenue'.
    status, payload = _post(
        f"{server_url}{SOURCES}/enable", {"source_id": source.id, "enabled": True}
    )
    assert status == 400
    assert "already claimed by enabled source" in payload["error"]["message"]
    assert contexts.get_source(source.id).enabled is False  # unchanged


@pytest.mark.postgres
def test_validate_records_the_live_failure_so_the_card_matches_live(
    server_url, session_factory, tmp_path
):
    """hy-ppufd: a source that was validated/synced fine but whose remote is now
    unreachable must not keep a stale-green recorded status. A live Validate records
    the failure through the same status path sync uses, so the admin card
    (last_attempt_status/last_error) == what the live validation just found, and the
    error names the real cause -- not git's 'and the repository exists.' trailer."""
    # A pointer at a path that is not a git repository: the live fetch fails.
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(tmp_path / "not-a-repo"), "path": CONTEXT_PATH}
    )
    assert status == 200, payload
    source_id = payload["source"]["source_id"]

    status, payload = _post(f"{server_url}{SOURCES}/validate", {"source_id": source_id})
    assert status == 200, payload
    result = payload["result"]
    assert result["status"] == "invalid" and result["ok"] is False
    live_reason = "; ".join(result["reasons"])
    assert "Could not read from remote repository" in live_reason
    assert "and the repository exists." not in live_reason  # not the boilerplate trailer

    # RECORDED == LIVE: the source now reads 'failed' with the same cause, both in the
    # persisted row and on the admin card the operator sees.
    recorded = PostgresContextRepository(session_factory).get_source(source_id)
    assert recorded.last_attempt_status == "failed"
    assert "Could not read from remote repository" in recorded.last_error
    assert recorded.current_snapshot is None  # no governed snapshot written by a dry run

    status, listing = _get(f"{server_url}{SOURCES}")
    card = next(s for s in listing["sources"] if s["source_id"] == source_id)
    assert card["last_attempt_status"] == "failed"
    assert "Could not read from remote repository" in card["last_error"]


@pytest.mark.postgres
def test_validate_confirms_the_served_pin_as_unchanged_when_the_live_check_still_passes(
    server_url, session_factory, tmp_path
):
    """A source synced and still valid at the same commit: a live Validate records the
    attempt as 'unchanged' (recorded == live), leaving the serving snapshot untouched."""
    repo = make_repository(tmp_path / "repo")
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    source_id = payload["source"]["source_id"]
    _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})

    status, payload = _post(f"{server_url}{SOURCES}/validate", {"source_id": source_id})
    assert status == 200 and payload["result"]["status"] == "valid"
    recorded = PostgresContextRepository(session_factory).get_source(source_id)
    assert recorded.last_attempt_status == "unchanged"  # the live check confirmed the pin
    assert recorded.current_snapshot is not None  # the served snapshot is untouched


@pytest.mark.postgres
def test_a_failed_audit_append_rolls_back_the_validate_status_write(
    server_url, session_factory, tmp_path, monkeypatch
):
    """#446 round 2 (adversary): Validate's status write and its audit append are COUPLED
    in one transaction, so a failed audit rolls the status write back -- an authority-status
    change is never persisted unaudited. Load-bearing beyond the 500: after a forced audit
    failure the source's last_attempt_status/at/commit/error must be UNCHANGED, not the
    'failed' the live (now-unreachable) validate would otherwise have recorded."""
    import shutil

    from hyperset.repositories.postgres import PostgresAdminAuditRepository

    repo = make_repository(tmp_path / "repo")
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    source_id = payload["source"]["source_id"]
    # Sync once so there IS a prior recorded status to preserve.
    status, payload = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})
    assert status == 200 and payload["result"]["status"] == "synced"
    before = PostgresContextRepository(session_factory).get_source(source_id)
    assert before.last_attempt_status == "synced" and before.last_error is None

    # Make the remote unreachable so a live validate WOULD record a 'failed' attempt...
    shutil.rmtree(repo)

    def _boom(self, *a, **k):
        raise RuntimeError("audit store down")

    # ...then force the audit append to fail; the coupled transaction must roll the
    # record_failure write back.
    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _boom)
    status, payload = _post(f"{server_url}{SOURCES}/validate", {"source_id": source_id})
    assert status == 500
    assert "was not recorded to the admin audit trail" in payload["error"]["message"]

    after = PostgresContextRepository(session_factory).get_source(source_id)
    assert after.last_attempt_status == before.last_attempt_status  # NOT 'failed'
    assert after.last_error == before.last_error  # still None, not the fetch error
    assert after.last_attempt_at == before.last_attempt_at  # timestamp untouched
    assert after.last_attempted_commit_sha == before.last_attempted_commit_sha


@pytest.mark.postgres
def test_a_failed_audit_append_rolls_back_the_sync_snapshot_and_status(
    server_url, session_factory, tmp_path, monkeypatch
):
    """hy-oq1y4: sync's snapshot + last_attempt_* write and its audit are coupled in ONE
    transaction (mirroring the Validate fix #446), so a failed audit rolls BOTH back -- a
    governed snapshot is never persisted without an audit row. Load-bearing beyond the 500:
    after a forced audit failure the source's serving snapshot AND last_attempt_* are
    UNCHANGED, and no new snapshot was persisted for the new commit."""
    repo = make_repository(tmp_path / "repo")
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    source_id = payload["source"]["source_id"]
    contexts = PostgresContextRepository(session_factory)
    status, payload = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})
    assert status == 200 and payload["result"]["status"] == "synced"
    before = contexts.get_source(source_id)
    snapshot_before = before.current_snapshot.id
    snapshot_count_before = contexts.snapshot_count(source_id)  # == 1 after the first sync
    assert before.last_attempt_status == "synced"

    # A NEW commit, so a re-sync WOULD create a new snapshot and update last_attempt_*.
    (repo / CONTEXT_PATH / "context.md").write_text("later edit\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "-m", "later edit", cwd=repo)

    def _boom(self, *a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _boom)
    status, payload = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})
    assert status == 500
    assert "was not recorded to the admin audit trail" in payload["error"]["message"]

    after = contexts.get_source(source_id)
    # The core invariant this fix exists for: NO orphan governed snapshot row was
    # persisted for the new commit -- the whole write rolled back, not just the pointer.
    assert contexts.snapshot_count(source_id) == snapshot_count_before
    # The serving snapshot and the FULL last_attempt_* tuple (incl last_error) are unchanged.
    assert after.current_snapshot.id == snapshot_before  # no new snapshot serves
    assert after.last_attempt_status == before.last_attempt_status
    assert after.last_attempt_at == before.last_attempt_at
    assert after.last_attempted_commit_sha == before.last_attempted_commit_sha
    assert after.last_error == before.last_error


def _add_and_sync_twice(server_url, session_factory, tmp_path):
    """A source with TWO snapshots: commit A (original), then commit B that ADDS a
    `net_revenue` definition. Returns (source_id, commit_a, commit_b, repo)."""
    import yaml

    repo = make_repository(tmp_path / "repo")
    status, payload = _post(
        f"{server_url}{SOURCES}", {"repository": str(repo), "path": CONTEXT_PATH}
    )
    assert status == 200, payload
    source_id = payload["source"]["source_id"]
    commit_a = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})[1]["result"]["commit"]

    manifest = repo / CONTEXT_PATH / "manifest.yaml"
    doc = yaml.safe_load(manifest.read_text())
    doc["definitions"].append(
        {"term": "net_revenue", "statement": "Recognized revenue less refunds."}
    )
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "-m", "add net_revenue definition", cwd=repo)
    commit_b = _post(f"{server_url}{SOURCES}/sync", {"source_id": source_id})[1]["result"]["commit"]
    assert commit_a != commit_b
    return source_id, commit_a, commit_b, repo


@pytest.mark.postgres
def test_compare_two_serving_commits_shows_the_semantic_delta(
    server_url, session_factory, tmp_path
):
    """hy-bo5p: an admin compares two serving commits of a source and sees the added/changed/
    removed sections of the governed definition -- read-only, over two already-persisted
    snapshots (no Git read, no write)."""
    source_id, commit_a, commit_b, _ = _add_and_sync_twice(server_url, session_factory, tmp_path)

    status, payload = _post(
        f"{server_url}{SOURCES}/compare",
        {"source_id": source_id, "base_commit": commit_a, "target_commit": commit_b},
    )
    assert status == 200, payload
    comparison = payload["comparison"]
    assert comparison["base"]["commit_sha"] == commit_a
    assert comparison["target"]["commit_sha"] == commit_b
    assert comparison["identical"] is False  # the content hashes differ
    added_terms = {
        entry["term"] for entry in comparison["diff"]["sections"]["definitions"]["added"]
    }
    assert "net_revenue" in added_terms  # the added definition is surfaced as the delta

    # Comparing a commit to ITSELF is identical with an empty diff.
    status, payload = _post(
        f"{server_url}{SOURCES}/compare",
        {"source_id": source_id, "base_commit": commit_b, "target_commit": commit_b},
    )
    assert status == 200 and payload["comparison"]["identical"] is True
    assert payload["comparison"]["diff"]["sections"] == {}


@pytest.mark.postgres
def test_compare_fails_closed_on_a_commit_not_in_history(server_url, session_factory, tmp_path):
    """hy-bo5p: compare only reads commits THIS source snapshotted; an unknown commit is a
    400, not a silent empty diff."""
    source_id, commit_a, _, _ = _add_and_sync_twice(server_url, session_factory, tmp_path)
    status, payload = _post(
        f"{server_url}{SOURCES}/compare",
        {"source_id": source_id, "base_commit": commit_a, "target_commit": "0" * 40},
    )
    assert status == 400 and "no snapshot at commit" in payload["error"]["message"]
    assert "0" * 40 in payload["error"]["message"]


@pytest.mark.postgres
def test_rollback_re_pins_a_prior_commit_audits_and_writes_no_new_snapshot(
    server_url, session_factory, tmp_path
):
    """hy-bo5p: rolling a source back RE-PINS its serving snapshot to a prior commit it already
    produced. An INTEGRATION action (ADR 0012): the pin moves, the served bundle follows, NO
    new snapshot / governed row is written, and the action is audited."""
    source_id, commit_a, commit_b, _ = _add_and_sync_twice(server_url, session_factory, tmp_path)
    contexts = PostgresContextRepository(session_factory)
    assert contexts.snapshot_count(source_id) == 2
    assert contexts.get_source(source_id).current_snapshot.commit_sha == commit_b

    # At B the domain DECLARES net_revenue, so a request for it governs.
    directive = ContextDirective(domains=["revenue"], concepts=["net_revenue"])
    at_b = resolve_analytics_context(
        query="net revenue", directive=directive, session_factory=session_factory
    ).to_dict()
    assert at_b["resolution"]["status"] in ("governed", "mixed")

    # ROLL BACK to A.
    status, payload = _post(
        f"{server_url}{SOURCES}/rollback", {"source_id": source_id, "commit_sha": commit_a}
    )
    assert status == 200, payload
    assert payload["source"]["serving_commit"] == commit_a

    after = contexts.get_source(source_id)
    assert after.current_snapshot.commit_sha == commit_a  # the serving pin moved back
    # No authorship: an integration re-pin creates NO new snapshot (both priors are retained).
    assert contexts.snapshot_count(source_id) == 2

    # The served bundle now follows the pin: net_revenue (introduced at B) is gone again, so
    # the 'revenue' domain no longer declares it and the same request no longer governs.
    at_a = resolve_analytics_context(
        query="net revenue", directive=directive, session_factory=session_factory
    ).to_dict()
    assert at_a["resolution"]["status"] == "no_match"
    codes = {warning["code"] for warning in at_a["resolution"]["warnings"]}
    assert "domain_does_not_declare" in codes  # net_revenue is no longer a declared concept

    # Exactly one audit row records the rollback.
    rows = [
        entry
        for entry in PostgresAdminAuditRepository(session_factory).list(workspace=ALL_WORKSPACES)
        if entry.target == source_id and entry.action == "context_source.rollback"
    ]
    assert len(rows) == 1 and rows[0].result == "ok"


@pytest.mark.postgres
def test_rollback_fails_closed_on_a_commit_not_in_history(server_url, session_factory, tmp_path):
    """hy-bo5p: a source can only be rolled back to a commit it actually served; an unknown
    commit is a 400 and the serving pin is UNCHANGED."""
    source_id, _, commit_b, _ = _add_and_sync_twice(server_url, session_factory, tmp_path)
    contexts = PostgresContextRepository(session_factory)

    status, payload = _post(
        f"{server_url}{SOURCES}/rollback", {"source_id": source_id, "commit_sha": "0" * 40}
    )
    assert status == 400 and "no snapshot at commit" in payload["error"]["message"]
    assert contexts.get_source(source_id).current_snapshot.commit_sha == commit_b  # unchanged


@pytest.mark.postgres
def test_a_failed_audit_append_rolls_back_the_rollback_re_pin(
    server_url, session_factory, tmp_path, monkeypatch
):
    """hy-bo5p: the re-pin and its audit append are coupled in ONE transaction, so a failed
    audit rolls the re-pin back -- the serving snapshot is never moved without an audit row."""
    source_id, commit_a, commit_b, _ = _add_and_sync_twice(server_url, session_factory, tmp_path)
    contexts = PostgresContextRepository(session_factory)
    assert contexts.get_source(source_id).current_snapshot.commit_sha == commit_b

    def _boom(self, *a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _boom)
    status, payload = _post(
        f"{server_url}{SOURCES}/rollback", {"source_id": source_id, "commit_sha": commit_a}
    )
    assert status == 500
    assert "could not be re-pinned together with its audit record" in payload["error"]["message"]
    # The serving pin did NOT move -- the coupled transaction rolled the re-pin back.
    assert contexts.get_source(source_id).current_snapshot.commit_sha == commit_b
