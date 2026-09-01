# 0037: Tenant/workspace isolation — an additive, fail-closed workspace dimension

Status: ACCEPTED — the realization of the per-tenant isolation ADR-0036 foreshadowed
("per-authority-target and per-tenant isolation are added as first-class scope
dimensions (no cross-target or cross-tenant leakage)"). Directed by the mayor
(hq-t6nx, 2026-08-22) as slice 12 of the auth-hardening / multi-Git-repo track.

Extends ADR 0030 (the authorization boundary): workspace is the OUTERMOST level of
the same `Scope`/`Resource` superset seam that already carries
`domain`/`source_ref`/`field`. It governs WHICH TENANT's config a caller may see or
manage; it never grants approval authority (that is a human Git merge, ADR 0012).

It adds no served operation and moves no served field. Workspace is a
TRANSPORT-boundary identity concern (a `Principal` claim) and a data-layer row
partition; it is not an MCP tool input, is off `RESOLVE_PATH_OPERATIONS`, and the
admin surface it enforces on is not part of the served bundle contract. So
`tools_hash` and `SCHEMA_VERSION` are unaffected — the same reason the admin
config/reconcile surfaces do not move them.

## Decision

1. **Additive dimension, single implicit default.** A `workspace_id` column
   (`NOT NULL`, server default `'default'`) partitions `context_sources`,
   `context_writeback_config`, and `connections`. The migration backfills every
   existing row to `'default'`, so a single-tenant estate is byte-identical: it is
   the one implicit `'default'` workspace. Identity uniqueness widens to include the
   workspace (`uq_context_source_identity` becomes
   `(workspace_id, repository, ref, path)`; the write-back routing-key uniqueness
   becomes `(workspace_id, routing_key)`), so two tenants may hold the same source
   pointer or route the same domain without colliding.

2. **The Principal carries the workspace, fail-closed to a concrete value.**
   `Principal.workspace` is derived from a configured OIDC claim
   (`HYPERSET_OIDC_WORKSPACE_CLAIM`). If the claim is not configured, or the token
   omits it, or it is not a non-empty string, the caller acts in `'default'` — a
   single CONCRETE workspace, NEVER a wildcard/all-workspaces value. A missing claim
   therefore can only NARROW a caller to the one implicit workspace; it can never
   widen one to another tenant's data. (Distinct from the roles claim, whose
   fail-closed value is NO roles: `'default'` is not authority, it is a partition
   every caller has.)

3. **The Scope/Resource seam gains a workspace level.** `Scope.workspace` and
   `Resource.workspace` are the outermost level of `Scope.covers`; `None` means
   "any workspace", so every grant defined before this slice (`Scope()`) is
   unchanged. A deployment narrows a role to one tenant by setting it. Every op's
   authorization `Resource` carries the caller's workspace, so the read gate is
   tenant-aware uniformly (a workspace-scoped grant denies a sibling tenant; the
   pre-slice all-workspace grants keep `workspace=None` and are unaffected).

4. **Fail-closed enforcement, this slice.** Enforced server-side:
   - **Admin list/manage** of context sources, write-back targets, and connections:
     the handler passes the verified principal's concrete workspace to the
     repository, which filters lists by `workspace_id` and treats a by-id row in
     another workspace as ABSENT (NON-DISCLOSING — the same 404/None as one that does
     not exist, so existence never leaks across tenants).
   - **Write-back `get_by_routing`**: a proposal routes only WITHIN the proposing
     caller's workspace — an enabled keyed target of that workspace, else that
     workspace's default target, else fail closed. A target in another tenant is
     never eligible, so a proposal never crosses tenants.
   - **Served READ consumers**: `list_context_catalog`, `discover_analytics_context`,
     `expand_analytics_context`, and context `history` scope their source reads to the
     caller's workspace, so a tenant sees only its own governed domains.
   - **`validate_analytics_plan`**: its INTERNAL resolve is workspace-scoped, so a
     validation bundle is never built from a sibling tenant's source. (The public
     RESOLVE op's DATA scoping stays deferred — see Consequences — but VALIDATE is
     inside this slice's all-consumers boundary.)
   - **Identity resolution fails closed**: identity is `(workspace, repository, ref,
     path)`. A tenant-sensitive caller passes an explicit `workspace` and resolves
     exactly one row. A workspace-LESS `get_source_by_identity` over a pointer two
     tenants share raises `AmbiguousIdentityError` (a `NotFoundError` subclass, so
     existing handlers degrade cleanly) instead of the datastore's unhandled
     `MultipleResultsFound` — it never silently picks a tenant or crashes.
   - **Observed-evidence / connection enumeration is scoped BY CONSTRUCTION.** The
     enumeration reads at the connection/observed repository layer —
     `PostgresConnectionRepository.list`, `PostgresObservedAssetRepository.list_all`
     and `.count_by_type` — take a REQUIRED `workspace` with no silent global default.
     A concrete tenant scopes; the explicit `ALL_WORKSPACES` sentinel (a distinct type,
     `hyperset.repositories.scope`) is the only cross-tenant read, reserved for SYSTEM
     callers (the CLI, ops health overviews, the connector sync loop, the raw-metadata
     eval arm) and written out at each such site. Every served observed consumer —
     `ObservedEvidenceResolver` (context sync AND the resolve/VALIDATE evidence path),
     the catalog `_observed` counts, the gather candidate scan, and the resolver's
     observed-relationship / references-into helpers — passes the caller's workspace, so
     a tenant's sync can never link or persist another tenant's observed asset when
     connector-native ids overlap, and a tenant's catalog never carries another's
     connection id or count. Forgetting the argument is a `TypeError`, not a leak, so a
     NEW consumer of connection/observed state is scoped by construction and this class
     cannot silently regress.
   Each tenant has its OWN default write-back target (found-or-created per
   workspace); the `'default'` workspace keeps the migrated legacy singleton id, so
   a single-tenant estate is unchanged.

5. **Backward-compatible internal seam.** Context-source / write-back / connection
   BY-ID lookups used by internal / serving / reconcile paths accept `workspace=None`
   ("no filter"), so those paths are unchanged until their own scoping follow-ons land.
   The connection/observed ENUMERATION reads are the exception (point 4): they are
   fail-closed by construction, and a serving path that must read estate-wide (the
   deferred RESOLVE gather) says so explicitly via `resolve_workspace_scope(None) ->
   ALL_WORKSPACES` at the repo boundary rather than relying on a silent default.
   Tenant-sensitive
   callers (admin handlers, `get_by_routing`, reconcile, history, validate) always
   supply a concrete workspace; the isolation guarantee is that the service layer
   supplies the tenant and the tests flip-verify it. A workspace-less identity lookup
   that becomes ambiguous fails closed (point 4) rather than serve a wrong tenant.

## Consequences

- Single-tenant deployments (the common case today) are byte-identical: no claim
  configured → everyone is `'default'` → the migration's backfill makes every
  existing row visible exactly as before.
- A multi-tenant deployment configures `HYPERSET_OIDC_WORKSPACE_CLAIM`; each
  tenant's admins see and manage only their own sources/targets/connections, and a
  proposal routes only within the proposer's tenant.
- Deferred (filed follow-ons, so this PR stays single-purpose): the public
  RESOLVE op's DATA scoping (its authz `Resource.workspace` is populated, but the
  served governed bundle is still gathered estate-wide — VALIDATE, which is inside
  this slice, is scoped; RESOLVE is not), review-task/reconcile DATA workspace
  scoping (review tasks carry no workspace yet; reconcile resolves its routed
  target's own workspace), and a per-workspace default-target admin UI.

## Acceptance (real Postgres)

- A workspace-A admin is DENIED workspace-B's connections/targets/sources —
  flip-verified both directions; a cross-workspace manage is a non-disclosing 404.
- `get_by_routing` routes within a workspace only and fails closed for a tenant with
  no target.
- A missing/blank workspace claim resolves to `'default'` (never all-workspaces).
- Existing rows migrate to `'default'` and remain visible to a default-workspace
  caller.
- Two tenants may govern the same domain without a cross-tenant collision, and the
  served READ consumers (catalog/discover/expand/history) return only the caller's
  tenant's domains; a tenant that governs nothing sees an empty catalog.
- `validate_analytics_plan` for a tenant that does not govern the named domain does
  not validate against a sibling tenant's source.
- Two tenants may register the same `(repository, ref, path)` pointer; a scoped
  identity lookup resolves each tenant's own row, and a workspace-less lookup over
  the shared pointer fails closed with `AmbiguousIdentityError` (never a 500).
- Two tenants may observe an asset with the same connector-native external id; a
  scoped evidence resolve links only the caller's own asset, and the explicit
  `ALL_WORKSPACES` read sees both and refuses as ambiguous.
- A tenant's catalog `observed` list carries only its own connections/counts.
- The connection/observed enumeration reads reject a missing workspace with a
  `TypeError` (scoped by construction); only `ALL_WORKSPACES` spans tenants.
