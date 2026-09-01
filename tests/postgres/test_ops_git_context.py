"""The operator view's Git-context surface over a real store (hy-3yri, hy-gh-72 S2).

Read-only: per source, the pinned commit and the last sync attempt's health. The
pin and the attempt are reported SEPARATELY -- a failed attempt leaves the last
good snapshot serving -- so the tests below assert both halves and the case where
they disagree.
"""

from __future__ import annotations

from hyperset.ops.status import read_git_context
from hyperset.repositories.postgres import PostgresContextRepository


def _source(session_factory, *, path="domains/revenue", name="Revenue"):
    return (
        PostgresContextRepository(session_factory)
        .register_source(repository="/tmp/repo", ref="main", path=path, display_name=name)
        .id
    )


def _pin(session_factory, source_id, *, commit="c0ffee", domain="revenue"):
    PostgresContextRepository(session_factory).record_snapshot(
        source_id=source_id,
        commit_sha=commit,
        committed_at=None,
        domain=domain,
        title="Revenue context",
        files={"manifest.yaml": "schema_version: 1\n"},
        normalized={"domain": domain},
    )


def _git_for(session_factory, source_id):
    return next(s for s in read_git_context(session_factory) if s.source_id == source_id)


def test_a_pinned_source_surfaces_its_commit_and_content_hash(session_factory):
    source_id = _source(session_factory)
    _pin(session_factory, source_id, commit="abc123")

    ctx = _git_for(session_factory, source_id)
    assert ctx.pinned is True
    assert ctx.commit_sha == "abc123"
    assert ctx.snapshot_id is not None
    assert ctx.content_hash  # a real content hash, not empty
    assert ctx.domain == "revenue"
    assert ctx.title == "Revenue context"
    assert ctx.last_attempt_status == "synced"


def test_a_source_never_synced_reads_as_unpinned(session_factory):
    source_id = _source(session_factory, path="domains/unsynced", name="Unsynced")

    ctx = _git_for(session_factory, source_id)
    assert ctx.pinned is False
    assert ctx.commit_sha is None
    assert ctx.snapshot_id is None
    assert ctx.content_hash is None
    assert ctx.last_attempt_status == "never_synced"


def test_a_failed_attempt_is_surfaced_with_its_error(session_factory):
    source_id = _source(session_factory, path="domains/broken", name="Broken")
    PostgresContextRepository(session_factory).record_failure(
        source_id, error="repository not reachable", commit_sha="deadbeef"
    )

    ctx = _git_for(session_factory, source_id)
    assert ctx.pinned is False  # never pinned successfully
    assert ctx.last_attempt_status == "failed"
    assert ctx.last_error == "repository not reachable"
    assert ctx.last_attempted_commit_sha == "deadbeef"


def test_a_failed_attempt_after_a_pin_keeps_the_pin(session_factory):
    # The two halves are separate: a failed re-sync leaves the last good snapshot
    # serving. The operator must see BOTH -- pin current, last attempt failed.
    source_id = _source(session_factory, path="domains/drifted", name="Drifted")
    _pin(session_factory, source_id, commit="good111")
    PostgresContextRepository(session_factory).record_failure(
        source_id, error="parse error at newer commit", commit_sha="bad222"
    )

    ctx = _git_for(session_factory, source_id)
    assert ctx.pinned is True
    assert ctx.commit_sha == "good111"  # the pin did not move
    assert ctx.last_attempt_status == "failed"
    assert ctx.last_error == "parse error at newer commit"
    assert ctx.last_attempted_commit_sha == "bad222"


def test_every_registered_source_appears(session_factory):
    a = _source(session_factory, path="domains/a", name="A")
    b = _source(session_factory, path="domains/b", name="B")
    ids = {s.source_id for s in read_git_context(session_factory)}
    assert {a, b} <= ids


def test_read_git_context_empty_when_no_sources(session_factory):
    assert read_git_context(session_factory) == []
