"""The linked multi-domain playground scenario composes a contains graph
(#230 slice 8, hy-2pqi).

subscription_revenue is a governed SUBDOMAIN of revenue (`parent: revenue`). A
question that spans both resolves them together and the composed bundle relates
them with the governed `contains` edge (evidence: git) in `composition.graph`,
while each domain keeps its own authority in `domains[]`. This exercises the
SHIPPED playground example manifests, not a synthetic fixture, so a demo estate
that stopped composing would fail here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.context.sync import sync_git_context
from hyperset.repositories.postgres import PostgresContextRepository
from tests.integration.test_git_context_source import git

EXAMPLES = Path(__file__).resolve().parents[2] / "playground" / "examples"


def _playground_estate(tmp_path: Path, domains: list[str]) -> Path:
    """A git repo holding the named playground example estates, each at
    `domains/<estate>/`, exactly as `bootstrap_contexts` would lay them out."""
    repository = tmp_path / "playground-repo"
    for domain in domains:
        destination = repository / "domains" / domain
        destination.mkdir(parents=True)
        for path in sorted((EXAMPLES / domain).iterdir()):
            if path.is_file():
                shutil.copy(path, destination / path.name)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "playground examples", cwd=repository)
    return repository


def _sync_all(session_factory, tmp_path: Path, repository: Path, domains: list[str]) -> None:
    contexts = PostgresContextRepository(session_factory)
    # Parent before child: subscription_revenue declares `parent: revenue`, and the
    # forest is validated whole-estate at sync, so revenue must be known first.
    for domain in domains:
        source = contexts.register_source(
            repository=str(repository), ref="main", path=f"domains/{domain}"
        )
        result = sync_git_context(
            source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
        )
        assert result.status == "synced", result.reasons


@pytest.mark.postgres
def test_the_linked_playground_examples_compose_a_contains_graph(session_factory, tmp_path):
    repository = _playground_estate(tmp_path, ["revenue", "subscription_revenue"])
    _sync_all(session_factory, tmp_path, repository, ["revenue", "subscription_revenue"])

    payload = resolve_analytics_context(
        query="recognized revenue and its recurring subscription component",
        directive=ContextDirective(
            domains=["revenue", "subscription_revenue"],
            concepts=["recognized_revenue", "recurring_revenue"],
        ),
        session_factory=session_factory,
    ).to_dict()

    graph = payload["composition"]["graph"]
    assert {node["id"] for node in graph["nodes"]} == {
        "domain:revenue",
        "domain:subscription_revenue",
    }
    assert all(node["kind"] == "domain" for node in graph["nodes"])
    # The genuine governed containment: revenue CONTAINS its subscription subdomain.
    assert graph["edges"] == [
        {
            "from": "domain:revenue",
            "to": "domain:subscription_revenue",
            "relation": "contains",
            "evidence": "git",
        }
    ]
    # The flat envelope stays empty and per-domain authority is independent (slice 3/5).
    assert payload["domain_graph"] == {"nodes": [], "edges": []}
    assert payload["context_authority"] is None
    assert {entry["context_authority"]["path"] for entry in payload["domains"]} == {
        "domains/revenue",
        "domains/subscription_revenue",
    }


@pytest.mark.postgres
def test_the_subscription_subdomain_resolves_governed_on_its_own(session_factory, tmp_path):
    # The child is a real governed domain: resolved alone it is governed (single
    # domain, no composition), so the link is a relationship, not a dependency.
    repository = _playground_estate(tmp_path, ["revenue", "subscription_revenue"])
    _sync_all(session_factory, tmp_path, repository, ["revenue", "subscription_revenue"])

    bundle = resolve_analytics_context(
        query="recurring subscription revenue by plan",
        directive=ContextDirective(
            domains=["subscription_revenue"], concepts=["recurring_revenue"]
        ),
        session_factory=session_factory,
    )
    assert bundle.status == "governed"
    assert bundle.composition is None
    assert "composition" not in bundle.to_dict()
