"""Multiple Git read-context sources in one workspace (hq-hnrf slice 1).

Real Postgres + real git repositories. Enterprise deployments read governed context
from MORE THAN ONE repository/source path at once, so this pins the shipped read-side
multi-source behavior as an end-to-end contract:

- more than one configured source resolves independently;
- resolution ROUTES by domain to the OWNING source and carries THAT source's
  provenance (repository, ref, path, commit, snapshot id) -- no assumed single global
  repository;
- a cross-source domain CONFLICT (two enabled sources claim one domain) is refused
  EXPLICITLY and fails safe -- `domain_ambiguous`, no bundle authority, no silent
  winner or merge (hy-gh-282);
- a DISABLED source is excluded from resolution.

The resolver already lists every enabled snapshotted source, selects per domain, and
refuses `domain_ambiguous`; the mayor's phase-2 map found no core read-side refactor
needed. This file is the acceptance bar for that behavior across two real sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.bundle.schema import DOMAIN_AMBIGUOUS
from hyperset.context.sync import sync_git_context
from hyperset.repositories.postgres import PostgresContextRepository
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository

QUESTION = "Which source and rules govern this domain?"
CONCEPTS = ["recognized_revenue"]  # the term the revenue fixture declares


def _repo_with_domain(root: Path, domain: str) -> Path:
    """A real repo holding the revenue fixture, but relabelled to `domain` so two
    independent sources declare DIFFERENT governed domains (or, when the same string
    is passed twice, the SAME domain -- the conflict case)."""
    repo = make_repository(root)
    if domain != "revenue":  # the fixture already ships domain: revenue -- no-op edit otherwise
        manifest = repo / CONTEXT_PATH / "manifest.yaml"
        manifest.write_text(
            manifest.read_text().replace("domain: revenue", f"domain: {domain}"), encoding="utf-8"
        )
        git("commit", "--quiet", "-am", f"relabel domain to {domain}", cwd=repo)
    return repo


def _add_and_sync(session_factory, cache_dir: Path, repo: Path):
    """Register a source for `repo` and sync it. Returns (source_id, ContextSyncResult)."""
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=cache_dir
    )
    return source.id, result


def _resolve(session_factory, domain: str):
    return resolve_analytics_context(
        query=QUESTION,
        directive=ContextDirective(domains=[domain], concepts=CONCEPTS),
        session_factory=session_factory,
    )


@pytest.mark.postgres
def test_two_sources_route_by_domain_with_per_source_provenance(session_factory, tmp_path):
    cache = tmp_path / "cache"
    repo_a = _repo_with_domain(tmp_path / "a", "revenue")
    repo_b = _repo_with_domain(tmp_path / "b", "marketing")
    a_id, a_sync = _add_and_sync(session_factory, cache, repo_a)
    b_id, b_sync = _add_and_sync(session_factory, cache, repo_b)
    assert a_sync.status == "synced" and b_sync.status == "synced"
    assert a_id != b_id

    # The 'revenue' domain routes to source A and carries A's exact provenance.
    revenue = _resolve(session_factory, "revenue")
    assert revenue.status == "governed"
    assert revenue.context_authority["repository"] == str(repo_a)
    assert revenue.context_authority["ref"] == "main"
    assert revenue.context_authority["path"] == CONTEXT_PATH
    assert revenue.context_authority["commit_sha"] == a_sync.commit_sha
    assert revenue.context_authority["context_snapshot_id"] == a_sync.snapshot_id

    # The 'marketing' domain routes to source B and carries B's provenance -- a
    # DIFFERENT repository and commit, proving there is no single global repository.
    marketing = _resolve(session_factory, "marketing")
    assert marketing.status == "governed"
    assert marketing.context_authority["repository"] == str(repo_b)
    assert marketing.context_authority["commit_sha"] == b_sync.commit_sha
    assert marketing.context_authority["context_snapshot_id"] == b_sync.snapshot_id
    assert marketing.context_authority["commit_sha"] != revenue.context_authority["commit_sha"]


@pytest.mark.postgres
def test_a_directive_over_two_sources_aggregates_each_with_its_own_provenance(
    session_factory, tmp_path
):
    """One directive naming BOTH domains AGGREGATES them into a `domains[]` envelope --
    each entry resolved independently from its OWN source, carrying that source's
    provenance. No commit's guidance is merged into another's: the top-level authority
    is null and each entry names a different repository and commit."""
    cache = tmp_path / "cache"
    repo_a = _repo_with_domain(tmp_path / "a", "revenue")
    repo_b = _repo_with_domain(tmp_path / "b", "marketing")
    _, a_sync = _add_and_sync(session_factory, cache, repo_a)
    _, b_sync = _add_and_sync(session_factory, cache, repo_b)

    bundle = resolve_analytics_context(
        query=QUESTION,
        directive=ContextDirective(domains=["revenue", "marketing"], concepts=CONCEPTS),
        session_factory=session_factory,
    )
    assert bundle.status in ("governed", "mixed")
    # A multi-domain answer carries no single top-level authority (nothing merged).
    assert bundle.context_authority is None
    assert len(bundle.domains) == 2
    by_repo = {entry["context_authority"]["repository"]: entry for entry in bundle.domains}
    assert set(by_repo) == {str(repo_a), str(repo_b)}
    assert by_repo[str(repo_a)]["context_authority"]["commit_sha"] == a_sync.commit_sha
    assert by_repo[str(repo_b)]["context_authority"]["commit_sha"] == b_sync.commit_sha


@pytest.mark.postgres
def test_a_cross_source_domain_conflict_is_refused_not_silently_merged(session_factory, tmp_path):
    """Two enabled sources both claiming 'revenue' -> resolve refuses `domain_ambiguous`,
    naming BOTH claimants, with NO bundle authority. Constructed via the supported API
    (the sync-time collision check only bars a NEW claim while the incumbent is enabled,
    so disable A, sync B, re-enable A reaches the two-live-claimant state an operator can
    create); the resolver's fail-safe is what must catch it."""
    cache = tmp_path / "cache"
    repo_a = _repo_with_domain(tmp_path / "a", "revenue")
    repo_b = _repo_with_domain(tmp_path / "b", "revenue")
    context = PostgresContextRepository(session_factory)

    a_id, a_sync = _add_and_sync(session_factory, cache, repo_a)
    assert a_sync.status == "synced"
    # While A is enabled, B's sync is refused as a collision (fail-safe #1: no second
    # authority is admitted silently) -- the reason names A.
    b_source = context.register_source(repository=str(repo_b), ref="main", path=CONTEXT_PATH)
    blocked = sync_git_context(
        source_id=b_source.id, session_factory=session_factory, cache_dir=cache
    )
    assert blocked.status == "failed"
    assert any("already claimed" in reason for reason in blocked.reasons), blocked.reasons

    # Force the two-live-claimant state: disable A, sync B (now allowed), re-enable A.
    context.set_enabled(a_id, enabled=False)
    b_sync = sync_git_context(
        source_id=b_source.id, session_factory=session_factory, cache_dir=cache
    )
    assert b_sync.status == "synced"
    context.set_enabled(a_id, enabled=True)

    # Resolve fails safe: an explicit domain_ambiguous refusal, no authority, no winner.
    bundle = _resolve(session_factory, "revenue")
    assert bundle.status == "no_match"
    assert bundle.context_authority is None
    codes = {w["code"] for w in bundle.resolution["warnings"]}
    assert DOMAIN_AMBIGUOUS in codes, bundle.resolution["warnings"]
    message = next(
        w["message"] for w in bundle.resolution["warnings"] if w["code"] == DOMAIN_AMBIGUOUS
    )
    assert a_id in message and b_source.id in message  # both claimants named
    assert str(repo_a) in message and str(repo_b) in message


@pytest.mark.postgres
def test_a_disabled_source_is_excluded_from_resolution(session_factory, tmp_path):
    cache = tmp_path / "cache"
    repo_a = _repo_with_domain(tmp_path / "a", "revenue")
    a_id, a_sync = _add_and_sync(session_factory, cache, repo_a)
    assert _resolve(session_factory, "revenue").status == "governed"

    PostgresContextRepository(session_factory).set_enabled(a_id, enabled=False)
    disabled = _resolve(session_factory, "revenue")
    assert disabled.status == "no_match"
    assert disabled.context_authority is None
