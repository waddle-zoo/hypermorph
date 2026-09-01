"""The durable miss-log (hy-jrpm). Operational only.

Writes one row per resolve outcome worth revisiting, at the transport boundary
rather than inside the resolver, so the resolver stays deterministic and
side-effect-free. This is NOT a system of record for meaning (ADR 0012, ADR
0020 decision 5): it stores what was asked, what was named, the status, and the
warning codes -- never governed context, and nothing here decides authority.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import ResolveMiss
from hyperset.repositories.dto import ResolveMissRecord


def _miss_record(row: ResolveMiss) -> ResolveMissRecord:
    return ResolveMissRecord(
        id=row.id,
        query=row.query,
        directive=dict(row.directive or {}),
        status=row.status,
        warning_codes=list(row.warning_codes or []),
        bundle_id=row.bundle_id,
        created_at=row.created_at,
    )


class PostgresResolveMissRepository:
    """Write-and-read side of `resolve_miss`. Operational only."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        query: str,
        directive: dict,
        status: str,
        warning_codes: list[str],
        bundle_id: str,
    ) -> ResolveMissRecord:
        """Persist one miss. Its own committing session, independent of the
        read sessions the resolve used, the same idiom every write repository
        keeps."""
        with self._session_factory() as session, session.begin():
            row = ResolveMiss(
                query=query,
                directive=dict(directive or {}),
                status=status,
                warning_codes=list(warning_codes or []),
                bundle_id=bundle_id,
            )
            session.add(row)
            session.flush()
            return _miss_record(row)

    def recent(self, *, limit: int = 50) -> list[ResolveMissRecord]:
        """Most recent misses first, for the flywheel consumers to read."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ResolveMiss)
                    .order_by(ResolveMiss.created_at.desc(), ResolveMiss.id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_miss_record(r) for r in rows]
