"""One Git context sync (hy-gh-43): read the configured ref, validate what
Git says at that exact commit, and persist an immutable snapshot.

Fail-safe by construction. A failure path -- unreachable repository, invalid
schema, structurally unlinkable ref -- records the attempt and leaves the
previously valid snapshot serving. Hyperset never edits, repairs, or approves
the customer's context; the only way to change it is a commit.

What is NOT a failure path is evidence resolution (ADR 0017). A ref that no
connected system has observed is a fact about Superset or DataHub, and
withholding the commit over it would make connected-system sync state a
precondition for Git context existing. Those refs are recorded as findings on
the snapshot instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from hyperset.context.adapter.apply import apply_adapter, has_adapter
from hyperset.context.errors import ContextValidationError, GitReadError
from hyperset.context.evidence import ObservedEvidenceResolver
from hyperset.context.git import GitContextReader
from hyperset.context.hierarchy import validate_domain
from hyperset.context.schema import parse_context
from hyperset.repositories.postgres import PostgresContextRepository


@dataclass
class ContextSyncResult:
    """`status` mirrors what was persisted: "synced" (a new immutable
    snapshot), "unchanged" (the ref still points at the snapshotted commit),
    or "failed" (nothing replaced).

    `reasons` is why nothing was persisted and `findings` is what could not be
    corroborated in what WAS persisted. They are separate fields because they
    are separate outcomes: a sync with findings succeeded.

    `synced_at` is when the snapshot `findings` describes was resolved, which
    on the unchanged path is an earlier run than this one. It travels with
    them because their sentences are present tense about the world -- "matches
    no observed asset", "sync the superset connection to corroborate it" --
    and are replayed byte-identically on every later run, including the run an
    operator makes right after doing what one asked (hy-5lgg)."""

    source_id: str
    status: str
    commit_sha: str | None = None
    snapshot_id: str | None = None
    synced_at: datetime | None = None
    reasons: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # "invalid" is the validate dry-run's not-ok (hq-3fjt), alongside sync's
        # "failed"; a dry-run persists nothing, but it is still not a pass.
        return self.status not in ("failed", "invalid")


def sync_git_context(
    *,
    source_id: str,
    session_factory,
    cache_dir: Path | str,
    reader: GitContextReader | None = None,
    session=None,
    workspace: str | None = None,
) -> ContextSyncResult:
    """With `session` given, the snapshot/attempt persistence is written in THAT
    transaction and not committed here, so a caller can couple the re-sync to a
    lifecycle+audit append in ONE transaction (hq-ci92 reconcile): if the audit
    fails, the snapshot rolls back too, honouring the "nothing recorded" contract.
    The Git read and the read-only resolver run outside it (they persist no source
    state). With `workspace` given the source is resolved WITHIN that tenant
    (hq-t6nx), so the admin sync of one tenant cannot snapshot another's source; an
    internal caller (CLI, reconcile) passes None."""
    repository = PostgresContextRepository(session_factory)
    source = repository.get_source(source_id, workspace=workspace)
    reader = reader or GitContextReader(cache_dir)

    try:
        read = reader.read(repository=source.repository, ref=source.ref, path=source.path)
    except GitReadError as exc:
        repository.record_failure(source_id, error=str(exc), session=session)
        return ContextSyncResult(source_id=source_id, status="failed", reasons=[str(exc)])

    current = source.current_snapshot
    if current is not None and current.commit_sha == read.commit_sha:
        # An unchanged commit is a no-op: no new snapshot, no re-validation
        # of content that already produced one. The findings are still
        # reported, read off the stored snapshot rather than recomputed:
        # a no-op sync that says nothing is indistinguishable from a gap that
        # closed, and this is the run an operator makes right after doing what
        # a finding's message asked (hy-f462).
        repository.record_unchanged(source_id, commit_sha=read.commit_sha, session=session)
        return ContextSyncResult(
            source_id=source_id,
            status="unchanged",
            commit_sha=read.commit_sha,
            snapshot_id=current.id,
            synced_at=current.synced_at,
            findings=current.evidence_findings,
        )

    try:
        # A corpus carrying a context-adapter.yaml is PROJECTED through it into the
        # v0 shape (hy-s8up, #283); a source without one is ordinary v0 context,
        # parsed unchanged (back-compat). Either way the snapshot's commit_sha is
        # `read.commit_sha` -- the reviewed commit in the CUSTOMER's corpus, not a
        # projector's build artifact -- so authority points one hop closer to the
        # truth (the #283 provenance win). `AdapterApplyError` subclasses
        # `ContextValidationError`, so an apply failure records like an invalid
        # manifest: the last valid snapshot keeps serving.
        document = (
            apply_adapter(read.files) if has_adapter(read.files) else parse_context(read.files)
        )
    except ContextValidationError as exc:
        repository.record_failure(
            source_id, error="; ".join(exc.reasons), commit_sha=read.commit_sha, session=session
        )
        return ContextSyncResult(
            source_id=source_id, status="failed", commit_sha=read.commit_sha, reasons=exc.reasons
        )

    # Estate placement -- a domain COLLISION and a hierarchy fault -- is checked
    # by the shared `_check_estate_placement` (hq-3fjt), so the validate dry-run
    # applies the SAME rule. Refused like invalid content: the last valid snapshot
    # keeps serving (`record_failure` leaves `current_snapshot_id` untouched) and
    # the sync exits non-zero, with the reason naming the existing claimant or the
    # hierarchy fault.
    placement_reasons = _check_estate_placement(
        repository, source_id, document, source.workspace_id
    )
    if placement_reasons:
        repository.record_failure(
            source_id,
            error="; ".join(placement_reasons),
            commit_sha=read.commit_sha,
            session=session,
        )
        return ContextSyncResult(
            source_id=source_id,
            status="failed",
            commit_sha=read.commit_sha,
            reasons=placement_reasons,
        )

    # Deliberately not a branch: what resolution found never decides whether
    # the snapshot is recorded, only what the snapshot discloses.
    # Evidence resolves WITHIN the source's own tenant (hq-t6nx #438 r3): a sync must
    # never link a governed ref to -- or persist -- another tenant's observed asset when
    # connector-native ids overlap. The source carries its workspace_id.
    resolution = ObservedEvidenceResolver(session_factory, workspace=source.workspace_id).resolve(
        document.evidence_refs
    )

    snapshot, created = repository.record_snapshot(
        source_id=source_id,
        commit_sha=read.commit_sha,
        committed_at=read.committed_at,
        domain=document.domain,
        title=document.title,
        files=read.files,
        normalized=document.normalized,
        owner_refs=_owner_refs(document.owner_refs, read.repository_owner_refs),
        evidence_refs=resolution.resolved,
        evidence_findings=resolution.findings,
        session=session,
    )
    return ContextSyncResult(
        source_id=source_id,
        status="synced" if created else "unchanged",
        commit_sha=read.commit_sha,
        snapshot_id=snapshot.id,
        synced_at=snapshot.synced_at,
        findings=snapshot.evidence_findings,
    )


def _check_estate_placement(repository, source_id: str, document, workspace: str) -> list[str]:
    """The estate-level validation shared by `sync_git_context` and the validate
    dry-run (hq-3fjt): a domain COLLISION (another ENABLED source already claims
    this domain, hy-gh-282) and a hierarchy fault (an unknown parent or a cycle,
    ADR-0031). Returns the human reasons the document may NOT be served, empty
    when it may. Reads only -- it persists nothing -- so a dry-run applies exactly
    the rule a sync would.

    SCOPED to the source's own `workspace` (hq-t6nx): an estate serves one source
    per domain PER TENANT, so a domain claimed in another tenant is not a collision
    here and never appears in this source's reasons -- the collision read and the
    hierarchy read are both confined to the workspace, so no cross-tenant source
    id/repo can leak into a failure message.

    RESIDUAL RACE, accepted by design (hy-gh-282 panel ruling): the collision is
    a check-then-write with no DB uniqueness constraint (a constraint cannot live
    on the versioned snapshot's domain -- see `source_claiming_domain`), so two
    concurrent first-time syncs of one fresh domain can both pass. That corrupts
    nothing: it degrades to the pre-existing-duplicate path -- resolve discloses
    `domain_ambiguous` and never picks a winner, and `hyperset context disable`
    reconciles it.

    The hierarchy check is SCOPED to this document's own parent chain, not the
    whole estate: a pre-existing dangling parent elsewhere must not fail an
    unrelated source's sync with a misattributed reason and wedge every write.
    """
    claimant = repository.source_claiming_domain(
        document.domain, exclude_source_id=source_id, workspace=workspace
    )
    if claimant is not None:
        return [
            f"the domain {document.domain!r} is already claimed by context source "
            f"{claimant.id} ({claimant.repository}@{claimant.ref}:{claimant.path} "
            f"commit {claimant.current_snapshot.commit_sha}); an estate serves one source per "
            f"domain. Disable the other source with `hyperset context disable {claimant.id}`, "
            f"or change this manifest's domain, then sync again"
        ]
    parent_of = {
        other.current_snapshot.domain: other.current_snapshot.normalized.get("parent")
        for other in repository.list_sources(workspace=workspace)
        if other.id != source_id and other.enabled and other.current_snapshot is not None
    }
    parent_of[document.domain] = document.normalized.get("parent")
    return validate_domain(document.domain, parent_of)


def validate_git_context(
    *,
    source_id: str,
    session_factory,
    cache_dir: Path | str,
    reader: GitContextReader | None = None,
    workspace: str | None = None,
    session=None,
) -> ContextSyncResult:
    """Live-check a source's configured ref WITHOUT persisting a new snapshot (hq-3fjt).

    Reads the ref, parses/validates the manifest, and checks estate placement
    (domain collision + hierarchy) using the SAME reader, parser, and
    `_check_estate_placement` as `sync_git_context`. It records NO new governed
    snapshot -- a dry run never snapshots -- so the last VALID context keeps
    serving whatever this reports. `status` is "valid" or "invalid"; `reasons`
    names why an "invalid" result would be refused.

    It DOES record the outcome of THIS LIVE ATTEMPT through the SAME status path
    sync uses -- `record_failure` on a failure, `record_unchanged` when the live
    check confirms the currently-served commit -- so the recorded `last_attempt_*`
    the admin card shows matches what a live validation just found, and a remote
    that became unreachable after a good sync can no longer hide behind a stale
    green (hy-ppufd). It leaves `current_snapshot_id` untouched. A valid NEW commit
    records nothing: a dry run must not claim a sync it did not perform, so the
    card keeps its last real sync status while the "valid" result is shown live.

    With `session` given, the `record_*` write is made in THAT transaction and not
    committed here, so the caller can COUPLE the status write to its audit append in
    ONE transaction: if the audit fails, the status write rolls back too and
    `last_attempt_*` is left exactly as it was (hy-ppufd #446 -- an authority-status
    change is never persisted unaudited). The Git read runs first, before any write.
    """
    repository = PostgresContextRepository(session_factory)
    source = repository.get_source(source_id, workspace=workspace)
    reader = reader or GitContextReader(cache_dir)
    try:
        read = reader.read(repository=source.repository, ref=source.ref, path=source.path)
    except GitReadError as exc:
        repository.record_failure(source_id, error=str(exc), session=session)
        return ContextSyncResult(source_id=source_id, status="invalid", reasons=[str(exc)])
    try:
        document = (
            apply_adapter(read.files) if has_adapter(read.files) else parse_context(read.files)
        )
    except ContextValidationError as exc:
        repository.record_failure(
            source_id, error="; ".join(exc.reasons), commit_sha=read.commit_sha, session=session
        )
        return ContextSyncResult(
            source_id=source_id, status="invalid", commit_sha=read.commit_sha, reasons=exc.reasons
        )
    reasons = _check_estate_placement(repository, source_id, document, source.workspace_id)
    if reasons:
        repository.record_failure(
            source_id, error="; ".join(reasons), commit_sha=read.commit_sha, session=session
        )
        return ContextSyncResult(
            source_id=source_id, status="invalid", commit_sha=read.commit_sha, reasons=reasons
        )
    current = source.current_snapshot
    if current is not None and current.commit_sha == read.commit_sha:
        # The live check confirms the exact commit already serving: record that the
        # last attempt found the served pin unchanged, so recorded == live.
        repository.record_unchanged(source_id, commit_sha=read.commit_sha, session=session)
    return ContextSyncResult(
        source_id=source_id,
        status="valid",
        commit_sha=read.commit_sha,
        reasons=reasons,
    )


def _owner_refs(manifest_owners: list[str], repository_owners: list[str]) -> list[dict]:
    """Owners are captured, never invented: each entry states whether the
    manifest or the repository's own CODEOWNERS said it."""
    owners = [{"ref": ref, "source": "manifest"} for ref in manifest_owners]
    owners.extend({"ref": ref, "source": "codeowners"} for ref in repository_owners)
    return owners
