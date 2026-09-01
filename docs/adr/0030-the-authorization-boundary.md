# 0030: An authorization boundary — a caller proves identity at the transport, and a fail-closed reader gate governs every served operation

> **Amended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** The fail-closed gate and the `(domain, source_ref, field)` scope cover KG-backed sources identically; per-authority-target and per-tenant isolation are added as first-class scope dimensions (no cross-target or cross-tenant leakage). A backend's own ACLs are additive, never a substitute for this gate.

Status: ACCEPTED — ratified by Brandon via the Overseer (hq-rqq6, 2026-08-15):
"approve provider-neutral OIDC/SAML boundary, initial reader-all governed context,
extensible scoped grants, deny-whole/no existence leaks. Enable enforcement only after
the planned domain-shape isinstance guard, HTTPS-only JWKS validation, corrected
directive-domain mapping, and the required independent review union." Ratification
authorizes IMPLEMENTATION of the seam; it does NOT enable enforcement. The gate stays
behind `HYPERSET_AUTHZ_ENABLED`, DEFAULT-OFF, so a deployment is byte-identical until an
operator flips it, and flipping it on waits on Brandon's four named preconditions plus
the independent review union. Originally PROPOSED 2026-08-13 (Brandon ruled #78, and
directed this as the access model that ADR 0029's classification enforcement and #230's
per-caller access were deferred to). This ADR records the verified transport seam, the
authz schema, and the acceptance-test list; the gate and the in-process principal
landed default-off in hy-ac2x, and the live bearer verifier + transport threading in
hy-lrho.

Extends ADR 0012 (authority is a human Git merge; this governs who may READ the
snapshot, never who may approve it), ADR 0019 (assist may reason, governance may
not), and ADR 0029 (facet enforcement folded into #230). It adds no served
operation and moves no served field: authorization is a TRANSPORT-boundary concern
threaded to the shared executor, never an MCP tool input, so `tools_hash`
(`sha256:fe930a003b731211`) and `SCHEMA_VERSION` (16) are unaffected. The proposed
typed schema ships as an unwired stub under `hyperset/security/`, imported by
nothing on the resolve path, so it cannot move `tools_hash` either — the same
reason serving DISCOVER and the review ops does not (they sit off
`RESOLVE_PATH_OPERATIONS`).

> [!NOTE]
> **Implementation amendment — 2026-08-30.** The “What this slice delivers”
> section below is the historical boundary of the original ADR slice, not current
> implementation status. The RS256/HTTPS-only JWKS verifier, bearer identity at
> the HTTP and hosted-MCP boundaries, Authorization-Code + PKCE browser flow,
> `/login`/`/callback`/`/logout`, signed session cookie, nonce/state binding, and
> subject-bound CSRF protection have shipped and are tested. They remain
> present-but-default-off until a deployment configures and enables OIDC. The
> remaining #78 work is the reviewed per-principal grant source and scoped policy
> (F1–F5, `hy-tjow`) plus a live configured-OIDC smoke. SAML/gateway integration
> and broader SSO are still future work. No Hyperset-local username/password
> login exists.

## Context

The HTTP read API is unauthenticated. It binds loopback by default (hy-voes,
`http.py`) precisely because it publishes governed context with no caller check;
the default is a mitigation, not a boundary. The surface gates that exist
(`http.py`, the writeback-config admin/public split) explicitly disclaim being
authentication and name the residual as hy-2nqb. ADR 0029's structural
`classification_undisclosed` check deliberately made NO access decision, deferring
"whether a restricted/pii payload may enter a bundle FOR A GIVEN CALLER" to this
model. #78 is that model.

## The measured seam (do not assume a library)

The ruling required verifying the real integration point rather than assuming a
Flask/Superset auth stack. Measured on this tree:

- **There is no web framework in the read path.** The HTTP transport is the Python
  standard library: `ThreadingHTTPServer` + `BaseHTTPRequestHandler` (`http.py`).
  It is NOT Flask, NOT Flask-AppBuilder, NOT authlib. Its only request-header reads
  today are `Content-Length`/`Transfer-Encoding` framing — no `Authorization`,
  cookie, or identity is read anywhere.
- **The hosted MCP transport is a Starlette + uvicorn ASGI app** (`mcp.py`,
  `streamable_http_app`/`serve_streamable_http`), behind the optional `mcp` extra.
  MCP-over-stdio is a spawned local subprocess whose only trust is OS process
  ownership — it has no network caller identity. So authorization on MCP is
  meaningful only on the hosted transport; stdio's principal is the spawning
  process.
- **Superset is a separate service, not an in-process Flask app.** The Superset
  connector is an outbound HTTP client (`connectors/superset/rest.py`); Hyperset
  shares no security manager with it. "Superset-compatible" therefore means
  integrating the SAME upstream identity provider a Superset deployment already
  uses (OIDC/SAML), NOT reusing Superset's Flask-AppBuilder security. Rewriting the
  stdlib handler onto Flask-AppBuilder to borrow that security manager is the
  largest possible change for zero reuse and is rejected.
- **The pieces to build on already exist as core dependencies.** `pyjwt` is a core
  dep, used today to MINT an RS256 JWT for the GitHub App (`security/github_app.py`)
  — the same library VALIDATES an inbound OIDC token, inverted. `requests` (core)
  fetches a provider's JWKS. `security/secret_box.py` (AES-256-GCM, KEK from
  `HYPERSET_SECRET_KEY` env, never the DB, fail-closed `SecretBoxError`) is the
  precedent for storing an IdP client secret encrypted or by reference.
- **One shared executor governs the whole served surface.** Every served operation
  on BOTH transports flows through `run_operation(name, params, *, session_factory)`
  (`transport/operations.py`): HTTP calls it for agent and review routes, MCP calls
  it from the tool handler. `OPERATIONS` = catalog, discover, resolve, validate, expand,
  and the review ops. `run_operation` takes no caller identity today.

## Decisions

### 1. Authentication is a provider-neutral, standards-based transport boundary

A caller proves identity with an upstream IdP over OIDC or SAML; Okta is the
reference provider, and no provider is hard-coded. Two shapes are viable against the
measured stack, and a deployment may use either:

- **Gateway-terminated (recommended, and required for SAML).** An authenticating
  reverse proxy / API gateway terminates OIDC/SAML with the customer's IdP and
  forwards a verified identity — a signed JWT or a trusted, signed header. Hyperset
  verifies that assertion at its boundary. SAML has no practical in-process path in
  a stdlib server, so SAML deployments use this shape.
- **Hyperset-verified bearer JWT.** The caller presents an OIDC access/ID token;
  Hyperset validates it with PyJWT against the issuer's JWKS (issuer + audience +
  signature + expiry), provider-neutrally. This is self-contained for the HTTP read
  API and the hosted MCP transport.

Either way, an UNVERIFIED request never yields an identity. Roles come from the IdP
token claims or a Hyperset-side mapping keyed by the verified `sub`/groups.

### 2. Identity threads into the one shared executor; it is never a tool parameter

The verified identity is threaded from each transport boundary into
`run_operation` (a new executor parameter), so a single fail-closed decision there is
inherited by HTTP and hosted MCP alike and covers the whole `OPERATIONS` surface. Two
boundaries pair with it: a handler-entry check at the top of `do_GET`/`do_POST` for
the HTTP handler's non-operation routes (writeback-config set, operator status), and
Starlette/ASGI middleware around the hosted MCP app. A THIRD `run_operation` caller
is the in-process `InProcessExecutor` on the evaluation path (`planner/executor.py`):
it is not a remote request, so the wiring slice gives it a system principal rather
than a token, and the mandatory gate must not strand the eval harness. Because identity rides the
transport and the executor signature — never an MCP tool's `input_schema` —
`tools_hash` does not move.

### 3. Authorization is role-based now, and extensible by construction

The initial authorization is a single `reader` role that may READ all governed
context — the enabled context-source snapshots (domains) `list_context_catalog`
enumerates, the same set a resolve can reach. The schema is NOT a flat role list; it
is `Principal → roles → Role → grants → Grant(effect, action, Scope, conditions)`,
where a `Scope` of `(domain, source_ref, field)` with `None` meaning "any" widens a
reader grant to everything today and NARROWS to per-domain, per-source, or per-field
tomorrow by setting those fields, and a `Grant` may carry policy `Condition`s. No
schema break is needed to go from "reader reads all" to "analyst reads only domain
revenue" or "reads revenue only after 2026": the same shapes carry it. A finer grant
is only MEANINGFUL, though, once the enforcement seam authorizes at the granularity
of what a response would return (Decision 4): a coarse whole-domain read that would
surface a source or field a finer DENY forbids must itself be denied, so a
source- or field-scoped grant is not bypassable by asking for the whole domain.
`Scope.covers` in the stub is deliberately the safe SUPERSET direction (a
whole-domain grant covers a field request, never the reverse); reconciling a coarse
request against a finer DENY is the enforcement seam's job, not `covers`'s.

### 4. Fail-closed: an unauthorized caller gets a complete, uniform, non-disclosing denial

Default-deny. No identity, an invalid/expired token, no role, or no matching ALLOW
grant is a COMPLETE denial: no bundle, no partial bundle, no provenance, no catalog
entry, no restricted-resource reference. Deny wins over allow. Authorization is
evaluated at the granularity of what a response WOULD return: the enforcement seam
decomposes an operation's result into its constituent resources (domain, source,
field) and authorizes each, and a DENY on ANY constituent denies the WHOLE
operation. It never strips the forbidden part and serves the rest — a stripped
bundle is itself a partial bundle this decision forbids and a disclosure that the
part exists. The denial is UNIFORM
— identical for a resource that exists and one that does not — so it discloses
nothing about what exists, modeled on the existing non-disclosing error contract
(`OperationError.to_dict()` returns only `{code, message, recovery}`; the
internal-error path scrubs host/driver detail). Any verification failure REFUSES with
no fallback to an unauthenticated path, exactly as ADR 0027's write-back auth fails
closed.

### 5. Secrets are validated, not minted or stored in the clear

An IdP client secret, if a deployment needs one, is stored by reference (env name) or
encrypted at rest via `secret_box` (KEK from env), never returned and never logged.
Inbound tokens are VALIDATED and discarded, never persisted — the inverse of
`github_app.py`'s per-op mint. A verified identity is derived per request and not
stored.

## What this slice delivers, and does not

Delivers (design, returnable to Brandon): this ADR; a typed, UNWIRED authz schema
stub under `hyperset/security/authz.py` (the `Principal`/`Role`/`Grant`/`Scope`/
`Condition`/`Resource`/`Decision` shapes and a pure, fail-closed `authorize()`
decision function), so the acceptance list is executable rather than prose; and the
acceptance-test list below.

Does not build (later slices, gated on this ADR's ratification): the OIDC/JWKS
verifier, the SAML/gateway trust configuration, the transport-boundary middleware and
the `run_operation` identity thread, any live enforcement, and the classification
identity-gating consumer (ADR 0029). Nothing here authenticates or denies a real
request; the stub enforces nothing and is imported by no served path.

## Acceptance tests

The design is accepted when these hold (encoded as executable specs against the
schema stub, and to be re-checked end-to-end when the transport wiring lands):

1. **AuthN boundary.** A request with no token, an invalid signature, a wrong
   audience/issuer, or an expired token yields NO principal and a complete denial;
   only a fully verified token yields a `Principal`.
2. **Provider-neutral.** A principal minted from issuer A and one from issuer B
   authorize identically; no issuer string is special-cased (Okta is configuration,
   not code).
3. **Reader reads governed context.** A `reader` principal is allowed to read every
   governed domain the catalog enumerates (whole-domain and its sources/fields).
4. **No-role denial is complete and leak-free.** A principal with no matching grant
   receives an identical, opaque denial for an EXISTING and a NON-EXISTENT resource —
   no bundle, no provenance, no existence signal.
5. **Fail-closed default.** Absent policy, an unknown role, or an unknown/unsatisfied
   condition denies; a condition satisfied and scope matched allows; deny wins over a
   co-matching allow.
6. **Extensibility seam.** A per-domain grant restricts a reader to a subset of
   domains; a per-field grant narrows further — both without changing the schema.
7. **Both transports, one gate.** HTTP and hosted MCP inherit the same
   `run_operation` decision; MCP-over-stdio's local-process principal is defined and
   distinct.
8. **Invariant.** The schema stub is off the resolve path: `tools_hash` stays
   `sha256:fe930a003b731211` and `SCHEMA_VERSION` stays 16.

## Consequences

- **Blast radius, stated plainly.** As proposed and DESIGN-ONLY, this changes no
  runtime behaviour: the stub is unwired and the live path is untouched, so a
  clean-checkout deployment authenticates exactly as before (loopback, no gate) until
  an implementation slice lands. When implemented, the blast radius is every served
  operation on both transports gaining a mandatory fail-closed identity check — which
  is the intent.
- This closes the hy-2nqb "surface gate is not authentication" residual and supplies
  the access model ADR 0029 and #230 deferred to — but only once the follow-on
  implementation lands; the ADR alone enforces nothing.
- No approval boundary moves: authorization governs READ; a human Git merge remains
  the sole authority (ADR 0012).
- `tools_hash` and the MCP trust surface are unaffected — authorization is a
  transport boundary and an executor concern, not a served operation, and the schema
  stub is imported by nothing on the resolve path.
- No implementation lands against this ADR until Brandon ratifies the schema and the
  acceptance list.
