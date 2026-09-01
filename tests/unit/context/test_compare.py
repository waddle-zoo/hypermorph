"""Pure compare of two context snapshots (hy-bo5p, V1 gap Admin/4).

`compare_snapshots` is dict-vs-dict over two already-persisted snapshots: it reports the
content-hash `identical` flag and the section-level `diff` of the normalized definition,
reusing the review `diff_definition`. No git, no SQL, no store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from hyperset.context.compare import compare_snapshots


def _snapshot(*, commit, content_hash, definitions, domain="revenue"):
    return SimpleNamespace(
        commit_sha=commit,
        committed_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=content_hash,
        domain=domain,
        normalized={"domain": domain, "definitions": definitions},
    )


def test_compare_reports_the_added_definition_and_metadata():
    base = _snapshot(commit="aaa", content_hash="h1", definitions=[{"term": "recognized_revenue"}])
    target = _snapshot(
        commit="bbb",
        content_hash="h2",
        definitions=[{"term": "recognized_revenue"}, {"term": "net_revenue"}],
    )
    result = compare_snapshots(base, target)

    assert result["base"]["commit_sha"] == "aaa"
    assert result["target"]["commit_sha"] == "bbb"
    assert result["base"]["content_hash"] == "h1"
    assert result["base"]["committed_at"] == "2026-01-01T00:00:00+00:00"
    assert result["identical"] is False  # the content hashes differ
    added = {entry["term"] for entry in result["diff"]["sections"]["definitions"]["added"]}
    assert added == {"net_revenue"}
    assert result["diff"]["sections"]["definitions"]["removed"] == []


def test_equal_content_hash_is_identical_with_an_empty_diff():
    definitions = [{"term": "recognized_revenue"}]
    base = _snapshot(commit="aaa", content_hash="same", definitions=definitions)
    target = _snapshot(commit="bbb", content_hash="same", definitions=definitions)
    result = compare_snapshots(base, target)

    assert result["identical"] is True  # same content hash, even at different commits
    assert result["diff"]["sections"] == {}


def test_a_removed_definition_is_reported_as_removed():
    base = _snapshot(
        commit="aaa",
        content_hash="h1",
        definitions=[{"term": "recognized_revenue"}, {"term": "net_revenue"}],
    )
    target = _snapshot(
        commit="bbb", content_hash="h2", definitions=[{"term": "recognized_revenue"}]
    )
    result = compare_snapshots(base, target)

    removed = {entry["term"] for entry in result["diff"]["sections"]["definitions"]["removed"]}
    assert removed == {"net_revenue"}
    assert result["diff"]["sections"]["definitions"]["added"] == []
