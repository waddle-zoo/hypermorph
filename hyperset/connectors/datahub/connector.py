"""Read-only, lossless observation of a pinned DataHub OSS instance over
GraphQL (hy-gh-17).

DataHub is the second source in ADR 0010's two-source loop, and it is
complementary rather than overlapping: Superset supplies BI assets and
analytical configuration, DataHub supplies catalog identity, domain
membership, ownership, glossary terms, and lineage. Neither is approved
business truth, and this connector never reconciles the two -- see
`_CROSS_SOURCE_WARNING`.

Identity is DataHub's own URN, used verbatim as `ObservedAsset.external_id`.
No parsing, no re-derivation: a URN already encodes platform, name, and
fabric, so splitting it apart to rebuild a key would only invent a second
identity scheme that could disagree with the source.

Four entity types are projected: dataset, domain, corp user, glossary term.
Discovery is a scroll across *every* type the instance holds, so anything
outside those four is reported as an uncovered type with a real count rather
than silently missed -- and, per `run_sync`, an uncovered type is never
deletion-checked.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from hyperset.connectors.datahub.graphql import DataHubGraphQLClient
from hyperset.connectors.errors import ConnectorError
from hyperset.connectors.types import (
    ConnectionTest,
    ConnectorSnapshot,
    ObservedAssetInput,
    UnresolvedLink,
)
from hyperset.repositories.hash_basis import DROP_KEYS, SORT_LIST_KEYS_BY

# DataHub bookkeeping that moves when metadata is rewritten with identical
# content: real fields, kept in the stored payload, but excluded from change
# detection so an unchanged resync stays a no-op. Same role as the Superset
# connector's `*_humanized` suffix, matched by exact key at any depth.
_VOLATILE_KEYS = frozenset({"lastIngested"})

# Upstream maps that GraphQL serializes as a list, whose entry order the
# pinned instance does not preserve across rewrites. Key-sorted in the hash
# basis only; `raw_payload` keeps the order the source served.
#
# Deliberately narrow: only keys observed to reorder are sorted. Sorting every
# list would also reorder `schemaMetadata.fields`, where column order is real
# information, and would hide genuine reordering elsewhere.
_UNORDERED_MAP_KEYS = frozenset({"customProperties"})

# Declared once, stored on every version row it produces, and applied by the
# repository -- so what an entity's `content_hash` covers can be replayed from
# stored state instead of from this module (hy-y8g).
_HASH_BASIS = {
    DROP_KEYS: sorted(_VOLATILE_KEYS),
    SORT_LIST_KEYS_BY: {key: "key" for key in sorted(_UNORDERED_MAP_KEYS)},
}

# -- projections ------------------------------------------------------------
#
# One document per entity type, each the exact upstream contract this
# connector reads. `run_sync` records their combined fingerprint on the
# checkpoint, so narrowing a projection shows up as a contract change instead
# of looking like the source lost metadata.
#
# Notes on shapes the pinned v1.6.0 build forced (all verified live):
#   - `CorpUser.status` is an enum leaf, not an object: no subselection.
#   - `DatasetProperties.created` is a `Long` leaf, while `lastModified` is an
#     `AuditStamp` object.
#   - Datasets carry no `upstreamLineage` field; upstreams come from
#     `lineage(input: {direction: UPSTREAM})` as `DownstreamOf` edges.
#   - Glossary term *definitions* are projected on the glossary term entity
#     only. Datasets reference terms by URN alone, so editing a definition
#     upstream is one changed asset, not three.

_DATASET_QUERY = """
query hypersetDataset($urn: String!) {
  dataset(urn: $urn) {
    urn
    type
    name
    origin
    lastIngested
    platform { urn name }
    subTypes { typeNames }
    status { removed }
    properties {
      name
      qualifiedName
      description
      externalUrl
      created
      lastModified { time actor }
      customProperties { key value }
    }
    domain { domain { urn } }
    ownership {
      owners {
        owner {
          ... on CorpUser { urn }
          ... on CorpGroup { urn }
        }
        ownershipType { urn }
      }
    }
    glossaryTerms { terms { term { urn } } }
    tags { tags { tag { urn } } }
    schemaMetadata {
      name
      primaryKeys
      fields { fieldPath nullable nativeDataType type description }
    }
    upstreams: lineage(input: {direction: UPSTREAM, start: 0, count: 100}) {
      total
      relationships { type entity { urn type } }
    }
  }
}
"""

_DOMAIN_QUERY = """
query hypersetDomain($urn: String!) {
  domain(urn: $urn) {
    urn
    type
    properties { name description }
    ownership {
      owners {
        owner {
          ... on CorpUser { urn }
          ... on CorpGroup { urn }
        }
        ownershipType { urn }
      }
    }
  }
}
"""

_CORP_USER_QUERY = """
query hypersetCorpUser($urn: String!) {
  corpUser(urn: $urn) {
    urn
    type
    username
    status
    properties { displayName email title active departmentName }
  }
}
"""

_GLOSSARY_TERM_QUERY = """
query hypersetGlossaryTerm($urn: String!) {
  glossaryTerm(urn: $urn) {
    urn
    type
    name
    hierarchicalName
    properties { name definition termSource sourceRef }
    ownership {
      owners {
        owner {
          ... on CorpUser { urn }
          ... on CorpGroup { urn }
        }
        ownershipType { urn }
      }
    }
    domain { domain { urn } }
  }
}
"""

_CROSS_SOURCE_WARNING = (
    "cross-source mapping is not performed here: DataHub's own identifiers "
    "(custom properties, upstream lineage) are recorded verbatim as observations, and "
    "linking them to Superset-observed assets is a governance decision made downstream "
    "from explicit identifiers -- a matching display name is a review candidate, never a merge"
)

_UNPROJECTED_ENTITY_TYPES_WARNING = (
    "DataHub serves entity types this connector does not project ({types}); they were "
    "discovered but not read, and uncovered types are never deletion-checked"
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _custom_properties(properties: dict) -> dict[str, str]:
    """DataHub returns custom properties as `[{key, value}]`; observed as a
    mapping, with the original list still intact in `raw_payload`."""
    return {
        str(entry["key"]): entry.get("value")
        for entry in _as_list(properties.get("customProperties"))
        if isinstance(entry, dict) and entry.get("key") is not None
    }


def _owner_urns(payload: dict) -> list[str]:
    """Owner URNs exactly as DataHub gave them.

    A `CorpGroup` owner projects the same `urn` field as a `CorpUser`, so both
    land here; `_dataset_links` is what decides that only corp users are a
    resolvable link target today.
    """
    owners = _as_list(_as_dict(payload.get("ownership")).get("owners"))
    urns = []
    for entry in owners:
        urn = _as_dict(_as_dict(entry).get("owner")).get("urn")
        if urn:
            urns.append(str(urn))
    return urns


def _glossary_term_urns(payload: dict) -> list[str]:
    terms = _as_list(_as_dict(payload.get("glossaryTerms")).get("terms"))
    urns = []
    for entry in terms:
        urn = _as_dict(_as_dict(entry).get("term")).get("urn")
        if urn:
            urns.append(str(urn))
    return urns


def _domain_urn(payload: dict) -> str | None:
    urn = _as_dict(_as_dict(payload.get("domain")).get("domain")).get("urn")
    return str(urn) if urn else None


def _upstream_dataset_urns(payload: dict) -> list[str]:
    """Explicit upstream lineage edges, dataset-to-dataset only.

    This is the one relationship ADR 0010 lets cross a platform boundary, and
    it is only ever read from a lineage edge DataHub actually asserted.
    """
    relationships = _as_list(_as_dict(payload.get("upstreams")).get("relationships"))
    urns = []
    for entry in relationships:
        entity = _as_dict(_as_dict(entry).get("entity"))
        if entity.get("type") == "DATASET" and entity.get("urn"):
            urns.append(str(entity["urn"]))
    return urns


def _source_modified_at(payload: dict) -> datetime | None:
    """`properties.lastModified.time` as UTC, when the source actually set it.

    The pinned instance returns `time: 0` for metadata written without an
    audit stamp. Reading that as 1970-01-01 would be a fabricated
    modification time, so only a positive epoch-millisecond value is mapped
    and everything else stays `None`.
    """
    stamp = _as_dict(_as_dict(payload.get("properties")).get("lastModified"))
    time_ms = stamp.get("time")
    if not isinstance(time_ms, int) or isinstance(time_ms, bool) or time_ms <= 0:
        return None
    return datetime.fromtimestamp(time_ms / 1000, tz=UTC)


def _keys_present(payload: Any, wanted: frozenset[str]) -> set[str]:
    """Which of `wanted` this payload actually carried, at any depth.

    Drives the snapshot warnings, so what change detection narrowed is
    reported from the real payload rather than asserted in the abstract.
    """
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in wanted:
                found.add(key)
            found |= _keys_present(value, wanted)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_present(item, wanted)
    return found


# -- normalizers ------------------------------------------------------------


def _dataset_links(payload: dict) -> list[UnresolvedLink]:
    links = []
    domain_urn = _domain_urn(payload)
    if domain_urn:
        links.append(
            UnresolvedLink(kind="domain", target_external_id=domain_urn, relation="in_domain")
        )
    for owner_urn in _owner_urns(payload):
        # Only corp users are projected as entities, so a corp-group owner
        # deliberately surfaces as an unresolved-link warning from `run_sync`
        # rather than being dropped or silently retyped.
        links.append(
            UnresolvedLink(kind="corp_user", target_external_id=owner_urn, relation="owned_by")
        )
    for term_urn in _glossary_term_urns(payload):
        links.append(
            UnresolvedLink(
                kind="glossary_term", target_external_id=term_urn, relation="has_glossary_term"
            )
        )
    for upstream_urn in _upstream_dataset_urns(payload):
        links.append(
            UnresolvedLink(kind="dataset", target_external_id=upstream_urn, relation="derived_from")
        )
    return links


def _normalize_dataset(payload: dict) -> ObservedAssetInput:
    properties = _as_dict(payload.get("properties"))
    schema = _as_dict(payload.get("schemaMetadata"))
    normalized = {
        "name": properties.get("name") or payload.get("name"),
        "qualified_name": properties.get("qualifiedName"),
        "description": properties.get("description") or "",
        "platform_urn": _as_dict(payload.get("platform")).get("urn"),
        "fabric": payload.get("origin"),
        "sub_types": _as_list(_as_dict(payload.get("subTypes")).get("typeNames")),
        "domain_urn": _domain_urn(payload),
        "owner_urns": _owner_urns(payload),
        "glossary_term_urns": _glossary_term_urns(payload),
        "tag_urns": [
            str(urn)
            for urn in (
                _as_dict(_as_dict(entry).get("tag")).get("urn")
                for entry in _as_list(_as_dict(payload.get("tags")).get("tags"))
            )
            if urn
        ],
        "upstream_dataset_urns": _upstream_dataset_urns(payload),
        "column_names": [
            field["fieldPath"]
            for field in _as_list(schema.get("fields"))
            if isinstance(field, dict) and field.get("fieldPath")
        ],
        # Verbatim source identifiers. Deliberately not interpreted here --
        # see `_CROSS_SOURCE_WARNING`.
        "custom_properties": _custom_properties(properties),
        # `status: null` means the instance never wrote a Status aspect, which
        # is not the same claim as "removed: false" -- so it stays None.
        "removed": _as_dict(payload.get("status")).get("removed"),
    }
    return ObservedAssetInput(
        external_id=_require_urn(payload, "dataset"),
        asset_type="dataset",
        raw_payload=payload,
        normalized=normalized,
        links=_dataset_links(payload),
        hash_basis=_HASH_BASIS,
        source_modified_at=_source_modified_at(payload),
    )


def _normalize_domain(payload: dict) -> ObservedAssetInput:
    properties = _as_dict(payload.get("properties"))
    return ObservedAssetInput(
        external_id=_require_urn(payload, "domain"),
        asset_type="domain",
        raw_payload=payload,
        normalized={
            "name": properties.get("name"),
            "description": properties.get("description") or "",
            "owner_urns": _owner_urns(payload),
        },
        links=[
            UnresolvedLink(kind="corp_user", target_external_id=urn, relation="owned_by")
            for urn in _owner_urns(payload)
        ],
        hash_basis=_HASH_BASIS,
    )


def _normalize_corp_user(payload: dict) -> ObservedAssetInput:
    properties = _as_dict(payload.get("properties"))
    return ObservedAssetInput(
        external_id=_require_urn(payload, "corp_user"),
        asset_type="corp_user",
        raw_payload=payload,
        normalized={
            "username": payload.get("username"),
            "display_name": properties.get("displayName"),
            "email": properties.get("email"),
            "title": properties.get("title"),
            "active": properties.get("active"),
            "status": payload.get("status"),
        },
        hash_basis=_HASH_BASIS,
    )


def _normalize_glossary_term(payload: dict) -> ObservedAssetInput:
    properties = _as_dict(payload.get("properties"))
    domain_urn = _domain_urn(payload)
    links = [
        UnresolvedLink(kind="corp_user", target_external_id=urn, relation="owned_by")
        for urn in _owner_urns(payload)
    ]
    if domain_urn:
        links.append(
            UnresolvedLink(kind="domain", target_external_id=domain_urn, relation="in_domain")
        )
    return ObservedAssetInput(
        external_id=_require_urn(payload, "glossary_term"),
        asset_type="glossary_term",
        raw_payload=payload,
        normalized={
            "name": properties.get("name") or payload.get("name"),
            # The business definition itself: source evidence for the
            # curator, never approved meaning on its own.
            "definition": properties.get("definition"),
            "term_source": properties.get("termSource"),
            "source_ref": properties.get("sourceRef"),
            "hierarchical_name": payload.get("hierarchicalName"),
            "owner_urns": _owner_urns(payload),
            "domain_urn": domain_urn,
        },
        links=links,
        hash_basis=_HASH_BASIS,
    )


def _require_urn(payload: dict, asset_type: str) -> str:
    urn = payload.get("urn")
    if not urn:
        raise ConnectorError(f"DataHub {asset_type} payload has no urn")
    return str(urn)


class _EntitySpec:
    """How one DataHub entity type is discovered, read, and normalized."""

    __slots__ = ("asset_type", "normalize", "query", "root_field")

    def __init__(self, *, asset_type: str, root_field: str, query: str, normalize) -> None:
        self.asset_type = asset_type
        self.root_field = root_field
        self.query = query
        self.normalize = normalize


_SPECS: dict[str, _EntitySpec] = {
    "DATASET": _EntitySpec(
        asset_type="dataset",
        root_field="dataset",
        query=_DATASET_QUERY,
        normalize=_normalize_dataset,
    ),
    "DOMAIN": _EntitySpec(
        asset_type="domain",
        root_field="domain",
        query=_DOMAIN_QUERY,
        normalize=_normalize_domain,
    ),
    "CORP_USER": _EntitySpec(
        asset_type="corp_user",
        root_field="corpUser",
        query=_CORP_USER_QUERY,
        normalize=_normalize_corp_user,
    ),
    "GLOSSARY_TERM": _EntitySpec(
        asset_type="glossary_term",
        root_field="glossaryTerm",
        query=_GLOSSARY_TERM_QUERY,
        normalize=_normalize_glossary_term,
    ),
}

_COVERED_ASSET_TYPES = tuple(spec.asset_type for spec in _SPECS.values())


def projections() -> tuple[tuple[str, str, str, str], ...]:
    """`(entity_type, asset_type, root_field, query)` for every projection.

    Public so `docker/datahub/capture_evidence.py` records the queries this
    connector actually sends. A capture script holding its own copy of the
    query text would drift, and the recorded fixtures would stop being
    evidence of the real upstream contract.
    """
    return tuple(
        (entity_type, spec.asset_type, spec.root_field, spec.query)
        for entity_type, spec in sorted(_SPECS.items())
    )


def projection_fingerprint() -> str:
    """Stable digest of every projection this connector sends.

    Recorded on the checkpoint so the exact GraphQL contract a past sync read
    is reconstructable, and so narrowing a projection is visible as a change
    of contract rather than as the source having lost metadata.
    """
    digest = hashlib.sha256()
    for entity_type in sorted(_SPECS):
        digest.update(entity_type.encode())
        digest.update(_SPECS[entity_type].query.encode())
    return digest.hexdigest()


class DataHubConnector:
    """The `hyperset.connectors.types.Connector` contract over DataHub GraphQL.

    One transport only: `graphql`. There is no bundle mode, because DataHub's
    equivalent of an export is a metadata-ingestion recipe, a different
    contract entirely, and v0 does not guess at it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        timeout: int = 30,
        session: Any | None = None,
    ) -> None:
        if not base_url:
            raise ConnectorError("DataHubConnector requires base_url")
        self._client = DataHubGraphQLClient(
            base_url=base_url, token=token, timeout=timeout, session=session
        )

    @property
    def transport(self) -> str:
        return "graphql"

    def test_connection(self) -> ConnectionTest:
        try:
            version = self._client.app_version()
        except ConnectorError as exc:
            return ConnectionTest(ok=False, detail=str(exc))
        return ConnectionTest(
            ok=True, detail=f"DataHub {version or 'unknown version'} at {self._client.base_url}"
        )

    def snapshot(self, checkpoint: dict | None = None) -> ConnectorSnapshot:
        """One read of the source: discover every entity, then project the
        four covered types by URN.

        A full refresh every run. The checkpoint is a recorded watermark, not
        a cursor that skips reads -- v0 does not pretend to resume
        incrementally.
        """
        client = self._client
        app_version = client.app_version()
        discovered = client.scroll_entities()

        entities: dict[str, list[dict]] = {asset_type: [] for asset_type in _COVERED_ASSET_TYPES}
        unprojected: dict[str, int] = {}
        missing: list[str] = []
        for row in discovered:
            spec = _SPECS.get(row["type"])
            if spec is None:
                unprojected[row["type"]] = unprojected.get(row["type"], 0) + 1
                continue
            payload = client.entity(spec.query, spec.root_field, row["urn"])
            if payload is None:
                # Discovered by the search index but not served by GMS: a real
                # inconsistency, reported instead of being counted as an
                # observation or as a deletion.
                missing.append(row["urn"])
                continue
            entities[spec.asset_type].append(payload)

        volatile: set[str] = set()
        unordered: set[str] = set()
        for payloads in entities.values():
            for payload in payloads:
                volatile |= _keys_present(payload, _VOLATILE_KEYS)
                unordered |= _keys_present(payload, _UNORDERED_MAP_KEYS)

        fingerprint = projection_fingerprint()
        warnings = [
            "GraphQL returns only the fields a query selects, so this snapshot is lossless "
            f"with respect to this connector's projections (sha256 {fingerprint[:12]}) and "
            "not to DataHub's full aspect model; no field is inferred where GraphQL "
            "returned null",
            _CROSS_SOURCE_WARNING,
        ]
        if unprojected:
            warnings.append(
                _UNPROJECTED_ENTITY_TYPES_WARNING.format(
                    types=", ".join(
                        f"{name}={count}" for name, count in sorted(unprojected.items())
                    )
                )
            )
        if missing:
            warnings.append(
                "discovered by search but not served by GMS at read time, so neither observed "
                f"nor treated as deleted: {', '.join(sorted(missing))}"
            )
        if volatile:
            warnings.append(
                "excluded from change detection as DataHub ingestion bookkeeping (retained in "
                f"the stored payload): {', '.join(sorted(volatile))}"
            )
        if unordered:
            warnings.append(
                "key-sorted for change detection because the source serialized these upstream "
                "maps as lists without a stable entry order (the served order is retained in "
                f"the stored payload): {', '.join(sorted(unordered))}"
            )

        asset_counts = {
            asset_type: len(payloads) for asset_type, payloads in sorted(entities.items())
        }
        return ConnectorSnapshot(
            source_version=app_version,
            bundle=entities,
            warnings=warnings,
            transport="graphql",
            covered_asset_types=_COVERED_ASSET_TYPES,
            source_capabilities={
                "base_url": client.base_url,
                "read_transport": "graphql_scroll_and_entity",
                "application_version_disclosed": app_version is not None,
                "projection_fingerprint": fingerprint,
                "projected_entity_types": sorted(_SPECS),
            },
            checkpoint={
                "transport": "graphql",
                "read_mode": "full_refresh",
                "source_version": app_version,
                "projection_fingerprint": fingerprint,
                "asset_counts": asset_counts,
                "discovered_entity_count": len(discovered),
                "unprojected_entity_types": dict(sorted(unprojected.items())),
                "resumed_from_projection_fingerprint": (checkpoint or {}).get(
                    "projection_fingerprint"
                ),
            },
        )

    def normalize(self, snapshot: ConnectorSnapshot) -> Iterator[ObservedAssetInput]:
        # Referenced entities (domains, users, terms) are yielded before the
        # datasets that link to them, so first-sync link resolution does not
        # depend on `run_sync` deferring its checks.
        for entity_type in ("DOMAIN", "CORP_USER", "GLOSSARY_TERM", "DATASET"):
            spec = _SPECS[entity_type]
            for payload in snapshot.bundle.get(spec.asset_type, []):
                yield spec.normalize(payload)
