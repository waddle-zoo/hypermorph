"""Tenant/workspace isolation on the admin surface + write-back routing (hq-t6nx, ADR-0037).

Real server, real Postgres. The authz gate is ON and `principal_from_bearer` is
stubbed so each bearer maps to a Principal in a named workspace with the `admin`
(configure) role. Proves fail-closed isolation: a caller in workspace A never sees
or manages workspace B's connections/targets/sources (flip-verified both ways), a
proposal routes only within its own workspace, a missing workspace claim resolves
to the single implicit 'default', and pre-existing rows (workspace_id defaulted to
'default') are visible exactly as before. Additive: no served contract moves.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from hyperset.candidates import service as candidate_service
from hyperset.context.evidence import ObservedEvidenceResolver
from hyperset.context.schema import REF_AMBIGUOUS, EvidenceRef
from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.repositories.errors import AmbiguousIdentityError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.security.authz import Principal
from hyperset.transport import http as http_module
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, make_repository
from tests.postgres.test_cli import _write_bundle_zip


@pytest.fixture
def server(session_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    # Keep this real-HTTP tenant test offline with an explicit injected test
    # double; served configuration itself remains OpenAI-only.
    monkeypatch.setattr(
        candidate_service,
        "configured_embedding_provider",
        lambda: DeterministicEmbeddingProvider(),
    )

    # A bearer of "ws:<name>" is an admin in workspace <name>; "ws:" (or no bearer)
    # carries NO workspace claim, so it fails closed to the 'default' workspace.
    def _principal_for(header):
        if not header:
            return None
        token = header.split(" ", 1)[-1]
        name = token[3:] if token.startswith("ws:") else ""
        return Principal(
            subject="u",
            issuer="https://issuer.example",
            roles=("admin",),
            workspace=name or "default",
        )

    monkeypatch.setattr(http_module, "principal_from_bearer", _principal_for)
    srv = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _req(url, *, ws, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    method = method or ("POST" if data is not None else "GET")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer ws:{ws}"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.postgres
def test_connections_are_isolated_per_workspace(server, session_factory, tmp_path):
    bundle = _write_bundle_zip(tmp_path)
    conn = f"{server}/admin/api/v0/connections"
    status, a = _req(
        conn,
        ws="alpha",
        payload={"connector_type": "superset", "display_name": "A", "config_ref": bundle},
    )
    assert status == 200, a
    status, b = _req(
        conn,
        ws="beta",
        payload={"connector_type": "superset", "display_name": "B", "config_ref": bundle},
    )
    assert status == 200, b
    a_id, b_id = a["connection"]["id"], b["connection"]["id"]

    # Each workspace sees ONLY its own connection (flip-verified both ways).
    _, list_a = _req(conn, ws="alpha")
    _, list_b = _req(conn, ws="beta")
    ids_a = {c["id"] for c in list_a["connections"]}
    ids_b = {c["id"] for c in list_b["connections"]}
    assert a_id in ids_a and b_id not in ids_a
    assert b_id in ids_b and a_id not in ids_b

    # A cross-workspace MANAGE is a 404 (non-disclosing), not a 200.
    status, _ = _req(f"{conn}/enable", ws="beta", payload={"id": a_id, "enabled": False})
    assert status == 404


@pytest.mark.postgres
def test_writeback_targets_are_isolated_per_workspace(server, session_factory):
    targets = f"{server}/admin/api/v0/review/writeback-targets"
    body = {"repository": "/srv/a", "base_ref": "main", "manifest_path": CONTEXT_PATH}
    status, _ = _req(targets, ws="alpha", payload={"routing_key": "revenue", **body})
    assert status == 200
    status, _ = _req(
        targets,
        ws="beta",
        payload={
            "routing_key": "revenue",
            "repository": "/srv/b",
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
        },
    )
    assert status == 200

    _, list_a = _req(targets, ws="alpha")
    _, list_b = _req(targets, ws="beta")
    repos_a = {t["repository"] for t in list_a["targets"]}
    repos_b = {t["repository"] for t in list_b["targets"]}
    assert "/srv/a" in repos_a and "/srv/b" not in repos_a
    assert "/srv/b" in repos_b and "/srv/a" not in repos_b

    # get_by_routing routes within a workspace only -- a proposal for 'revenue'
    # never crosses tenants.
    repo = PostgresWritebackConfigRepository(session_factory)
    assert repo.get_by_routing("revenue", workspace="alpha").repository == "/srv/a"
    assert repo.get_by_routing("revenue", workspace="beta").repository == "/srv/b"
    assert repo.get_by_routing("revenue", workspace="gamma") is None  # fail closed


@pytest.mark.postgres
def test_context_sources_are_isolated_per_workspace(server, session_factory, tmp_path):
    repo_a = make_repository(tmp_path / "a")
    repo_b = make_repository(tmp_path / "b")
    sources = f"{server}/admin/api/v0/context/sources"
    status, _ = _req(sources, ws="alpha", payload={"repository": str(repo_a), "path": CONTEXT_PATH})
    assert status == 200
    status, _ = _req(sources, ws="beta", payload={"repository": str(repo_b), "path": CONTEXT_PATH})
    assert status == 200

    _, list_a = _req(sources, ws="alpha")
    _, list_b = _req(sources, ws="beta")
    repos_a = {s["repository"] for s in list_a["sources"]}
    repos_b = {s["repository"] for s in list_b["sources"]}
    assert str(repo_a) in repos_a and str(repo_b) not in repos_a
    assert str(repo_b) in repos_b and str(repo_a) not in repos_b


@pytest.mark.postgres
def test_two_tenants_may_govern_the_same_domain_and_read_only_their_own(
    server, session_factory, tmp_path
):
    """Finding 2 + 3: estate placement and the served READ consumers (catalog,
    discover, expand) are workspace-scoped. Only 'alpha' governs the revenue domain;
    'beta' shares the estate but governs nothing, so its catalog/discover/expand are
    empty and its sync of the SAME domain never collides with alpha's (no cross-tenant
    claimant id/repo can even be reached, because the placement read is scoped)."""
    repo_a = make_repository(tmp_path / "a")
    repo_b = make_repository(tmp_path / "b")
    sources = f"{server}/admin/api/v0/context/sources"
    sync = f"{sources}/sync"

    _, add_a = _req(sources, ws="alpha", payload={"repository": str(repo_a), "path": CONTEXT_PATH})
    status, sa = _req(sync, ws="alpha", payload={"source_id": add_a["source"]["source_id"]})
    assert status == 200 and sa["result"]["status"] == "synced", sa

    # beta syncs its OWN revenue source: no collision, because alpha's claim is in
    # another tenant and the placement read never sees it.
    _, add_b = _req(sources, ws="beta", payload={"repository": str(repo_b), "path": CONTEXT_PATH})
    status, sb = _req(sync, ws="beta", payload={"source_id": add_b["source"]["source_id"]})
    assert status == 200 and sb["result"]["status"] == "synced", sb

    # A same-tenant second claimant DOES collide, and the reason names the sibling --
    # proving the scoped read still catches an in-tenant duplicate (not vacuously green).
    repo_a2 = make_repository(tmp_path / "a2")
    _, add_a2 = _req(
        sources, ws="alpha", payload={"repository": str(repo_a2), "path": CONTEXT_PATH}
    )
    status, sa2 = _req(sync, ws="alpha", payload={"source_id": add_a2["source"]["source_id"]})
    assert status == 200 and sa2["result"]["status"] == "failed", sa2
    assert any("already claimed" in r for r in sa2["result"]["reasons"]), sa2

    def _op(name, ws, params):
        return _req(f"{server}/v0/{name}", ws=ws, payload=params)

    # The served READ consumers are workspace-scoped. 'gamma' shares the estate (alpha
    # AND beta each govern a 'revenue' source) yet governs nothing itself, so its
    # catalog/discover/expand are empty -- proving the reads never leak a sibling
    # tenant's governed domain.
    _, cat_a = _op("list_context_catalog", "alpha", {})
    _, cat_g = _op("list_context_catalog", "gamma", {})
    assert "revenue" in {d["domain"] for d in cat_a["domains"]}
    assert cat_g["domains"] == []

    _, disc_a = _op("discover_analytics_context", "alpha", {"query": "revenue by region"})
    _, disc_g = _op("discover_analytics_context", "gamma", {"query": "revenue by region"})
    assert disc_a["candidates"] and not disc_g["candidates"]

    # expand starts only from a governed domain: alpha navigates from 'revenue'; gamma
    # does not govern it, so the start is unknown in gamma's tenant.
    expand_params = {"query": "revenue", "domain": "revenue", "concepts": ["recognized_revenue"]}
    _, exp_a = _op("expand_analytics_context", "alpha", expand_params)
    _, exp_g = _op("expand_analytics_context", "gamma", expand_params)
    assert exp_a["start"] == "revenue" and not any(
        w["code"] == "expansion_start_unknown" for w in exp_a["warnings"]
    )
    assert any(w["code"] == "expansion_start_unknown" for w in exp_g["warnings"]), exp_g


@pytest.mark.postgres
def test_the_hive_mind_root_walk_is_workspace_scoped_and_navigation_only(
    server, session_factory, tmp_path
):
    """hy-l93sc slice 1 end-to-end over the real DB + transport: a `from_root` walk enters
    the synthetic workspace root and returns the tenant's top-level governed domains with
    document POINTERS (never content), the root edge is catalog-derived (never `evidence:
    git`), the result is navigation (no `context_authority`), and a tenant that governs
    nothing gets an empty root -- no cross-tenant leak."""
    repo_a = make_repository(tmp_path / "a")
    sources = f"{server}/admin/api/v0/context/sources"
    _, add_a = _req(sources, ws="alpha", payload={"repository": str(repo_a), "path": CONTEXT_PATH})
    status, sa = _req(
        f"{sources}/sync", ws="alpha", payload={"source_id": add_a["source"]["source_id"]}
    )
    assert status == 200 and sa["result"]["status"] == "synced", sa

    def _op(ws, params):
        return _req(f"{server}/v0/expand_analytics_context", ws=ws, payload=params)

    _, served = _op("alpha", {"query": "what do we govern?", "from_root": True})
    assert served["result_kind"] == "navigation"
    assert "context_authority" not in served
    assert served["root"]["kind"] == "hive_mind_root"
    revenue = next(d for d in served["domains"] if d["domain"] == "revenue")
    assert revenue["available"] is True
    pointers = revenue["pointers"]
    assert pointers["context_doc"] and pointers["snapshot_id"] and pointers["commit_sha"]
    assert pointers["approved_sources"]  # refs, the pointers to resolve/grep next
    # POINTERS ONLY: no governed document text rides in the walk.
    assert "text" not in pointers
    root_edges = [e for e in served["edges"] if e["relation"] == "catalog_contains"]
    assert root_edges and all(e["evidence"] == "system" for e in root_edges)
    assert all(e["evidence"] != "git" for e in root_edges)

    # A tenant that governs nothing gets an empty root -- no sibling tenant's domain leaks.
    _, gamma = _op("gamma", {"query": "what do we govern?", "from_root": True})
    assert gamma["domains"] == []


@pytest.mark.postgres
def test_validate_scopes_its_internal_resolve_to_the_caller_tenant(
    server, session_factory, tmp_path
):
    """Round-2 blocker 1: VALIDATE must not build its validation bundle from a sibling
    tenant's source. Only 'alpha' governs revenue; 'gamma' governs nothing. RESOLVE is
    unscoped/deferred (ADR-0037), so it yields revenue's governed bundle_id. A plan
    carrying that id validates as alpha (its scoped resolve re-resolves the SAME governed
    revenue -> id matches -> checkable), but as gamma the scoped resolve finds no_match
    -> the id diverges -> `stale_bundle`. Were VALIDATE unscoped, gamma would resolve
    alpha's revenue and validate cleanly -- exactly the cross-tenant leak this refuses."""
    repo_a = make_repository(tmp_path / "a")
    sources = f"{server}/admin/api/v0/context/sources"
    _, add_a = _req(sources, ws="alpha", payload={"repository": str(repo_a), "path": CONTEXT_PATH})
    status, sa = _req(
        f"{sources}/sync", ws="alpha", payload={"source_id": add_a["source"]["source_id"]}
    )
    assert status == 200 and sa["result"]["status"] == "synced", sa

    def _op(name, ws, params):
        return _req(f"{server}/v0/{name}", ws=ws, payload=params)

    directive = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}
    _, res = _op("resolve_analytics_context", "alpha", {"query": "revenue", "directive": directive})
    assert res["resolution"]["status"] in ("governed", "mixed"), res
    plan = {
        "query": "revenue",
        "directive": directive,
        "bundle_id": res["bundle_id"],
        "source_refs": [],
        "grain": "order_date",
    }
    _, val_a = _op("validate_analytics_plan", "alpha", plan)
    _, val_g = _op("validate_analytics_plan", "gamma", plan)
    codes_a = {v["code"] for v in val_a["violations"]}
    codes_g = {v["code"] for v in val_g["violations"]}
    assert "stale_bundle" not in codes_a and "no_governed_context" not in codes_a, val_a
    assert "stale_bundle" in codes_g, val_g


@pytest.mark.postgres
def test_a_shared_source_pointer_resolves_per_tenant_and_fails_closed_unscoped(
    server, session_factory, tmp_path
):
    """Finding 4 + history scope: two tenants may register the SAME (repository, ref,
    path) pointer -- identity now includes the workspace. A workspace-SCOPED identity
    lookup resolves exactly one row per tenant. A workspace-LESS lookup over a shared
    pointer FAILS CLOSED with an explicit AmbiguousIdentityError -- it does NOT raise a
    raw MultipleResultsFound 500 and does NOT silently pick a tenant. The served
    history read is scoped too, so each tenant reads only its own source."""
    repo = make_repository(tmp_path / "shared")
    sources = f"{server}/admin/api/v0/context/sources"
    _, add_a = _req(sources, ws="alpha", payload={"repository": str(repo), "path": CONTEXT_PATH})
    _, add_b = _req(sources, ws="beta", payload={"repository": str(repo), "path": CONTEXT_PATH})
    a_id, b_id = add_a["source"]["source_id"], add_b["source"]["source_id"]
    assert a_id != b_id

    context = PostgresContextRepository(session_factory)
    ident = {"repository": str(repo), "ref": "main", "path": CONTEXT_PATH}
    # Scoped: each tenant resolves its OWN row unambiguously.
    assert context.get_source_by_identity(**ident, workspace="alpha").id == a_id
    assert context.get_source_by_identity(**ident, workspace="beta").id == b_id
    # Un-scoped over a shared pointer: fail CLOSED, explicit, no 500, no silent pick.
    with pytest.raises(AmbiguousIdentityError) as excinfo:
        context.get_source_by_identity(**ident)
    assert "alpha" in str(excinfo.value) and "beta" in str(excinfo.value)
    # A pointer only ONE tenant holds still resolves un-scoped (single-tenant back-compat).
    solo = make_repository(tmp_path / "solo")
    _, add_solo = _req(sources, ws="alpha", payload={"repository": str(solo), "path": CONTEXT_PATH})
    assert (
        context.get_source_by_identity(repository=str(solo), ref="main", path=CONTEXT_PATH).id
        == add_solo["source"]["source_id"]
    )

    # The served history route is workspace-scoped: each tenant reads its own source.
    history = f"{server}/admin/api/v0/context/history"
    query = f"repository={repo}&ref=main&path={CONTEXT_PATH}"
    _, hist_a = _req(f"{history}?{query}", ws="alpha")
    _, hist_b = _req(f"{history}?{query}", ws="beta")
    assert hist_a["source"]["id"] == a_id
    assert hist_b["source"]["id"] == b_id


def _seed_observed(session_factory, *, workspace, external_id):
    """A connection in `workspace` with one observed asset carrying `external_id`,
    behind a finished, successful sync (so the evidence resolver measures it)."""
    conn = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="superset", display_name=f"superset-{workspace}", workspace=workspace
    )
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(conn.id, mode="full")
    PostgresObservedAssetRepository(session_factory).upsert(
        connection_id=conn.id,
        external_id=external_id,
        asset_type="dataset",
        sync_run_id=run.id,
        raw_payload={"id": external_id},
    )
    syncs.finish_run(run.id, counters={"created": 1})
    return conn


@pytest.mark.postgres
def test_observed_evidence_resolves_within_a_tenant_not_across(session_factory):
    """Round-3 leak 1: the observed-evidence resolver is workspace fail-closed. Two
    tenants each observe a dataset with the SAME connector-native external id. A scoped
    resolve links ONLY that tenant's asset; the explicit ALL_WORKSPACES read sees both
    and refuses as AMBIGUOUS -- which is exactly the cross-tenant collision that, left
    unscoped, would let one tenant's context sync persist another tenant's observed
    asset."""
    shared = "urn:dataset:shared-id"
    alpha_conn = _seed_observed(session_factory, workspace="alpha", external_id=shared)
    beta_conn = _seed_observed(session_factory, workspace="beta", external_id=shared)
    ref = EvidenceRef(connector="superset", asset_type="dataset", external_id=shared)

    alpha = ObservedEvidenceResolver(session_factory, workspace="alpha").resolve([ref])
    assert [r["connection_id"] for r in alpha.resolved] == [alpha_conn.id]
    assert alpha.findings == []

    beta = ObservedEvidenceResolver(session_factory, workspace="beta").resolve([ref])
    assert [r["connection_id"] for r in beta.resolved] == [beta_conn.id]

    # The un-scoped system read sees BOTH tenants' assets -- the ambiguity a scoped
    # resolve exists to prevent. It resolves nothing and discloses the collision.
    everywhere = ObservedEvidenceResolver(session_factory, workspace=ALL_WORKSPACES).resolve([ref])
    assert everywhere.resolved == []
    assert [f["code"] for f in everywhere.findings] == [REF_AMBIGUOUS]


@pytest.mark.postgres
def test_catalog_observed_sources_are_scoped_to_the_tenant(server, session_factory):
    """Round-3 leak 2: the catalog's `observed` list is workspace-scoped. Each tenant's
    catalog carries only its OWN connection id/display/connector/count -- never a sibling
    tenant's."""
    alpha_conn = _seed_observed(session_factory, workspace="alpha", external_id="urn:a:1")
    beta_conn = _seed_observed(session_factory, workspace="beta", external_id="urn:b:1")

    def _observed_ids(ws):
        _, cat = _req(f"{server}/v0/list_context_catalog", ws=ws, payload={})
        return {entry["connection_id"] for entry in cat["observed"]}

    ids_a = _observed_ids("alpha")
    ids_b = _observed_ids("beta")
    assert alpha_conn.id in ids_a and beta_conn.id not in ids_a
    assert beta_conn.id in ids_b and alpha_conn.id not in ids_b


@pytest.mark.postgres
def test_connection_and_observed_enumeration_is_scoped_by_construction(session_factory):
    """Round-3 structural guarantee: the enumeration reads at the connection/observed
    repository layer have NO silent global default. Omitting the workspace is a
    TypeError, so a new consumer cannot enumerate across tenants by forgetting to
    scope -- global access must be the explicit ALL_WORKSPACES opt-in."""
    connections = PostgresConnectionRepository(session_factory)
    assets = PostgresObservedAssetRepository(session_factory)
    with pytest.raises(TypeError):
        connections.list()
    with pytest.raises(TypeError):
        assets.count_by_type()
    with pytest.raises(TypeError):
        assets.list_all(asset_type="dataset")
    # The explicit system opt-in is accepted.
    assert connections.list(workspace=ALL_WORKSPACES) == []
    assert assets.count_by_type(workspace=ALL_WORKSPACES) == []


@pytest.mark.postgres
def test_a_missing_workspace_claim_falls_back_to_default(server, session_factory, tmp_path):
    """A principal with no workspace claim acts in 'default' (never all-workspaces),
    and a pre-existing row (workspace_id defaulted to 'default' by the migration)
    is visible to it -- existing rows migrate to 'default' unchanged."""
    # A row seeded with NO explicit workspace takes the model/DB default 'default'.
    existing = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="superset", display_name="legacy", config_ref="/srv/legacy"
    )
    assert existing.workspace_id == "default"

    conn = f"{server}/admin/api/v0/connections"
    # An empty workspace claim -> 'default': the legacy row is visible.
    _, listed = _req(conn, ws="")
    assert existing.id in {c["id"] for c in listed["connections"]}
    # ...and a different tenant does NOT see it.
    _, other = _req(conn, ws="alpha")
    assert existing.id not in {c["id"] for c in other["connections"]}
