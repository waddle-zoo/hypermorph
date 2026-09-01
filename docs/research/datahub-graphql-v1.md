# DataHub OSS GraphQL: the contract Hyperset's second connector reads

> **Checked version:** DataHub OSS **v1.6.0**, reported by the running
> instance itself as `{"acryldata/datahub": {"version": "v1.6.0", "commit":
> "059a36c0b035a6057de00114ccac0ea9003d6bc2"}}` from `GET /config`,
> verified 2026-07-27.
>
> **Scope:** the GraphQL API that `acryldata/datahub-gms:v1.6.0` serves at
> `POST /api/graphql`, limited to what the shared revenue scenario needs:
> entity discovery, and the dataset / domain / corp-user / glossary-term
> projections. Mutations, the React frontend's own queries, the metadata
> ingestion framework, and DataHub Cloud are out of scope.

Every claim below was checked by querying the running pinned instance, not
read off upstream documentation. Where the two disagreed, the instance won,
and the disagreement is recorded.

DataHub is the second source in [ADR 0010](../docs/adr/0010-two-source-evaluation-loop.md)'s
two-source loop. It is complementary to Superset rather than overlapping:
Superset supplies BI assets and analytical configuration, DataHub supplies
catalog identity, domain membership, ownership, glossary terms, and lineage.
Neither is approved business truth.

## 1. GraphQL is a projection, so "lossless" needs a narrower definition

A REST detail body arrives with every field the server chose to include. A
GraphQL response contains *exactly* the fields the query selected and nothing
else. So a GraphQL connector cannot claim to be lossless with respect to the
source's full model; it can only be lossless with respect to its own query
document.

Hyperset handles that by making the projections explicit rather than
incidental:

- every query lives as a named constant in
  `hyperset/connectors/datahub/connector.py`;
- `projection_fingerprint()` is a SHA-256 over all of them, recorded on each
  snapshot's `source_capabilities` and `checkpoint`;
- each snapshot carries a warning stating the projection is the boundary of
  losslessness;
- `docker/datahub/capture_evidence.py` records fixtures using
  `projections()` from the connector itself, so the checked-in evidence
  cannot drift away from the queries the connector really sends.

A narrowed projection therefore shows up as a changed fingerprint on the next
sync, instead of looking like the source lost metadata.

## 2. Shapes the pinned build forced

These were all found by getting them wrong first against the live instance:

| Field | v1.6.0 reality | Consequence |
|---|---|---|
| `CorpUser.status` | enum leaf `CorpUserStatus`, not an object | a subselection is a `ValidationError`; selected bare |
| `DatasetProperties.created` | `Long` leaf | selected bare, while sibling `lastModified` *is* an `AuditStamp` object |
| `Dataset.upstreamLineage` | does not exist on the GraphQL type | upstreams read via `lineage(input: {direction: UPSTREAM})`, which returns `DownstreamOf` edges |
| `getEntityCounts` | requires a non-null `input.types` | cannot be used to ask "what types exist?"; rejected with HTTP 200 + a `DataFetchingException` |
| `scrollAcrossEntities(input: {types: null})` | returns every entity type | used for discovery, so completeness is evidence-based rather than a hardcoded expectation |
| `appConfig.appVersion` | returns `"v1.6.0"` | unlike Superset, DataHub *does* disclose its application version, so `source_version` is real evidence, never `None` |

## 3. Absence versus failure

DataHub answers "no such metadata" and "I could not read that" in ways that
are easy to conflate, and conflating them would let a failed sync imply
deletion. The three cases the connector distinguishes:

1. **HTTP 401/403** — `ConnectorAuthError`. Token auth is deployment
   configuration (`METADATA_SERVICE_AUTH_ENABLED`); an authorization failure
   must fail the run, never read as absence.
2. **HTTP 200 with a non-empty `errors` array** — GraphQL's normal partial
   failure. Raised as `ConnectorError`. A sync that accepted the accompanying
   `null` would record "the source has no such metadata" when in fact the
   read failed.
3. **HTTP 200, no `errors`, `data.<field> == null`** — genuine absence. Only
   this case is treated as "the instance does not serve that entity", and when
   discovery listed the URN it is reported as a warning: observed as neither
   an asset nor a deletion.

Separately, a field the query selected that comes back `null` is recorded as
`null`. `Dataset.status` null means no Status aspect was ever written, which
is not the same claim as `removed: false`, so `normalized["removed"]` stays
`None`. `properties.lastModified.time` is `0` for aspects written without an
audit stamp; mapping that to 1970-01-01 would fabricate a modification time,
so `source_modified_at` stays `None` unless the value is positive.

## 4. Identity

DataHub URNs are used verbatim as `ObservedAsset.external_id`:

```text
urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.public.finance_orders_daily,PROD)
urn:li:domain:revenue
urn:li:corpuser:revenue_owner
urn:li:glossaryTerm:recognized_revenue
```

A URN already encodes platform, name, and fabric. Parsing one apart to
rebuild a key would invent a second identity scheme that could disagree with
the source, so nothing in the connector does it.

## 5. Cross-source mapping is not the connector's job

The seeded Superset-side dataset carries Superset's own UUID in DataHub's
`customProperties`:

```json
{"key": "superset_dataset_uuid", "value": "ae48881d-334f-54a7-94e8-1ffcc73866e2"}
```

That is the same UUID Superset's REST payload serves, so an explicit
identifier match is available. The connector records it verbatim and stops
there — resolving it against a Superset-observed asset is a governance
decision made downstream, and a matching display name is a review candidate,
never a merge. Each snapshot says so in a warning rather than leaving the
boundary implicit.

## 6. Change detection and the one-change property

Glossary term definitions are projected on the glossary-term entity only;
datasets reference terms by URN alone. That is deliberate: editing one
definition upstream then produces exactly one immutable version and one
`ConnectorChange`, instead of restating every dataset that carries the term.

`lastIngested` is excluded from the change basis as ingestion bookkeeping —
the same role `*_humanized` plays for Superset — while remaining in the
stored payload. It was `null` on the seeded instance, because the seed writes
through the OpenAPI v3 entity endpoints rather than the ingestion framework.

**`customProperties` entry order is not stable across rewrites.** Found the
hard way: re-running the idempotent seed returned the Superset-side dataset's
three custom properties in a different order with identical content, which
would have made a no-op resync look like a real change. `customProperties` is
a map upstream that GraphQL serializes as a list, so the change basis sorts it
by key while `raw_payload` keeps the order the source served.

That sorting is deliberately narrow — only keys observed to reorder. Sorting
every list would also reorder `schemaMetadata.fields`, where column order is
real information, and would hide genuine reordering elsewhere. Owner, term,
and tag lists are semantically unordered too, but none was observed to
reorder, so none is sorted: the rule follows evidence, not symmetry.

Restoring the drifted definition produced a response byte-identical to the
baseline, since DataHub returned no volatile per-request field to re-render.
So `tests/fixtures/datahub/v1.6.0/revenue/` holds two captures, not three: a
`restored` capture would have duplicated `baseline` exactly and asserted
nothing.

## 7. The pinned local stack

`docker-compose.yml`'s `datahub` profile derives from DataHub's own
quickstart compose at tag v1.6.0
(`docker/quickstart/docker-compose-without-neo4j.quickstart.yml`), with three
reductions:

1. **Service names are `datahub-` prefixed.** The compose file already owns a
   `postgres` service, so every upstream host reference is rewritten to the
   prefixed name rather than relying on `hostname:` resolution.
2. **`datahub-frontend-react` and `datahub-actions` are not started.**
   Hyperset reads GMS's GraphQL API directly; the React UI and the actions
   framework serve neither the sync nor its tests, and this stack has to
   coexist with the Superset demo profile inside one Docker memory budget.
3. **Upstream's `kafka-setup` job is not started.** It only pre-creates
   topics when `DATAHUB_PRECREATE_TOPICS=true`, which quickstart itself
   defaults to false, and it publishes no image past v1.2.0 — so at v1.6.0
   there is nothing to pin and nothing for it to do. `datahub-upgrade`
   therefore waits on schema-registry directly.

Everything else (env, healthchecks, JVM sizing, the `SystemUpdate` bootstrap
job) is upstream's, unchanged.

One non-obvious operational fact: **GMS refuses to start without a
token-service signing key even when `METADATA_SERVICE_AUTH_ENABLED=false`.**
`DataHubTokenServiceFactory.validate` throws
`authentication.tokenService.signingKey must be set and not be empty`. The
local stack therefore sets `DATAHUB_TOKEN_SERVICE_SIGNING_KEY` and
`DATAHUB_TOKEN_SERVICE_SALT` to documented throwaway values; outside local
dev they are real secrets, because they mint API tokens.

## 8. What this proved about a connector SDK

With two real sources implemented, the only abstraction they turned out to
share is the three-member `Connector` protocol in
`hyperset/connectors/types.py`: snapshot the source, normalize what was read,
name the transport. Auth, discovery, pagination, identity extraction, and
link shapes generalized between Superset and DataHub not at all — REST JWT
login versus an optional bearer token, offset pages versus scroll cursors,
UUIDs versus URNs, `database_uuid` versus lineage edges.

So no connector SDK was extracted. `run_sync` is source- and
transport-neutral and knows no product name; everything source-specific
stays inside each connector package. A third connector, not a second, is
what would justify more.

## 9. Not v0

DataHub write-back, all entity types, DataHub Cloud, a generic catalog SDK,
and broad version compatibility are all out of scope. Support is claimed for
the exact pinned v1.6.0 tuple the tests exercise and nothing wider.
