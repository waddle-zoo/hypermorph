from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import GovernedContext, GovernedContextVersion
from hyperset.repositories.dto import GovernedContextRecord, GovernedContextVersionRecord
from hyperset.repositories.errors import NotFoundError


def _version_record(row: GovernedContextVersion | None) -> GovernedContextVersionRecord | None:
    if row is None:
        return None
    return GovernedContextVersionRecord(
        id=row.id,
        context_id=row.context_id,
        version=row.version,
        title=row.title,
        definition=row.definition,
        reviewer=row.reviewer,
        approval_decision_id=row.approval_decision_id,
        created_at=row.created_at,
        created_by=row.created_by,
        review_interval_days=row.review_interval_days,
    )


def _context_record(
    row: GovernedContext, current: GovernedContextVersion | None
) -> GovernedContextRecord:
    return GovernedContextRecord(
        id=row.id,
        workspace=getattr(row, "workspace", "default"),
        context_type=row.context_type,
        domain=row.domain,
        name=row.name,
        lifecycle=row.lifecycle,
        current_version=_version_record(current),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _search_text(definition: dict) -> str:
    parts = []
    for key in ("title", "label", "description", "business_definition"):
        value = definition.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts) or " "


def _version_columns(definition: dict) -> dict:
    """Denormalized read columns extracted from `definition` -- see
    `GovernedContextVersion`'s docstring: `definition` stays the single
    round-trippable source, these are a query convenience.

    This serves the ADR 0011 dual-governance store, whose writer -- the admin
    UI -- v0 has not built. Both of its two call sites, `propose_version` below
    and `PostgresReviewRepository.approve` in `review.py`, have only test
    callers, and no bundle, transport, CLI or processor path reads a
    `GovernedContextVersion`; every served resolve builds instructions from
    `git_instructions(snapshot.normalized)` instead, the `parse_context` path
    (`bundle/resolver.py`, `bundle/catalog.py`). Unbuilt, not broken -- this is
    not dead code awaiting deletion. To re-check whether that is still true,
    grep this function rather than either writer:

        git grep -n _version_columns

    Both writers must be checked, or a store wired up through only one of them
    leaves this comment reading true while being false.

    Two entries below are placeholders for that unbuilt writer rather than
    oversights, and no value has ever reached either on any path:

    - `filters` is hardcoded empty because no `definition` key feeds it.
    - `freshness_policy` reads `freshness_expectation`, whose only occurrence
      in the tree is the line below, into a column with no reader. The served
      freshness in `linked_evidence` is built from observation timestamps
      (`resolver.py`, `_iso(asset.last_seen_at)` and its neighbours) and never
      consults this column.

    When the writer is built, the question hy-gh-43 answered for
    `manifest.yaml` -- is an unrecognized key an error or a silent drop --
    comes back for `definition` and gets answered then.
    """
    return {
        "approved_assets": definition.get("approved_datasets", []) or [],
        "joins": definition.get("join_rules", []) or [],
        "filters": [],
        "dimensions": definition.get("valid_dimensions", []) or [],
        "warnings": definition.get("known_alternatives", []) or [],
        "freshness_policy": definition.get("freshness_expectation"),
        "validation_guidance": definition.get("validation_rules", []) or [],
        "conflicts": definition.get("definitions", []) or [],
        "deprecations": definition.get("deprecated_sources", []) or [],
    }


class PostgresGovernedContextRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def propose_version(
        self,
        *,
        context_type: str,
        domain: str,
        name: str,
        title: str,
        definition: dict,
        created_by: str | None = None,
        review_interval_days: int | None = None,
        workspace: str = "default",
    ) -> GovernedContextVersionRecord:
        with self._session_factory() as session, session.begin():
            context = session.execute(
                select(GovernedContext).where(
                    GovernedContext.workspace == workspace,
                    GovernedContext.context_type == context_type,
                    GovernedContext.domain == domain,
                    GovernedContext.name == name,
                )
            ).scalar_one_or_none()
            if context is None:
                context = GovernedContext(
                    workspace=workspace,
                    context_type=context_type,
                    domain=domain,
                    name=name,
                    lifecycle="candidate",
                )
                session.add(context)
                session.flush()
                next_version = 1
            else:
                latest_version = session.execute(
                    select(func.max(GovernedContextVersion.version)).where(
                        GovernedContextVersion.context_id == context.id
                    )
                ).scalar_one()
                next_version = (latest_version or 0) + 1
                context.updated_at = utcnow()

            version_row = GovernedContextVersion(
                context_id=context.id,
                version=next_version,
                title=title,
                definition=definition,
                created_by=created_by,
                review_interval_days=review_interval_days,
                **_version_columns(definition),
            )
            session.add(version_row)
            session.flush()
            # Only a persisted ReviewDecision may replace an approved pointer.
            if context.lifecycle != "approved":
                context.current_version_id = version_row.id
            session.flush()

            session.execute(
                update(GovernedContextVersion)
                .where(GovernedContextVersion.id == version_row.id)
                .values(
                    search_vector=func.to_tsvector("english", _search_text(definition) or title)
                )
            )
            return _version_record(version_row)

    def get(self, context_id: str, *, workspace: str = "default") -> GovernedContextRecord:
        with self._session_factory() as session:
            context = session.execute(
                select(GovernedContext).where(
                    GovernedContext.id == context_id,
                    GovernedContext.workspace == workspace,
                )
            ).scalar_one_or_none()
            if context is None:
                raise NotFoundError(f"governed context {context_id!r} not found")
            current = (
                session.get(GovernedContextVersion, context.current_version_id)
                if context.current_version_id
                else None
            )
            return _context_record(context, current)

    def get_by_name(
        self, *, context_type: str, domain: str, name: str, workspace: str = "default"
    ) -> GovernedContextRecord:
        with self._session_factory() as session:
            context = session.execute(
                select(GovernedContext).where(
                    GovernedContext.workspace == workspace,
                    GovernedContext.context_type == context_type,
                    GovernedContext.domain == domain,
                    GovernedContext.name == name,
                )
            ).scalar_one_or_none()
            if context is None:
                raise NotFoundError(f"governed context {domain}/{context_type}/{name} not found")
            current = (
                session.get(GovernedContextVersion, context.current_version_id)
                if context.current_version_id
                else None
            )
            return _context_record(context, current)

    def get_version(
        self, context_id: str, version: int, *, workspace: str = "default"
    ) -> GovernedContextVersionRecord:
        with self._session_factory() as session:
            row = session.execute(
                select(GovernedContextVersion)
                .join(GovernedContext, GovernedContext.id == GovernedContextVersion.context_id)
                .where(
                    GovernedContextVersion.context_id == context_id,
                    GovernedContextVersion.version == version,
                    GovernedContext.workspace == workspace,
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"governed context {context_id!r} has no version {version}")
            return _version_record(row)

    def list_all(
        self,
        *,
        domain: str | None = None,
        context_type: str | None = None,
        workspace: str = "default",
    ) -> list[GovernedContextRecord]:
        """Every context at HEAD, unfiltered by search relevance -- what
        hy-gh-38's processor needs to scan for policy violations (review
        interval, missing owner, ...), which `search()`'s tsquery match
        can't express ("give me everything")."""
        with self._session_factory() as session:
            stmt = select(GovernedContext).where(GovernedContext.workspace == workspace)
            if domain is not None:
                stmt = stmt.where(GovernedContext.domain == domain)
            if context_type is not None:
                stmt = stmt.where(GovernedContext.context_type == context_type)
            contexts = session.execute(stmt).scalars().all()
            records = []
            for context in contexts:
                current = (
                    session.get(GovernedContextVersion, context.current_version_id)
                    if context.current_version_id
                    else None
                )
                records.append(_context_record(context, current))
            return records

    def history(
        self, context_id: str, *, workspace: str = "default"
    ) -> list[GovernedContextVersionRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(GovernedContextVersion)
                    .join(GovernedContext, GovernedContext.id == GovernedContextVersion.context_id)
                    .where(GovernedContextVersion.context_id == context_id)
                    .where(GovernedContext.workspace == workspace)
                    .order_by(GovernedContextVersion.version)
                )
                .scalars()
                .all()
            )
            return [_version_record(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        context_type: str | None = None,
        workspace: str = "default",
    ) -> list[GovernedContextRecord]:
        with self._session_factory() as session:
            stmt = (
                select(GovernedContext, GovernedContextVersion)
                .join(
                    GovernedContextVersion,
                    GovernedContext.current_version_id == GovernedContextVersion.id,
                )
                .where(
                    GovernedContext.workspace == workspace,
                    GovernedContextVersion.search_vector.op("@@")(
                        func.plainto_tsquery("english", query)
                    ),
                )
            )
            if domain is not None:
                stmt = stmt.where(GovernedContext.domain == domain)
            if context_type is not None:
                stmt = stmt.where(GovernedContext.context_type == context_type)
            rows = session.execute(stmt).all()
            return [_context_record(c, v) for c, v in rows]
