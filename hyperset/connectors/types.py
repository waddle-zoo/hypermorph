"""Connector-agnostic types.

hy-gh-27 implemented one connector (Superset); hy-gh-17 added a second
(DataHub). `Connector` below is the *only* seam those two implementations
actually proved they share -- snapshot the source, normalize what was read,
and say which transport produced it. It is not a connector SDK, and ADR
0010 keeps one out of v0: everything source-specific (auth, pagination,
identity extraction, link shapes) stays inside each connector package.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class ConnectionTest:
    """Result of `Connector.test_connection()`."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class EstablishedDenominator:
    """The warrant to soft-delete one asset type: an established count of what
    the source holds, WITH the instrument that established it (hy-6nit).

    A bare flag would let "nobody established this" and "something established
    this, path unknown" read alike, and only one of those may license a
    deletion. So `producer` is required and refuses to be empty: a warrant with
    no instrument named cannot be constructed at all, rather than being
    constructed and later disbelieved.

    One field, because a field `run_sync` does not read is a trap: a
    `sufficient=False` sitting beside a deletion that ran anyway reads as a
    check when it is decoration. Constructing this object IS the claim of
    sufficiency, and the producer is what a reader holds to it.

    That bar is higher than a count. Count equality is necessary and not
    sufficient: over an index with no point-in-time, one entity skipped by a
    repeated sort key and one indexed mid-walk cancel exactly, so the counts
    agree while the sets differ -- and it is the skipped entity that then gets
    soft-deleted. A producer holding only a count must pair it with something
    else (a dedupe invariant, a cursor-termination check) and name both here,
    since `producer` is the whole of what the disclosure can show.
    """

    producer: str

    def __post_init__(self) -> None:
        if not self.producer.strip():
            raise ValueError("EstablishedDenominator requires a producer: an unnamed instrument")


@dataclass
class ConnectorSnapshot:
    """Raw output of one connector pull, before normalization.

    `bundle` is grouped by top-level asset-type key (e.g. "databases",
    "datasets", "charts", "dashboards" for Superset) -> list of raw
    source-native dicts, exactly as read from the source. Nothing in this
    type drops or reshapes a field -- that's `Connector.normalize()`'s job,
    and even there the raw dict is preserved, not replaced (Required
    decisions: "Unknown source fields must survive round trips").

    `transport` names which upstream contract produced the payloads, because
    an export bundle and a REST response are not interchangeable shapes
    (ADR 0003). `covered_asset_types` is what this snapshot actually looked
    for: a type absent from it was never read, so it must never be inferred
    deleted ("partial or failed sync never implies deletion").
    `source_capabilities` and `checkpoint` carry the per-sync source version,
    transport, capability, and checkpoint record `docs/research/superset-api-v1.md`
    item 5 requires.
    """

    source_version: str | None
    bundle: dict[str, list[dict]]
    warnings: list[str] = field(default_factory=list)
    transport: str = "export_bundle"
    covered_asset_types: tuple[str, ...] = ("database", "dataset", "chart", "dashboard")
    source_capabilities: dict = field(default_factory=dict)
    checkpoint: dict | None = None
    established_denominators: dict[str, EstablishedDenominator] = field(default_factory=dict)
    """Per asset type, the warrant to soft-delete what this snapshot did not
    serve (hy-6nit). Absent means refused.

    Empty by default, and the default IS the rule: an asset type with no entry
    here is read as incomplete, not as complete, and `run_sync` declines its
    deletion pass. `covered_asset_types` says which types were looked for;
    this says which of them were read to the end, and only the second question
    may decide a deletion. "May deletion run" has a safe default. "Was this
    read complete" has none.

    NO CONNECTOR PRODUCES ONE TODAY, so both v0 connectors stop soft-deleting,
    and neither is carved an exemption to keep the blast radius small.

    DataHub: `scrollAcrossEntities` offers no denominator the client can
    establish. `total` is capped at 10000, and the cap is invisible -- the
    field is `Int!` with no relation, and `SearchFlags` exposes nothing that
    lifts it -- so a gate built on it goes quiet at exactly the estate size
    where a short scroll would soft-delete tens of thousands of assets. No
    aggregation error bound is reachable either: `AggregationMetadata` carries
    value, count, entity, displayName and nothing else. This is not a gap
    waiting on a newer DataHub, and that matters, because "wait for the next
    version" would make this rule provisional. `ScrollAcrossLineageResults`
    and `SearchAcrossLineageResults` DO carry `isPartial`. DataHub knows how
    to disclose partiality and ships that disclosure one surface away from the
    one discovery reads; the entity scroll, search and aggregate surfaces
    carry no such field. The silence is a design choice, measured against the
    pinned v1.6.0 schema.

    Superset: `list_resource` measures completeness in rows, not identities
    (hy-fc01), so it has no established denominator either."""


@dataclass
class UnresolvedLink:
    """A relationship `normalize()` observed but can't fully resolve yet --
    the target is named by its *external* id/uuid, not the internal
    `ObservedAsset.id` `ObservedAssetRepository` will assign it. Resolving
    these into persisted relationships is the sync orchestration's job,
    once every asset in the snapshot has an internal id to point at."""

    kind: str
    target_external_id: str
    relation: str = "references"


@dataclass
class ObservedAssetInput:
    """What `ObservedAssetRepository.upsert()` needs, produced by
    `Connector.normalize()`. IDs, versions, and content hashes belong to
    persistence, not the connector.
    """

    external_id: str
    asset_type: str
    raw_payload: dict
    normalized: dict = field(default_factory=dict)
    source_modified_at: datetime | None = None
    links: list[UnresolvedLink] = field(default_factory=list)
    hash_basis: dict | None = None
    """How change detection should narrow `raw_payload` before hashing it,
    when the source mixes its own state with values the server re-renders
    per request. Superset's REST bodies carry `*_humanized` relative times
    ("now", "10 minutes ago"), so hashing the whole payload would report a
    new immutable version on every resync of an unchanged asset.

    A declared rule, not a projected payload: the rules are defined by
    `hyperset.repositories.hash_basis`, applied at persistence time, and
    stored on the version row, so what a `content_hash` covers stays
    recomputable from stored state (hy-y8g). The stored `raw_payload` still
    keeps every field; only the hash input is narrowed, and the connector
    also discloses the excluded keys as a snapshot warning.
    `None` means "hash the raw payload".

    The rule belongs to the connector, never to the transport (hy-y8g.3): one
    connection may carry more than one read mode (hy-gh-48), and two read
    modes hashing the same asset by different rules would append a version on
    every switch, from the rule changing rather than the source. A connector
    that narrows the basis for one transport narrows it by the same rule for
    all of them."""


@runtime_checkable
class Connector(Protocol):
    """What `hyperset.connectors.sync.run_sync` needs from any source.

    Runtime-checkable so a test can assert both v0 connectors still satisfy
    it. `isinstance` verifies the members exist, not their signatures, so it
    is a guard against an accidental rename -- not a substitute for the
    per-connector contract tests.

    Deliberately three members wide. Both v0 connectors take their own
    constructor arguments and own their own transport client; nothing here
    describes credentials, endpoints, or entity shapes, because the two
    implementations agreed on none of those.
    """

    @property
    def transport(self) -> str:
        """Which upstream contract this instance reads (e.g. "rest",
        "graphql", "export_bundle"). Two transports of the same product are
        not interchangeable shapes (ADR 0003), so the sync records it."""

    def test_connection(self) -> ConnectionTest: ...

    def snapshot(self, checkpoint: dict | None = None) -> ConnectorSnapshot: ...

    def normalize(self, snapshot: ConnectorSnapshot) -> Iterable[ObservedAssetInput]: ...
