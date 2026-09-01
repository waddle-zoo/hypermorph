"""Read-only summaries of the immutable Git context snapshot history.

The public agent surface resolves a current ``ContextBundle``.  The
playground and local operators also need to see how that context arrived at
its current commit, so this module exposes the existing repository history as
metadata without returning the stored source files or normalized payload.
"""

from __future__ import annotations

from hyperset.repositories.postgres import PostgresContextRepository


def get_context_history(
    *, session_factory, repository: str, ref: str, path: str, workspace: str | None = None
) -> dict:
    """Return an immutable snapshot timeline for one configured Git source.

    Identity is the same repository/ref/path tuple shown in the catalog.  The
    current pointer is marked on the matching snapshot; all other entries are
    historical snapshots retained for audit and rollback inspection. `workspace`
    scopes the identity lookup to one tenant (hq-t6nx): the served history read
    passes the caller's workspace so a tenant cannot read another's timeline,
    and the same (repository, ref, path) pointer in two tenants resolves to the
    caller's own source.
    """

    context_repository = PostgresContextRepository(session_factory)
    source = context_repository.get_source_by_identity(
        repository=repository, ref=ref, path=path, workspace=workspace
    )
    current_id = source.current_snapshot.id if source.current_snapshot else None
    snapshots = []
    for snapshot in context_repository.history(source.id):
        snapshots.append(
            {
                "snapshot_id": snapshot.id,
                "commit_sha": snapshot.commit_sha,
                "committed_at": snapshot.committed_at.isoformat()
                if snapshot.committed_at
                else None,
                "synced_at": snapshot.synced_at.isoformat(),
                "domain": snapshot.domain,
                "title": snapshot.title,
                "content_sha256": snapshot.content_hash,
                "current": snapshot.id == current_id,
                "evidence_ref_count": len(snapshot.evidence_refs),
                "finding_count": len(snapshot.evidence_findings),
            }
        )
    return {
        "source": {
            "id": source.id,
            "repository": source.repository,
            "ref": source.ref,
            "path": source.path,
            "display_name": source.display_name,
            "enabled": source.enabled,
            "current_snapshot_id": current_id,
            "last_attempt_status": source.last_attempt_status,
            "last_attempt_at": source.last_attempt_at.isoformat()
            if source.last_attempt_at
            else None,
        },
        "snapshots": snapshots,
    }
