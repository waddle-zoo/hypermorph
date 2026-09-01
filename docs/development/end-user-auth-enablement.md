# End-user authentication & authorization -- enablement design (hy-7q3o, #78)

Status: DESIGN (2026-08-16, hy-7q3o). DESIGN-FIRST and SECURITY-SENSITIVE: this
records the path to make end-user authn/authz actually available and enforceable
for a NON-LOOPBACK production deployment, and decomposes it into impl slices. It
builds NOTHING and flips NOTHING. Every decision that (a) flips the default from
unauthenticated to authenticated, (b) picks or binds an IdP or wire contract, (c)
moves the served contract / `SCHEMA_VERSION` / `tools_hash`, or (d) sets a
security policy (domain-ACL semantics, service identity, token-acceptance rules)
is a **Brandon-level decision**: it is raised as a FORK below with options and a
recommendation and is NOT decided here. Governed by
[ADR-0030](../adr/0030-the-authorization-boundary.md) (enablement deferral).

> [!NOTE]
> **Implementation amendment — 2026-08-30.** This document preserves the
> original decomposition and fork rulings. Since that design, the OIDC/JWKS
> bearer verifier and authorization thread, PKCE/session/CSRF primitives, and
> `/login`/`/callback`/`/logout` route wiring have shipped. Therefore later
> statements that the UI has no login/session or that route wiring is still a
> follow-on are historical. Current remainder: reviewed per-principal grant
> source/scoped policy (F1–F5, `hy-tjow`) and a live configured-OIDC smoke.
> Auth remains present-but-default-off; no local username/password provider was
> added.

## 1. What is already shipped (measured on main `07af725`, not re-derived)

The substrate is largely built and inert; #78 is enablement, not construction.

- **A complete authorization decision model** (`hyperset/security/authz.py`):
  `Principal -> roles -> Role -> Grant(effect, action, Scope, conditions)`, a
  PURE `authorize()` that is fail-closed (deny unless an explicit ALLOW matches
  and no DENY matches -- deny wins), a uniform non-disclosing `Decision`, the
  `reader` role, and `SYSTEM_PRINCIPAL` (the trusted in-process identity, never
  from external input). `Scope`/`Condition` are the extensibility seams: a
  `None` scope level means "any", so per-domain / per-source / per-field grants
  and policy conditions are a matter of POPULATING roles, not a schema change.
- **A hardened OIDC bearer verifier** (`hyperset/security/oidc.py`):
  `verify_bearer` does RS256 with a strict `algorithms` allowlist (no `alg:none`,
  no HS256 confusion), `require: ["exp"]`, issuer+audience checks, HTTPS-only
  JWKS AND issuer, and refuses a JWKS redirect off `https` (`_HTTPSOnlyRedirectHandler`).
  `principal_from_bearer` is the transport-boundary seam; it returns `None` when
  the gate is off and never constructs a `Principal` in a transport.
- **The gate is wired at every served governed read.** `authorization_error` is
  the ONE decision-as-a-value; `run_operation` raises it before dispatch, and the
  non-executor `GET /v0/context/history` route fails with the SAME check
  (`http.py:262`) -- so there is one fail-closed decision and one non-disclosing
  denial, not two. Multi-domain requests DENY-THE-WHOLE on the first denied
  resource.
- **Default OFF, byte-identical.** With `HYPERSET_AUTHZ_ENABLED` unset the gate is
  a no-op and served bytes are unchanged. The denial uses the already-served
  `UNAUTHORIZED` operation-error code -- no new vocabulary.
- **The enable-gate is READY but not flipped.** [ADR-0030] defers enforcement to
  four preconditions tracked by **hy-nt89**: (1) isinstance-domains guard, (2)
  HTTPS-only JWKS incl. redirect, (3) corrected directive-domain mapping are
  LANDED; (4) an independent review union run at the exact head being enabled
  remains, by design, for the flip itself.

## 2. The gap #78 names (what enablement still needs)

Each item is a distinct slice; each substantive one is a FORK in section 5.

- **IdP-role mapping.** LANDED (slice hy-09hy, F2 resolved by overseer ruling
  hq-l4g2). `verify_bearer` now derives `Principal.roles` from the configured
  roles claim (`ROLES_CLAIM_ENV = "HYPERSET_OIDC_ROLES_CLAIM"`) via
  `_roles_from_claims`, fail-closed: a configured claim maps the token's role
  names (a JSON array of strings, or a space/comma-delimited string); the claim
  UNSET keeps the baseline `reader` (opt-out, byte-identical); a configured claim
  with no valid roles yields NO roles and is denied (least privilege, no silent
  reader fallback). Role NAMES are carried, not authorized -- `authz.authorize`
  resolves each against `ROLES`, and an unknown name grants nothing. The role
  VOCABULARY beyond `reader` (Explorer/Reviewer/Admin/Git-owner) is the next
  slice below; until it lands, a mapped non-`reader` name is authenticated but
  authorized for nothing.
- **A role/grant SOURCE and roles beyond `reader`.** `ROLES` is a single static
  `reader`. #78 wants scoped/domain-ACL roles (and the reviewer roles the audit
  named). WHERE grants are authored -- a static config, a Git-owned policy, env --
  and the ACL SEMANTICS are undecided.
- **Service identity.** A non-human caller (evaluation, a service-to-service call)
  needs a principal that is not an end-user token. `SYSTEM_PRINCIPAL` covers only
  in-process trust; a networked service identity is unspecified.
- **Session/login for an exposed UI.** The playground/admin surfaces have no
  login/session. A browser cannot send a bearer JWT it never obtained. (`/admin`
  write-path auth is separately **hy-2nqb**.)
- **Non-loopback safety wiring.** LANDED (slice hy-71mi, hardened by hy-w5ld). A
  non-loopback bind now REQUIRES the authz gate enabled AND a complete OIDC config,
  or the process REFUSES TO START, per
  [ADR-0035](../adr/0035-layered-deployment-configuration.md) section 5:
  `security/deployment.py:assert_network_bind_authenticated` is called from
  `cli.cmd_serve_http` and `cmd_serve_mcp --http` before the listener binds, and
  `config/safety.py:validate_deployment_safety` refuses `server.bind: all` without
  auth at config-load. This is the ROOT FIX for the admin-write exposure (writeback-config
  + propose): closing the network bind closes every unauthenticated route at once. An
  actual LOOPBACK bind is the ONLY unauthenticated path -- there is NO env override
  (overseer ruling hy-w5ld removed an earlier `HYPERSET_ALLOW_INSECURE_NETWORK_BIND`
  warn-and-allow hatch, because a shipped default that binds `0.0.0.0` LAN-published
  unauthenticated is exactly the P0 exposure). A genuinely network-reachable bind fails
  closed; the demo instead publishes its ports on `127.0.0.1` only and asserts that with
  the narrow `HYPERSET_LOOPBACK_PUBLISHED` signal (Option A, mayor ruling; a test binds the
  signal to a `127.0.0.1:` publish), so `make up-demo` starts and stays non-LAN-reachable.
  The blanket allow-insecure flag is NOT re-introduced. This slice covers ALL THREE of
  ADR-0035 Decision 4's listeners: the two network APIs that serve the admin writes
  (`serve http`, `serve mcp --http`, via `assert_network_bind_authenticated`), AND the
  verifier-less `playground/ui/app.py` proxy, which is LOOPBACK-ONLY -- a non-loopback
  `HYPERSET_UI_HOST` is always fatal (`assert_loopback_only`, called in `app.main` before
  the bind, hy-w5ld). The proxy is stricter than the APIs: no auth configuration opens a
  non-loopback bind for it, because it authenticates nothing itself and serves local
  endpoints (the SPA, `/demo/*`). The config slice **hy-ax9w** still threads the validated
  auth toggle through the loader.
- **The flip.** Setting `HYPERSET_AUTHZ_ENABLED` in a real deployment (unauth ->
  auth) plus precondition #4. BUILD NONE of this here.

## 3. The enablement path (ordered; each step's impact named)

Ordered so each slice is independently reviewable and none flips the default:

1. **IdP-role mapping** -- derive `Principal.roles` from the verified token's
   roles claim. LANDED (hy-09hy); token-acceptance (FORK F2) resolved by hq-l4g2.
   No served-shape change; still inert while the gate is off.
2. **Role/grant source + domain-ACL roles** -- a reviewed policy source and the
   scoped roles it defines, enforced through the existing `Scope`/deny-the-whole
   seam. Security-policy: ACL semantics (FORK F1). Reuses `UNAUTHORIZED`; no
   contract move.
3. **Service identity** -- a networked non-human principal. Security-policy +
   wire-contract (FORK F3).
4. **Session/login for exposed UIs** -- the browser path. Wire-contract +
   served-surface change (FORK F4); coordinate with hy-2nqb.
5. **Non-loopback safety wiring** -- LANDED (hy-71mi, hardened by hy-w5ld): a
   non-loopback bind fails closed without auth (fatal at startup / config-load); loopback
   is the only unauthenticated path and there is no override. Served-behavior at the
   deployment boundary (FORK F5 covers the flip it gates); hy-ax9w still threads the
   validated toggle through the loader.
6. **The flip** -- precondition #4 review union at the enable head, then set the
   flag as a reviewed change (FORK F5). BUILD NONE here.

## 4. Served-contract & security impact

- **`tools_hash` is UNAFFECTED.** The gate is an executor-level Python check, not
  a served directive parameter; it adds no tool and changes no input schema
  (`authz.py` docstring). Stays `sha256:fe930a003b731211`.
- **`SCHEMA_VERSION` does not move** for the gate: the denial is the already-served
  `UNAUTHORIZED` code, and default-off keeps every response byte-identical.
- **The FLIP is a served-BEHAVIOR change, not a schema change.** A read that was
  answered unauthenticated is, once enabled, a `401/403`/`UNAUTHORIZED`. That is
  the audit's biggest finding being fixed and is exactly the Brandon-level flip
  this pass does not perform.
- **Session/login (step 4) DOES add served surface** (routes, a session cookie,
  possibly a redirect/callback) -- a contract move that must be designed and
  reviewed on its own (FORK F4), not folded into the gate.
- **Non-disclosure is preserved:** every denial stays the uniform `UNAUTHORIZED`
  that leaks nothing about whether a resource exists (`Decision.reason` names the
  class, not the resource).

## 5. FORKS -- Brandon-level decisions (options + recommendation; NOT decided here)

Each is raised to the mayor; none is shipped in this pass.

**F1 -- Role/grant SOURCE and domain-ACL semantics.** Where do grants live and
what does a domain ACL mean? Options: (a) a static, checked-in policy file the
loader reads (simple, reviewable, but a redeploy to change access); (b) a
Git-owned policy alongside governed context (consistent with ADR-0012's
authority model, human-approved, auditable); (c) an env/inline map (fastest, but
policy in ambient config). RECOMMEND **(b) Git-owned policy**, enforced through
the existing `Scope`/deny-the-whole seam, because access to governed context
should be owned where the context's meaning is owned. NEEDS a ruling: ACL
granularity (domain only vs domain/source/field) and default-deny confirmation.

**F2 -- IdP-role mapping / token-acceptance rules.** RESOLVED (overseer ruling
hq-l4g2, 2026-08-18) and IMPLEMENTED in slice hy-09hy as the recommended option:
**(a) direct roles-claim mapping**. Accepted claim shape: a JSON array of strings
OR a single space/comma-delimited string. Unknown role names are carried but
denied (fail-closed, as `ROLES` already does); a configured claim with no valid
roles yields no roles (least privilege, denied); the claim unset keeps the
baseline `reader` (opt-out). Multi-issuer stays OUT of scope this slice -- the
verifier reads a single configured issuer (`ISSUER_ENV`); a second issuer is a
later change, not a silent widening.

**F3 -- Service identity.** RESOLVED (overseer ruling hq-l4g2) and IMPLEMENTED in slice
hy-87us as the recommended option: **(a) OIDC client-credentials** -- a service is just
another verified `Principal` on the SAME bearer path (no new verification code), carrying
a distinct `service` role. The service role's grants (the point the fork flagged) are
LEAST PRIVILEGE: read-only. A service holds no `review` grant (and no `configure` once
hy-2nqb lands), so a machine token can never author a proposal or configure the
deployment; distinct from the human roles so a deployment can scope/audit machines
separately. Default-OFF; no served-contract/`SCHEMA_VERSION` move.

**F4 -- Session/login wire contract for exposed UIs.** RESOLVED (overseer ruling
hq-l4g2): **(a) OIDC Authorization-Code + PKCE with a server-set session cookie**.
PRIMITIVES LANDED (hy-ysn1) in `security/login.py`, inert: PKCE, state/nonce, the
authorize-URL builder (HTTPS-only), a local-only return-to allowlist (no open
redirect), a signed HttpOnly/SameSite/Secure session cookie with
`principal_from_session` (mirrors `principal_from_bearer`, off while authz off),
and a subject-bound CSRF token -- all fail-closed and tested. STILL a served-surface
change that lands on its own review: the code->token exchange and the
login/callback/logout ROUTES + the login button are the route-wiring follow-on,
coordinated with hy-2nqb for the admin write-path.

**F5 -- The default flip (unauth -> auth) + precondition #4.** Options: (a) flip
globally via `HYPERSET_AUTHZ_ENABLED` once #4's review union is green at the
enable head; (b) flip only behind a non-loopback bind (config-gated via hy-ax9w),
keeping loopback dev unauthenticated; (c) stage per surface. RECOMMEND **(b)
config-gated at the non-loopback boundary**, so production is fail-closed while
local dev is unchanged. This pass BUILDS NONE of it. NEEDS Brandon's decision and
a reviewed flag-flip change.

## 6. Impl-slice decomposition (follow-on beads -- DESIGN-FIRST, build none)

Ordered; each blocked on its FORK ruling; none built here:

1. IdP-role mapping (F2) -- bead **hy-09hy**. LANDED (F2 resolved by hq-l4g2).
2. Role/grant source + domain-ACL roles (F1) -- bead **hy-dq0r**.
3. Service identity (F3) -- bead **hy-87us**. LANDED (client-credentials, read-only `service` role).
4. Session/login for exposed UIs (F4; with hy-2nqb) -- bead **hy-ysn1**. PRIMITIVES
   LANDED (`security/login.py`, inert); route-wiring is the follow-on.
5. Non-loopback safety wiring (feeds hy-ax9w) -- bead **hy-71mi**, hardened by
   **hy-w5ld**. LANDED: the fail-closed network-bind guard + config safety validation; the
   demo publishes on `127.0.0.1` and asserts the narrow `HYPERSET_LOOPBACK_PUBLISHED`
   topology (no blanket override); and the verifier-less UI proxy is loopback-only
   (`assert_loopback_only`) -- all three ADR-0035
   Decision 4 listeners covered.
6. The reviewed enable-flip + precondition #4 review union (F5; hy-nt89) -- bead **hy-ia9n**.

## 7. See also

- [ADR-0030](../adr/0030-the-authorization-boundary.md) -- the authorization boundary and enable-gate.
- [ADR-0035](../adr/0035-layered-deployment-configuration.md) -- the config layer that must expose the toggle safely (hy-ax9w).
- [Feature-parity audit](feature-parity-audit.md) -- the finding this bead is the tracked home for.
