"""The synthetic hive-mind ROOT walk (hy-l93sc slice 1, Overseer directive hq-wisp-1d9imq5).

DB-free: a fake repository injects the estate (`list_source_candidates` + `get_snapshot`),
so the acceptance criteria -- root links only enabled/current/ACL-visible top-level domains,
the walk is bounded, each reached domain carries document POINTERS (never content), a denied
domain is EXCLUDED-with-reason with its content NEVER fetched, disabled/unsynced domains are
disclosed not dropped, multi-repository identity is retained, and the root is NAVIGATION never
authority -- are pinned deterministically. The transport wiring is proved over the real DB in
tests/postgres.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperset.bundle.expansion import (
    CATALOG_CONTAINS,
    EVIDENCE_SYSTEM,
    ROOT_KIND,
    expand_from_root,
    root_node_id,
)


def _normalized(parent, *, marker):
    # `marker` is a stand-in for the domain's document CONTENT; a pointer must never carry it.
    return {
        "parent": parent,
        "documents": {"context_doc": {"path": "context.md", "text": marker}},
        "approved_sources": [{"ref": f"table:postgres:{marker}"}],
        "fields": [],
    }


class _FakeRepo:
    """Minimal ContextRepository seam: metadata for classification/authz, snapshots by id
    for content. Records which snapshot ids were fetched, so a test can prove a denied
    domain's content was never read (authorize-before-content)."""

    def __init__(self, candidates, snapshots):
        self._candidates = candidates
        self._snapshots = snapshots
        self.fetched: list[str] = []

    def list_source_candidates(self, *, workspace=None):
        return [c for c in self._candidates if workspace is None or c.workspace_id == workspace]

    def get_snapshot(self, snapshot_id):
        self.fetched.append(snapshot_id)
        return SimpleNamespace(normalized=self._snapshots[snapshot_id])


def _candidate(domain, *, parent, enabled=True, synced=True, repo="git@example/mono", ws="alpha"):
    snap_id = None if not synced else f"snap-{domain}"
    return SimpleNamespace(
        id=f"src-{domain}",
        repository=repo,
        enabled=enabled,
        workspace_id=ws,
        parent=parent,
        current_snapshot_id=snap_id,
        domain=domain if synced else None,
        commit_sha=f"commit-{domain}",
        content_hash=f"hash-{domain}",
        last_attempt_status="ok",
        last_attempt_at=None,
        synced_at=None,
        committed_at=None,
    ), (snap_id, parent)


def _estate():
    """revenue (root) -> finance (child); marketing (root); secret (root, ACL-denied);
    stale (root, unsynced); disabled (root, disabled). All in workspace alpha."""
    specs = {
        "revenue": {"parent": None, "repo": "git@example/revenue"},
        "finance": {"parent": "revenue", "repo": "git@example/finance"},
        "marketing": {"parent": None, "repo": "git@example/marketing"},
        "secret": {"parent": None, "repo": "git@example/secret"},
        "stale": {"parent": None, "synced": False},
        "disabled": {"parent": None, "enabled": False},
    }
    candidates = []
    snapshots = {}
    for domain, spec in specs.items():
        cand, (snap_id, parent) = _candidate(
            domain,
            parent=spec.get("parent"),
            enabled=spec.get("enabled", True),
            synced=spec.get("synced", True),
            repo=spec.get("repo", "git@example/mono"),
        )
        candidates.append(cand)
        if snap_id is not None:
            snapshots[snap_id] = _normalized(parent, marker=f"CONTENT-{domain}")
    return _FakeRepo(candidates, snapshots)


def _walk(repo, *, authorize_domain=None, **kw):
    return expand_from_root(
        query="what do we govern?",
        session_factory=None,
        workspace="alpha",
        repository=repo,
        authorize_domain=authorize_domain,
        **kw,
    ).to_dict()


def _available(served):
    return {d["domain"] for d in served["domains"] if d["available"]}


def _excluded(served):
    return {d["domain"]: d["exclusion"] for d in served["domains"] if not d["available"]}


def test_root_links_only_enabled_current_acl_visible_top_level_domains():
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret")
    # revenue + marketing are root children; finance is reached BELOW revenue.
    assert _available(served) == {"revenue", "marketing", "finance"}
    root_children = {e["to"] for e in served["edges"] if e["relation"] == CATALOG_CONTAINS}
    assert root_children == {"domain:revenue", "domain:marketing"}  # finance is not a root child


def test_disabled_unsynced_and_acl_denied_domains_are_disclosed_excluded_not_dropped():
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret")
    # An unsynced source has no current snapshot, so no domain is known: it is disclosed by
    # its source id, still excluded-with-reason (never silently dropped). A disabled source
    # kept its last snapshot's domain.
    assert _excluded(served) == {"secret": "acl", "src-stale": "unsynced", "disabled": "disabled"}
    codes = {w["code"] for w in served["warnings"]}
    assert "expansion_acl_excluded" in codes and "expansion_domain_unavailable" in codes


def test_acl_denied_domain_content_is_never_fetched_or_returned():
    repo = _estate()
    served = _walk(repo, authorize_domain=lambda d: d != "secret")
    # Authorize-BEFORE-content: the denied domain's snapshot is never fetched at all.
    assert "snap-secret" not in repo.fetched
    # And no byte of its content (or a pointer to it) rides in the served walk.
    import json

    blob = json.dumps(served)
    assert "CONTENT-secret" not in blob
    assert "src-secret" not in blob and "commit-secret" not in blob
    assert "secret" in _excluded(served)  # but its EXISTENCE-with-reason is disclosed


def test_each_reached_domain_carries_document_pointers_and_no_content():
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret")
    revenue = next(d for d in served["domains"] if d["domain"] == "revenue")
    pointers = revenue["pointers"]
    assert pointers["snapshot_id"] == "snap-revenue"
    assert pointers["commit_sha"] == "commit-revenue"
    assert pointers["context_doc"] == "context.md"
    assert pointers["approved_sources"] == ["table:postgres:CONTENT-revenue"]
    # POINTERS ONLY: the document text is never inlined.
    assert "text" not in pointers
    assert "CONTENT-revenue" not in "".join(str(v) for v in [pointers["context_doc"]])


def test_multi_repository_sources_retain_per_source_identity_in_pointers():
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret")
    by_domain = {d["domain"]: d["pointers"] for d in served["domains"] if d["available"]}
    assert by_domain["revenue"]["repository"] == "git@example/revenue"
    assert by_domain["finance"]["repository"] == "git@example/finance"
    assert by_domain["revenue"]["source_id"] == "src-revenue"
    assert by_domain["finance"]["source_id"] == "src-finance"


def test_the_walk_is_bounded_by_depth_and_discloses_the_drop():
    # max_hops=1 keeps the root's direct children (depth 1) and drops finance (depth 2).
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret", max_hops=1)
    assert _available(served) == {"revenue", "marketing"}
    assert "finance" not in _available(served)
    assert any(w["code"] == "expansion_bounded" for w in served["warnings"])


def test_the_walk_is_bounded_by_component_budget():
    served = _walk(_estate(), authorize_domain=lambda d: d != "secret", max_components=1)
    assert len(_available(served)) == 1
    assert any(w["code"] == "expansion_bounded" for w in served["warnings"])


def test_a_large_root_estate_bounds_unavailable_disclosures():
    repo = _estate()
    for index in range(100):
        candidate, (snap_id, _parent) = _candidate(
            f"disabled-{index:03d}", parent=None, enabled=False
        )
        repo._candidates.append(candidate)
        if snap_id is not None:
            repo._snapshots[snap_id] = _normalized(None, marker=f"CONTENT-disabled-{index:03d}")

    served = _walk(repo, authorize_domain=lambda d: d != "secret")
    excluded = [domain for domain in served["domains"] if not domain["available"]]
    assert len(excluded) == 50
    assert any(
        warning["code"] == "expansion_bounded"
        and "unavailable domain disclosure" in warning["message"]
        for warning in served["warnings"]
    )


def test_a_tiny_budget_does_not_reassemble_every_reachable_component():
    repo = _estate()
    for index in range(1_000):
        candidate, (snap_id, _parent) = _candidate(f"domain-{index:04d}", parent=None)
        repo._candidates.append(candidate)
        if snap_id is not None:
            repo._snapshots[snap_id] = _normalized(None, marker=f"CONTENT-domain-{index:04d}")

    with pytest.raises(ValueError, match="context_budget must be at least"):
        _walk(repo, context_budget=1)
    # The default component cap is 100. A logarithmic budget search loads at most the
    # initial 100 plus one binary-search pass per level, rather than 100 + 99 + ... + 1.
    assert len(repo.fetched) < 250


def test_the_root_is_navigation_never_authority():
    served = _walk(_estate())  # authz off: every domain visible
    assert served["result_kind"] == "navigation"
    for governed_key in ("context_authority", "instructions", "linked_evidence", "bundle_id"):
        assert governed_key not in served
    # The root node is generated and workspace-scoped; it is not governed authority.
    assert served["root"] == {"id": root_node_id("alpha"), "kind": ROOT_KIND, "workspace": "alpha"}
    # NO root edge is ever governed `evidence: "git"`; the catalog link is system-derived.
    for edge in served["edges"]:
        if edge["relation"] == CATALOG_CONTAINS:
            assert edge["evidence"] == EVIDENCE_SYSTEM
            assert edge["evidence"] != "git"


def test_authz_off_makes_every_domain_visible():
    served = _walk(_estate())  # no authorize_domain -> gate off
    assert "secret" in _available(served)


def test_a_credential_bearing_stored_repository_is_redacted_in_the_pointer():
    """A stored repository can be a credential-bearing Git URL. It must be userinfo-redacted
    at the pointer serve boundary -- the token never rides in the walk (hy-l93sc round 1,
    #511 bounce). Mutation-red: drop the redaction and the token leaks into the pointer."""
    cand, (snap_id, parent) = _candidate(
        "revenue", parent=None, repo="https://alice:ghp_SECRETTOKEN@github.com/acme/context"
    )
    import json

    repo = _FakeRepo([cand], {snap_id: _normalized(parent, marker="CONTENT-revenue")})
    served = _walk(repo)
    pointer = next(d["pointers"] for d in served["domains"] if d["domain"] == "revenue")
    assert pointer["repository"] == "https://github.com/acme/context"
    assert "ghp_SECRETTOKEN" not in json.dumps(served)
    assert "alice:" not in json.dumps(served)
