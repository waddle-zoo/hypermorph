"""Admin write-back-TARGET management + reachability probe (hq-095h, slice 2).

Real server, real Postgres, real local target repositories. Proves the admin
manage surface over the slice-1 target model -- list / add / update /
enable-disable / delete -- plus a TEST-TARGET PROBE that checks a target is
reachable, its auth reference resolves, and its base_ref exists WITHOUT creating
a branch or PR. Secrets stay server-side references (no plaintext token in any
response); a target that fails the probe or is disabled is excluded from routing
(fail closed, never silently used). ADR 0012 is unchanged -- nothing here
approves, merges, or advances governed context.
"""

from __future__ import annotations

import json
import threading

import pytest

from hyperset.repositories.postgres import PostgresReviewRepository
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.postgres.test_interactive_review import _get, _post, _secret_key
from tests.postgres.test_writeback_targets import _payload_for, _proposal_branches
from tests.review_api import PROPOSE_REVIEW_TO_GIT_PATH

TARGETS = "/admin/api/v0/review/writeback-targets"
CONFIG = "/admin/api/v0/review/writeback-config"


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


def _propose(server_url, session_factory, domain, idem):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key=idem, proposal_payload=_payload_for(domain)
    )
    return _post(f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id})


@pytest.mark.postgres
def test_target_crud_round_trip_and_no_plaintext_secret(
    server_url, session_factory, tmp_path, monkeypatch
):
    """Add -> list -> update -> disable -> delete a keyed target through the admin
    surface, and prove no pasted token value ever appears in a response."""
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _secret_key())
    repo_rev = make_repository(tmp_path / "rev")

    # ADD a keyed target with an ENCRYPTED pasted token -- the strongest
    # no-plaintext check (the raw value is only ever in the request).
    secret = "ghp_super_secret_value_123"
    status, payload = _post(
        f"{server_url}{TARGETS}",
        {
            "routing_key": "revenue",
            "repository": str(repo_rev),
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
            "token_source": "encrypted",
            "token": secret,
        },
    )
    assert status == 200, payload
    target = payload["target"]
    target_id = target["id"]
    assert target["routing_key"] == "revenue"
    assert target["enabled"] is True
    assert target["is_default"] is False
    assert target["token_set"] is True
    # The pasted token never comes back -- not here, not anywhere in the response.
    assert secret not in json.dumps(payload)

    # LIST shows it, still with no secret value.
    status, payload = _get(f"{server_url}{TARGETS}")
    assert status == 200
    ids = {t["id"]: t for t in payload["targets"]}
    assert target_id in ids
    assert secret not in json.dumps(payload)
    assert "token_ciphertext" not in json.dumps(payload)

    # UPDATE (same routing_key) changes config without duplicating the row, and
    # re-saving with the token box blank keeps the stored ciphertext.
    status, payload = _post(
        f"{server_url}{TARGETS}",
        {
            "routing_key": "revenue",
            "repository": str(repo_rev),
            "base_ref": "release",
            "manifest_path": CONTEXT_PATH,
            "token_source": "encrypted",
        },
    )
    assert status == 200
    assert payload["target"]["base_ref"] == "release"
    assert payload["target"]["token_set"] is True  # ciphertext preserved on blank
    status, payload = _get(f"{server_url}{TARGETS}")
    revenue = [t for t in payload["targets"] if t["routing_key"] == "revenue"]
    assert len(revenue) == 1  # updated in place, not duplicated

    # DISABLE, then DELETE.
    status, payload = _post(f"{server_url}{TARGETS}/enable", {"id": target_id, "enabled": False})
    assert status == 200
    assert payload["target"]["enabled"] is False
    status, payload = _post(f"{server_url}{TARGETS}/delete", {"id": target_id})
    assert status == 200
    assert payload["deleted"] == target_id
    status, payload = _get(f"{server_url}{TARGETS}")
    assert target_id not in {t["id"] for t in payload["targets"]}


@pytest.mark.postgres
def test_the_default_target_cannot_be_deleted_here(server_url, session_factory, tmp_path):
    """Deleting the default/catch-all target would silently remove routing's
    fallback, so the manage surface refuses it (disable instead)."""
    repo_def = make_repository(tmp_path / "def")
    # Create the default target via the config path.
    status, _ = _post(
        f"{server_url}{CONFIG}",
        {"repository": str(repo_def), "base_ref": "main", "manifest_path": CONTEXT_PATH},
    )
    assert status == 200
    default = next(t for t in _get(f"{server_url}{TARGETS}")[1]["targets"] if t["is_default"])
    status, payload = _post(f"{server_url}{TARGETS}/delete", {"id": default["id"]})
    assert status == 400
    assert "default write-back target cannot be deleted" in payload["error"]["message"]


@pytest.mark.postgres
def test_probe_reports_ready_degraded_blocked_and_opens_no_pr(
    server_url, session_factory, tmp_path
):
    """The probe reads reachability with ls-remote and opens NO branch or PR:
    ready when base_ref exists, degraded when it does not, blocked when the repo
    is unreachable."""
    repo = make_repository(tmp_path / "r")
    base_before = git("rev-parse", "main", cwd=repo)

    def _add(routing_key, repository, base_ref):
        status, payload = _post(
            f"{server_url}{TARGETS}",
            {
                "routing_key": routing_key,
                "repository": repository,
                "base_ref": base_ref,
                "manifest_path": CONTEXT_PATH,
            },
        )
        assert status == 200, payload
        return payload["target"]["id"]

    # READY: reachable local repo, base_ref exists.
    ready_id = _add("revenue", str(repo), "main")
    status, payload = _post(f"{server_url}{TARGETS}/test", {"id": ready_id})
    assert status == 200, payload
    assert payload["probe"]["status"] == "ready"
    # DEGRADED: reachable, but base_ref does not exist.
    degraded_id = _add("marketing", str(repo), "does-not-exist")
    status, payload = _post(f"{server_url}{TARGETS}/test", {"id": degraded_id})
    assert payload["probe"]["status"] == "degraded"
    assert "does-not-exist" in payload["probe"]["reason"]
    # BLOCKED: unreachable repository path.
    blocked_id = _add("finance", str(tmp_path / "nope" / "missing-repo"), "main")
    status, payload = _post(f"{server_url}{TARGETS}/test", {"id": blocked_id})
    assert payload["probe"]["status"] == "blocked"
    assert payload["probe"]["recovery"]

    # The probe created NOTHING: no proposal branch, base ref unmoved.
    assert _proposal_branches(repo) == ""
    assert git("rev-parse", "main", cwd=repo) == base_before
    # The result is persisted (a short status string, never a secret).
    ready = next(t for t in _get(f"{server_url}{TARGETS}")[1]["targets"] if t["id"] == ready_id)
    assert ready["test_result"] == "ready"


@pytest.mark.postgres
def test_a_url_target_with_an_unresolvable_auth_ref_probes_blocked(
    server_url, session_factory, monkeypatch
):
    """A URL target whose env_ref token is absent fails closed to blocked BEFORE
    any git call, and leaks no secret."""
    monkeypatch.delenv("HYPERSET_WB_TOKEN_MISSING", raising=False)
    status, payload = _post(
        f"{server_url}{TARGETS}",
        {
            "routing_key": "revenue",
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
            "token_source": "env_ref",
            "token_ref": "HYPERSET_WB_TOKEN_MISSING",
        },
    )
    assert status == 200, payload
    target_id = payload["target"]["id"]
    status, payload = _post(f"{server_url}{TARGETS}/test", {"id": target_id})
    assert payload["probe"]["status"] == "blocked"
    assert "server-side token" in payload["probe"]["reason"]


@pytest.mark.postgres
def test_a_disabled_target_is_excluded_from_routing(server_url, session_factory, tmp_path):
    """A disabled keyed target is never used: a proposal for its domain falls back
    to the default (fail closed, never silently routed to the disabled target)."""
    repo_rev = make_repository(tmp_path / "rev")
    repo_def = make_repository(tmp_path / "def")
    # A keyed 'revenue' target and a default catch-all.
    status, payload = _post(
        f"{server_url}{TARGETS}",
        {
            "routing_key": "revenue",
            "repository": str(repo_rev),
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
        },
    )
    assert status == 200
    revenue_id = payload["target"]["id"]
    _post(
        f"{server_url}{CONFIG}",
        {"repository": str(repo_def), "base_ref": "main", "manifest_path": CONTEXT_PATH},
    )

    # DISABLE the revenue target.
    status, _ = _post(f"{server_url}{TARGETS}/enable", {"id": revenue_id, "enabled": False})
    assert status == 200

    # A revenue proposal now routes to the DEFAULT, not the disabled keyed target.
    status, payload = _propose(server_url, session_factory, "revenue", "wb:disabled")
    assert status == 200, payload
    assert _proposal_branches(repo_def) != ""
    assert _proposal_branches(repo_rev) == ""


@pytest.mark.postgres
def test_a_failed_audit_append_rolls_back_the_probe_result(
    server_url, session_factory, monkeypatch
):
    """Persisting a probe result is an admin mutation, so it must not land without
    an audit row (hq-095h round 2, the #421 invariant). With the audit append
    forced to fail inside the coupled transaction, the probe answers 500 and the
    target's test_result is UNCHANGED -- the write rolled back with the append."""
    from hyperset.repositories.postgres import (
        PostgresAdminAuditRepository,
        PostgresWritebackConfigRepository,
    )

    # A URL target with an absent env_ref token: the probe takes the fail-closed
    # 'blocked' path, which still persists test_result -- so it must be audited too.
    monkeypatch.delenv("HYPERSET_WB_ROLLBACK", raising=False)
    status, payload = _post(
        f"{server_url}{TARGETS}",
        {
            "routing_key": "revenue",
            "repository": "https://github.com/acme/context",
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
            "token_source": "env_ref",
            "token_ref": "HYPERSET_WB_ROLLBACK",
        },
    )
    assert status == 200
    target_id = payload["target"]["id"]
    assert payload["target"]["test_result"] is None

    def _boom(*_args, **_kwargs):
        raise RuntimeError("audit down")  # stands in for a failed audit append

    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _boom)

    status, payload = _post(f"{server_url}{TARGETS}/test", {"id": target_id})
    assert status == 500
    assert "could not be saved together with its audit record" in payload["error"]["message"]

    # The test_result write rolled back with the failed append -- read it straight
    # from the datastore (not the HTTP list, whose audit stub is still patched):
    # the probe left NO persisted mutation, so nothing landed unaudited.
    record = PostgresWritebackConfigRepository(session_factory).get_by_id(target_id)
    assert record is not None
    assert record.test_result is None
