"""Resolve optional context evidence refs against real observations (hy-gh-43).

A ref names a connector-native identity such as a Superset UUID. A link is
only created when an observed asset actually carries that identity;
display-name similarity never resolves anything here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperset.context.schema import (
    REF_AMBIGUOUS,
    REF_AWAITING_SYNC,
    REF_NOT_OBSERVED,
    EvidenceRef,
)
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import _AllWorkspaces


@dataclass
class EvidenceResolution:
    """Which refs resolved to observed assets, and which did not.

    `findings` is the one record of an unresolved ref -- `{code, ref,
    message}` -- because the code and the ref are what a caller acts on and
    the message is prose that may be reworded. `reasons` is a projection of it
    for the caller that predates it: a sync report read by a person. It is a
    key lookup, never a code re-derived from a sentence, which is the
    substring matching the code exists to remove.
    """

    resolved: list[dict]
    findings: list[dict] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [finding["message"] for finding in self.findings]


class ObservedEvidenceResolver:
    """Looks each ref up across every connection of its connector type IN one
    workspace.

    `workspace` is REQUIRED and FAIL-CLOSED (hq-t6nx): a ref resolves only against
    connections in that tenant, so a tenant's context sync (or a scoped VALIDATE) can
    never link to -- or persist -- another tenant's observed asset when connector-native
    ids overlap. Only the explicit `ALL_WORKSPACES` sentinel resolves across every
    tenant, reserved for the deferred estate-wide RESOLVE path."""

    def __init__(self, session_factory, *, workspace: str | _AllWorkspaces) -> None:
        self._connections = PostgresConnectionRepository(session_factory)
        self._assets = PostgresObservedAssetRepository(session_factory)
        self._syncs = PostgresSyncRepository(session_factory)
        self._workspace = workspace

    def _unmeasured(
        self, connectors: set[str], connections_by_type: dict[str, list[str]]
    ) -> dict[str, str]:
        """For each connector a ref names, why an absence there is not evidence
        of absence -- and no entry at all when it is.

        Two absences that read identically in `observed_assets` are different
        facts: one connector was asked and answered, the other was never asked.
        Which one this is lives in `sync_runs` and nowhere else, so it is
        looked up once per resolve rather than once per ref.

        EVERY connection of the type must have last finished a run that
        succeeded, not any of them, because a ref is looked up across all of
        them: a connection that has not read successfully is a place the asset
        could be sitting unseen, and a healthy sibling does not measure it.
        The LAST finished run rather than any successful one ever, because a
        connector that is down now is exactly when a newly created asset goes
        unseen. A connector with no connection at all is judged here too rather
        than defaulted at the call site: it is the same question, and left to
        `all(())` it is vacuously true -- "not in the estate" for an estate
        nobody has pointed Hyperset at.
        """
        unmeasured: dict[str, str] = {}
        for connector in sorted(connectors):
            connection_ids = connections_by_type.get(connector, [])
            if not connection_ids:
                unmeasured[connector] = "no connection is configured"
                continue
            never: list[str] = []
            failed: list[str] = []
            for connection_id in sorted(connection_ids):
                status = self._syncs.latest_finished_status(connection_id)
                if status is None:
                    never.append(connection_id)
                elif status != "succeeded":
                    failed.append(connection_id)
            reasons = []
            if never:
                reasons.append(f"never finished a sync: {', '.join(never)}")
            if failed:
                reasons.append(f"last sync failed: {', '.join(failed)}")
            if reasons:
                unmeasured[connector] = "; ".join(reasons)
        return unmeasured

    def resolve(self, refs: list[EvidenceRef]) -> EvidenceResolution:
        resolved: list[dict] = []
        findings: list[dict] = []
        connections_by_type: dict[str, list[str]] = {}
        for connection in self._connections.list(workspace=self._workspace):
            connections_by_type.setdefault(connection.connector_type, []).append(connection.id)
        unmeasured = self._unmeasured({ref.connector for ref in refs}, connections_by_type)

        for ref in refs:
            matches = []
            for connection_id in connections_by_type.get(ref.connector, []):
                try:
                    asset = self._assets.get_by_external_id(
                        connection_id=connection_id,
                        external_id=ref.external_id,
                        asset_type=ref.asset_type,
                    )
                except NotFoundError:
                    continue
                matches.append(asset)
            if not matches:
                reason = unmeasured.get(ref.connector)
                if reason:
                    message = (
                        f"evidence ref {ref.ref!r} matches no observed asset and the "
                        f"{ref.connector} estate has not been read ({reason}), so this is "
                        f"not evidence that it is absent; sync the {ref.connector} "
                        "connection to corroborate it"
                    )
                    finding = {"code": REF_AWAITING_SYNC, "ref": ref.ref, "message": message}
                    if ref.governed_source_ref is not None:
                        finding["governed_source_ref"] = ref.governed_source_ref
                    findings.append(finding)
                else:
                    message = (
                        f"evidence ref {ref.ref!r} matches no observed asset, and every "
                        f"{ref.connector} connection has read successfully; the asset it "
                        "names is not in the estate"
                    )
                    finding = {"code": REF_NOT_OBSERVED, "ref": ref.ref, "message": message}
                    if ref.governed_source_ref is not None:
                        finding["governed_source_ref"] = ref.governed_source_ref
                    findings.append(finding)
                continue
            if len(matches) > 1:
                # Ambiguous source identity: the same native id observed on
                # two connections is two different assets in the real world.
                # Picking one would invent a link Git never declared.
                message = (
                    f"evidence ref {ref.ref!r} is ambiguous: observed on connections "
                    f"{', '.join(sorted(asset.connection_id for asset in matches))}"
                )
                finding = {"code": REF_AMBIGUOUS, "ref": ref.ref, "message": message}
                if ref.governed_source_ref is not None:
                    finding["governed_source_ref"] = ref.governed_source_ref
                findings.append(finding)
                continue
            asset = matches[0]
            resolved_ref = {
                "ref": ref.ref,
                "connector": ref.connector,
                "asset_type": ref.asset_type,
                "external_id": ref.external_id,
                "asset_id": asset.id,
                "connection_id": asset.connection_id,
                "observed_version_id": (
                    asset.current_version.id if asset.current_version else None
                ),
            }
            if ref.governed_source_ref is not None:
                resolved_ref["governed_source_ref"] = ref.governed_source_ref
            resolved.append(resolved_ref)
        return EvidenceResolution(resolved=resolved, findings=findings)
