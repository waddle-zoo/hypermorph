"""The durable MCP interaction trace (hy-oqevj, epic hy-01442 slice 2).

One row per traced tool call, written at the transport boundary so a local
Claude/MCP session can be RECONSTRUCTED after the fact: `session_id` groups a
session, `turn_id` a turn, and `correlation_id` ties a search to the resolve
that follows. Operational audit only -- like `resolve_miss`, this is NOT a
system of record for meaning (ADR 0012). Nothing here decides authority.

The write is its OWN committing session, independent of the read sessions the
traced operation used, the same idiom every write repository keeps. The caller
treats a failed write as DEGRADED logging, never as a failed tool call -- the
trace never gates a served answer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import McpInteractionTrace
from hyperset.repositories.dto import InteractionTraceRecord


def _trace_record(row: McpInteractionTrace) -> InteractionTraceRecord:
    return InteractionTraceRecord(
        id=row.id,
        workspace=row.workspace,
        principal_identity=row.principal_identity,
        session_id=row.session_id,
        turn_id=row.turn_id,
        tool_call_id=row.tool_call_id,
        correlation_id=row.correlation_id,
        intent=row.intent,
        query=row.query,
        tool_name=row.tool_name,
        search_mode=row.search_mode,
        filters=dict(row.filters or {}),
        hit_ids=list(row.hit_ids or []),
        duration_ms=row.duration_ms,
        source_staleness=dict(row.source_staleness or {}),
        miss=dict(row.miss) if row.miss is not None else None,
        answer_bundle_id=row.answer_bundle_id,
        decision_ids=list(row.decision_ids or []),
        feedback_ids=list(row.feedback_ids or []),
        status=row.status,
        created_at=row.created_at,
    )


class PostgresInteractionTraceRepository:
    """Write-and-read side of the durable MCP interaction trace. Audit only."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        workspace: str,
        principal_identity: str,
        session_id: str | None,
        turn_id: str | None,
        tool_call_id: str | None,
        correlation_id: str,
        intent: str | None,
        query: str | None,
        tool_name: str,
        search_mode: str | None,
        filters: dict,
        hit_ids: list[str],
        duration_ms: int,
        source_staleness: dict,
        miss: dict | None,
        answer_bundle_id: str | None,
        status: str,
    ) -> InteractionTraceRecord:
        """Persist one traced tool call. `query`/`intent` must already be
        redacted and `hit_ids` must be opaque location ids -- this method stores
        exactly what it is given and never reaches back to source content."""
        with self._session_factory() as session, session.begin():
            row = McpInteractionTrace(
                workspace=workspace,
                principal_identity=principal_identity,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                correlation_id=correlation_id,
                intent=intent,
                query=query,
                tool_name=tool_name,
                search_mode=search_mode,
                filters=dict(filters or {}),
                hit_ids=list(hit_ids or []),
                duration_ms=duration_ms,
                source_staleness=dict(source_staleness or {}),
                miss=dict(miss) if miss is not None else None,
                answer_bundle_id=answer_bundle_id,
                decision_ids=[],
                feedback_ids=[],
                status=status,
            )
            session.add(row)
            session.flush()
            return _trace_record(row)

    def recent(self, *, limit: int = 50) -> list[InteractionTraceRecord]:
        """Most recent traced calls first, for an operator to read a session back."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(McpInteractionTrace)
                    .order_by(McpInteractionTrace.created_at.desc(), McpInteractionTrace.id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_trace_record(r) for r in rows]

    def link_decision(self, *, workspace: str, correlation_id: str, decision_id: str) -> None:
        """Back-link a citation decision to every trace in its workspace/correlation chain."""
        with self._session_factory() as session, session.begin():
            rows = (
                session.execute(
                    select(McpInteractionTrace)
                    .where(
                        McpInteractionTrace.workspace == workspace,
                        McpInteractionTrace.correlation_id == correlation_id,
                    )
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            for row in rows:
                if decision_id not in (row.decision_ids or []):
                    row.decision_ids = [*(row.decision_ids or []), decision_id]

    def session_chain(self, session_id: str) -> list[InteractionTraceRecord]:
        """Every traced call in one session, in the order it happened -- the
        reconstructable chain the epic asks for."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(McpInteractionTrace)
                    .where(McpInteractionTrace.session_id == session_id)
                    .order_by(McpInteractionTrace.created_at.asc(), McpInteractionTrace.id.asc())
                )
                .scalars()
                .all()
            )
            return [_trace_record(r) for r in rows]

    def for_correlation(
        self, *, workspace: str, correlation_id: str, limit: int = 1000
    ) -> list[InteractionTraceRecord]:
        """Return one workspace-scoped correlation chain for citation verification."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(McpInteractionTrace)
                    .where(
                        McpInteractionTrace.workspace == workspace,
                        McpInteractionTrace.correlation_id == correlation_id,
                    )
                    .order_by(McpInteractionTrace.created_at.asc(), McpInteractionTrace.id.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_trace_record(row) for row in rows]
