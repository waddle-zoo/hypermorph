"""Compare two serving-commit snapshots of a context source (hy-bo5p, V1 gap Admin/4).

An operator deciding whether to roll a source back needs to see WHAT CHANGED between two
serving commits. Each snapshot carries the deterministic `normalized` projection of the Git
content, so this diffs the two by the SAME entry identity the review diff uses (definitions by
term, sources by ref, fields by name), plus the commit metadata and an `identical` flag from
the content hash. Pure and read-only: it reads already-persisted snapshots, writes nothing.
"""

from __future__ import annotations

from hyperset.review.meaning_diff import diff_definition


def _meta(snapshot) -> dict:
    return {
        "commit_sha": snapshot.commit_sha,
        "committed_at": snapshot.committed_at.isoformat() if snapshot.committed_at else None,
        "content_hash": snapshot.content_hash,
        "domain": snapshot.domain,
    }


def compare_snapshots(base, target) -> dict:
    """The semantic delta from `base` to `target` (two `ContextSnapshotRecord`s). `identical`
    is content-hash equality (two commits that produced byte-identical governed content); `diff`
    is the added/changed/removed sections of the normalized definition."""
    return {
        "base": _meta(base),
        "target": _meta(target),
        "identical": base.content_hash == target.content_hash,
        "diff": diff_definition(base.normalized, target.normalized),
    }
