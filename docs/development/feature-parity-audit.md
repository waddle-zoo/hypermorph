# Feature-parity audit: advertised vs actually running (V0, hy-w8q2)

Status: AUDIT (2026-08-16, revised 2026-08-17, hy-w8q2). An honest matrix of what
Hyperset ADVERTISES against what a **clean supported deployment can actually
demonstrate**. It is evidence-first: every row cites a code path and states whether
the capability is reachable in the default supported deployment. Honesty over
completeness -- a capability that a clean deployment cannot demonstrate is never
listed as working.

The 2026-08-17 revision closes the delta the audit still owed: it points section 2
at the authz **ENABLEMENT** track (hy-tjow / #78 / ADR-0030 forks F1-F5, design in
`docs/development/end-user-auth-enablement.md`) as distinct from admin write-path
auth (hy-2nqb); adds a row for the #125 `linked_evidence.conflicts` reconciliation
(two producers reach the resolve path but are empty in a clean demo; three are
landed-but-inert pending activation forks); and is now bound to code by BEHAVIORAL
tests (`tests/unit/test_feature_parity_audit_claims.py` plus a resolve-path
assertion in `tests/postgres/test_context_bundle.py`), so a change that contradicts
a load-bearing claim here reddens a test instead of rotting silently.

The 2026-08-30 identity-status amendment keeps authentication **PARTIAL** because
it is present-but-default-off and still lacks the reviewed per-principal grant
source plus a live configured-OIDC smoke. It corrects the older inventory:
OIDC/JWKS bearer verification, PKCE login/callback/logout, signed session, CSRF,
and transport authorization are shipped. Hyperset has no local username/password
login; the loopback demo bypasses authentication rather than creating a local user.

## What "default supported deployment" means here

The demo entrypoint is **`make up-demo`** (`Makefile:20`). There is no bare
`make playground` target. `make up-demo` brings up Postgres + migrations, the
Superset demo, and the `api` + `mcp-http` services, and prints:

- Chat playground: `http://localhost:8000/playground/`
- Review: `http://localhost:8000/review/`
- Admin settings: `http://localhost:8000/admin/`
- MCP (HTTP): `http://localhost:8010/mcp`

`make up-demo` sets `HYPERSET_PLAYGROUND_ENABLED=true`
(`docker-compose.demo.yml:9`); plain `make up` runs a headless API with the
playground OFF (`docker-compose.yml:118`). The deployed runtime uses the
server-side OpenAI settings from `.env`; Ollama is
reserved for the separate scheduled benchmark. The playground UI **is built
into the `api` image**: `docker/hyperset.Dockerfile` has a `node:22` stage that
runs `npm run build`, so both `make up-demo` and `make serve` (each
`docker compose up ... api`, `Makefile:200`) serve the image-built bundle. The
`make playground-ui` step (`Makefile:192`) is needed only when the server is run
**directly on the host** (a bare `hyperset serve http`, or the served-playground
unit test, CLAUDE.md hy-r8jd) with no `playground/ui/dist/` present: there
`http.py:1001` falls back to the source `index.html` (which references
`/src/main.jsx`) and the page is broken. No `make` target hits that path.

Legend:

- **WIRED** -- implemented and reachable in the default supported deployment.
- **PARTIAL** -- real code, but inert or limited by default (a flag off, a live
  dependency, or configure-before-use).
- **DEMO-ONLY** -- served only behind `HYPERSET_PLAYGROUND_ENABLED`.
- **PROPOSED-ONLY** -- a design/seam with minimal code; not a working feature.
- **PLANNED / ABSENT** -- advertised, little or no code behind it.

Mapped to the requesting rubric (hy-w8q2): WIRED = *implemented-and-wired*, PARTIAL
= *partially-implemented*, DEMO-ONLY = *demo-only*, PROPOSED-ONLY = *stubbed*,
PLANNED / ABSENT = *planned*.

## 1. Agent trust surface (HTTP `/v0/{op}` + MCP tools)

| Capability | Status | Evidence | Default-deployment note |
|---|---|---|---|
| `list_context_catalog` | WIRED | `operations.py` `_catalog`; in `RESOLVE_PATH_OPERATIONS` (`loop.py:43`) | Served on HTTP + MCP, always on |
| `resolve_analytics_context` | WIRED | `operations.py` `_resolve`; allowlisted | Served always; deterministic, no SQL |
| `validate_analytics_plan` | WIRED | `operations.py` `_validate`; allowlisted | Served always; never runs SQL |
| `discover_analytics_context` (assist) | PARTIAL | `operations.py` `_discover` -> `candidates.service`; served, NOT allowlisted | Needs the configured OpenAI embedding endpoint/key; 500s if absent |
| `expand_analytics_context` | WIRED | `operations.py` `_expand` -> `bundle.expansion` | Served always; navigation-only |
| review: `list_review_tasks`, `get_review_task`, `edit_review_draft`, `refine_review_draft`, `propose_review_to_git` | routes WIRED; happy paths TASK-BEFORE-USE | `operations.py` `_list/_get/_edit/_refine/_propose_review_*`; served on both transports. The ROUTES are reachable (`list_review_tasks` responds, empty), but a clean `make up-demo` runs no connector sync and no processor, so NO review task exists for `get_review_task`/`edit_review_draft`/`refine_review_draft` to act on -- their happy paths are TASK-BEFORE-USE. `propose_review_to_git` is additionally off until a write-back repo+token are configured (`main.jsx:366`), and is PII-guarded |

`RESOLVE_PATH_OPERATIONS = (CATALOG, RESOLVE, VALIDATE)` is the only hashed
allowlist (`loop.py:43`); discover/expand/review are served but outside it, so
they do not move `tools_hash` (`sha256:fe930a003b731211`). This matches the
advertised trust boundary (v0-foundation.md sec 7, CLAUDE.md).

## 2. Authentication, authorization, admin, login, settings (SPECIAL FOCUS)

Brandon ran the local demo and did not see admin/login/auth. That observation is
CORRECT and is the intended default. Precise picture:

| Surface | Status | Evidence | Default-deployment note |
|---|---|---|---|
| Authorization gate | PARTIAL (real, inert) | `security/authz.py::authz_enabled`; shared executor gate | **DEFAULT OFF** -- unset `HYPERSET_AUTHZ_ENABLED` => allow-all, every governed read answered unauthenticated |
| OIDC / JWT authentication | PARTIAL (real verifier, inert) | `security/oidc.py::verify_bearer` (real PyJWT RS256, HTTPS-only JWKS, requires `exp`); `principal_from_bearer` returns `None` unless authz is on | **OFF by default**; needs `HYPERSET_AUTHZ_ENABLED` + `HYPERSET_OIDC_ISSUER/AUDIENCE/JWKS_URL` all set |
| Role model (scoped grants) | PARTIAL (role vocabulary landed) | The #78 posture roles now exist in the PUBLIC token-resolvable `authz.py` `ROLES`: `reader`, `explorer`, `reviewer`, `admin`, `git_owner` (hy-dq0r), plus the non-human `service` identity (hy-87us, F3) -- a client-credentials caller, read-only. `service` is token-resolvable but MACHINE-ONLY: the verifier strips it from a token's roles unless the token proves it is a genuine client-credentials grant (`sub == client_id`/`azp`), so a human bearer listing `roles=["service"]` cannot become a service identity (hy-okm6, `CLIENT_CREDENTIALS_ONLY_ROLES` + `oidc._is_client_credentials`). Only `reviewer` holds the `review` action; the rest are read-only (least privilege). The trusted in-process `system` role is DELIBERATELY OUTSIDE `ROLES`, in an identity-only `_SYSTEM_ROLES` registry reachable solely through the `SYSTEM_PRINCIPAL` object -- so a bearer token asserting `roles=["system"]` (hy-09hy maps `Principal.roles` from the verified token) resolves to nothing and is denied, and cannot spoof in-process review-authoring (hy-i4hc). Enforced through the existing `Scope`/deny-the-whole seam, default-OFF. STILL OPEN: the reviewed per-principal grant SOURCE (F1 recommends a Git-owned policy) is a later slice, admin config auth is hy-2nqb, and the DB "reviewer" columns still store a human's NAME, not an authz role |
| Login / logout / session / user management | PARTIAL (served, present-but-default-off) | `security/login.py` plus `transport/http.py`: fail-closed PKCE, state/nonce, code exchange, `/login`/`/callback`/`/logout`, signed HttpOnly session, subject-bound CSRF, and session-authenticated governed reads (#423) | Routes are inert while authz is off; a configured deployment uses its OIDC provider. Live configured-OIDC smoke and user-management/invite remain. No local credential provider exists. |
| Admin / Settings page (`/admin/`) | DEMO-ONLY; write authz-gated when on | `http.py:116` `/admin`; UI `main.jsx:458` `SettingsPanel`; served only behind `_playground_enabled()` | A write-back **config** form. The SURFACE guard is still a routing split (`/admin/api` vs `/playground/api`), not authentication; but the write-back-config WRITE now additionally requires an `admin` (`configure`) grant server-side when the authz gate is on (hy-2nqb). DEFAULT-OFF: with the gate off (loopback dev) it stays unauthenticated -- the local-only shortcut |
| Write-back secret at rest | PARTIAL | `security/secret_box.py` AES-256-GCM, KEK from `HYPERSET_SECRET_KEY` env | **Default uses an ephemeral in-memory key with a loud warning** (`secret_box.py:49`): a stored secret does not survive a restart, "never acceptable for a real deployment" |

Governing decisions: **ADR-0030 is ACCEPTED (ratified 2026-08-15) but ratification
authorizes the seam, NOT enforcement** -- "The gate stays behind
`HYPERSET_AUTHZ_ENABLED`, DEFAULT-OFF" (ADR-0030). ADR-0030 names loopback as the
mitigation. The supported Docker demo's containers bind `0.0.0.0` (a container must, to
answer a published port at all -- hy-voes), but as of **hy-w5ld** the demo publishes those
ports on the **host's loopback only** (`127.0.0.1:8000`/`127.0.0.1:8010`) and asserts that
with `HYPERSET_LOOPBACK_PUBLISHED=1`, so the effective exposure is loopback and nothing is
LAN-reachable. A genuinely NON-LOOPBACK, network-reachable bind (no loopback-publish
assertion, no auth) still FAILS CLOSED with an `InsecureBindError` (`security/deployment.py`).
So the demo runs, unauthenticated but not exposed; a real network deployment must configure
auth. Enabling authz for a network deployment is gated on Brandon's four preconditions plus
an independent review union. Two DISTINCT follow-on tracks, not one:

- **Authz ENABLEMENT** is the remaining reviewed per-principal grant source and
  scoped policy, the live configured-OIDC smoke, and the human-controlled
  enablement posture. It stays on **#78 / ADR-0030 F1-F5**, bead **hy-tjow**.
  Bearer verification, role mapping, service identity, PKCE login/session, and
  transport gates have shipped; this audit does not turn them on by default.
- **Admin write-path auth** (authenticating the `/admin` settings write paths:
  writeback-config + propose) is the narrower tracked follow-on **hy-2nqb**, and
  coordinates with the enablement track's FORK F4.

The non-loopback exposure is exactly what ADR-0035's config-layering safety rule
closes, and the runtime guard for it has LANDED (hy-71mi, hardened by hy-w5ld): a
network-reachable non-loopback bind REFUSES TO START without auth (`security/deployment.py`,
wired into `cli` serve commands; `config/safety.py` refuses `bind: all` without auth at
config-load). The only unauthenticated paths are a true loopback bind and a
loopback-PUBLISHED container (a `0.0.0.0` in-container bind whose port is published on
`127.0.0.1` only, asserted by `HYPERSET_LOOPBACK_PUBLISHED` and bound by a test to a
`127.0.0.1:` publish). The removed blanket `HYPERSET_ALLOW_INSECURE_NETWORK_BIND` flag is
NOT re-introduced. ADR-0031 (domain hierarchy) is ACCEPTED, validation-only.

**Bottom line for the demo: `make up-demo` STARTS and is SAFE. Its `api` (:8000) and
`mcp-http` (:8010) containers bind `0.0.0.0` but publish on `127.0.0.1` only and assert
the loopback-published topology (hy-w5ld, Option A), so they are reachable from the host
but NOT the LAN -- unauthenticated by design on loopback, exactly the ADR-0030 mitigation.
A genuinely network-reachable bind without auth fails closed; the earlier
`HYPERSET_ALLOW_INSECURE_NETWORK_BIND` blanket escape hatch stays removed. The admin
writes (writeback-config, propose) are therefore never exposed to the LAN.**

## 3. Playground UI surfaces (DEMO-ONLY)

Served only behind `HYPERSET_PLAYGROUND_ENABLED` (`http.py:994`). React SPA
(`playground/ui/src/main.jsx`) with three surfaces: Playground, Review, Settings.
Playground exposes 9 diagnostic tabs (`DEBUG_TABS`, `main.jsx:94`): live chat,
environment, catalog, discover, bundle resolver, plan validation, agent builder,
agent evaluator, domain graph -- all read-only / not persisted. WIRED when the
playground is enabled (the demo forces it on and the api image builds the bundle).

## 4. Supporting engines (behind non-tool claims)

| Capability | Status | Evidence |
|---|---|---|
| Read-only Superset connector | WIRED (hermetic bundle) in demo; live path configure-before-use | `connectors/superset/`; `make up-demo` now creates a BUNDLE-mode connection from a checked-in Superset export and syncs it (`make playground-observed`, no live Superset and no keys), so the demo carries an observed estate. The LIVE REST path is still manual: `make connection-live` + `make sync-live CONNECTION_ID=...` (`Makefile`) |
| DataHub connector (catalog/lineage) | DEMO-ONLY (separate profile) | `connectors/datahub/`; `[datahub]` profile via `make up-datahub` only |
| Offline processor (gap/conflict/drift/stale findings) | PARTIAL (not in the demo path) | `processor/engine.py`, `rules.py` exist; `make process` runs the processor over the most-recent completed sync run (hy-jp0gq). Whether `make up-demo` itself syncs and processes is tracked separately (hy-y1ng8) |
| Bundle-time reconciliation -- `linked_evidence.conflicts` (#125) | PARTIAL (2 producers wired-but-empty-in-demo; 3 producers landed-but-inert) | Only TWO producers are reached from the resolve path (`bundle/resolver.py` `_linked_evidence`): `source_deleted_while_governed` and `prohibited_but_referenced`; a persisted processor finding is additionally projected via the generic `reconcile()` (`_conflict`). The ownership/grain/freshness producers (`ownership_mismatch`, `grain_mismatch`, `freshness_stale`) are LANDED in `bundle/reconcile.py` but UNWIRED -- NO resolve-path call reaches them (deferred activation, pending **hy-z3wy** / **hy-yjkv** / **hy-kh9k**); a grep of `resolver.py` for those names returns nothing, by design. `conflicts` defaults to `[]` (`resolver.py:131`), and the two wired producers need OBSERVED assets that disagree with Git. As of **hy-u26p** a clean `make up-demo` now syncs a HERMETIC Superset export bundle (`make playground-observed`) whose observed estate carries the revenue manifest's prohibited `raw_payments` bi_override dataset AND a chart that queries it, so `prohibited_but_referenced` genuinely fires in `linked_evidence.conflicts` via the real resolve path -- no processor and no #38 (`tests/unit/test_demo_conflict_wiring.py` binds the demo wiring to the conflict-firing fixture; `tests/postgres/test_context_bundle.py` proves the resolve behaviour). The three inert producers (`ownership_mismatch`, `grain_mismatch`, `freshness_stale`) remain UNWIRED (activation forks **hy-z3wy** / **hy-yjkv** / **hy-kh9k**), and the finding-projected `_conflict` still needs the processor (#38). Tracked by **hy-u26p** (conflict demo, done) + the three activation forks |
| Flywheel (miss -> gather -> draft -> PR) | PARTIAL | `flywheel/authoring.py`, `live_lookup.py`, `git_pr.py`; propose is configure-before-use |
| Evaluation harness (#25 recorded-replay) | DEV-ONLY (not reachable in the demo) | `evals/task.py`, `report.py`, `scorers.py` -- deterministic and genuinely runnable via `hyperset evals score` / CI, but no `make up-demo` service invokes it, so it is a dev/CI capability, not a deployment surface (same rule as the #141 spine row) |
| Adversarial benchmark (#141) scoring spine | LIBRARY / DEV-ONLY (not reachable in the demo) | `hyperset/evals/benchmark_adversarial.py` is on main (via #370, hy-lvq6), but it serves NO operation, has NO CLI/report command, and no default service invokes `score_benchmark()` -- only the library + its tests exist. Not reachable in a clean `make up-demo`, so not WIRED by this doc's rule; the live generate/answer/judge arms are separately slices 2-4, blocked on infra hy-2tg6 |
| PII guard on proposals/miss-log | PARTIAL | `security/pii.py`; no-op unless `HYPERSET_PII_GUARD` |

## 5. Advertised but NOT real in a clean deployment (gaps)

| Claim | Status | Evidence | Tracking |
|---|---|---|---|
| Webhook / failure notification ("webhook on failure") | PLANNED / ABSENT | v0-foundation.md sec 6 + CLAUDE.md advertise it; no notifications module in `hyperset/` | **hy-gh-33** (open) |
| Per-source facets: freshness staleness, lineage walk, checks execution | PARTIAL (stated, not enforced) | v0-foundation.md sec (schema 12-16) admits each does NOT compute/enforce; only `classification_undisclosed` + `grain_fanout` actually enforce | **hy-gh-284** |
| Authz ENFORCEMENT enabled by default | PARTIAL (substrate real, DEFAULT-OFF) | OIDC/JWKS verification, role mapping, RBAC/service identity, PKCE login/session, and transport gates ship; `authz_enabled()` remains false by default. Reviewed per-principal grant source/scoped policy and live configured-OIDC smoke remain under #78 / ADR-0030 F1-F5. | **hy-tjow** (Brandon-pending) |
| Admin authentication on write paths | PARTIAL (enforced when authz on) | The write-back-config WRITE now requires an `admin` (`configure`) grant server-side (`operations.admin_config_authorization_error`, wired in `_post_writeback_config`), and `propose`/`edit`/`refine` require `review` via `run_operation` (hy-dq0r). DEFAULT-OFF: with the gate off (loopback dev) the admin write stays unauthenticated -- the local-only shortcut; a hosted deployment enables authz to close it (hy-2nqb) | **hy-2nqb** |
| A clean `make up-demo` demonstrates a conflict; a finding + review task are still not demonstrated | PARTIAL (conflict now demonstrated; finding + review task deferred) | up-demo now syncs a hermetic Superset bundle (`make playground-observed`), so a real `prohibited_but_referenced` appears in `linked_evidence.conflicts` via the resolve path (hy-u26p, done). A FINDING and a REVIEW TASK still need the offline processor in the demo path. `make process` now runs the processor over a completed sync run (hy-jp0gq); wiring it into `make up-demo` -- without direct-seeding a row, which is disallowed as it would misrepresent the pipeline -- is tracked by hy-y1ng8 | **hy-u26p** (conflict, done) + **hy-y1ng8** (finding + review task) |
| A bare host `hyperset serve http` (or the served-playground test) serves a broken UI without a prior `make playground-ui` | MINOR | `http.py:1001` falls back to source `index.html` (`/src/main.jsx`) when `playground/ui/dist/` is absent. NOT a `make` path: both `make serve` and `make up-demo` are `docker compose up ... api` and the image builds the bundle | **hy-ida7** neighbours it (vite base); a direct-host caveat, not a demo-path gap |
| Future connectors (Looker, Power BI, dbt, warehouses) | PLANNED | MANIFESTO lists as roadmap; only Superset + DataHub have code | roadmap |
| `get_provenance` tool | ABSENT (deliberate) | v0-foundation.md sec 7 gates it out pending evaluator evidence + ADR amendment | by design |

## How to read this audit

The deterministic trust core (catalog / resolve / validate / expand) is genuinely
WIRED and demonstrable in a clean `make up-demo`, which now also syncs a hermetic
observed estate so a real `prohibited_but_referenced` reconciliation conflict is
demonstrated on the resolve path (hy-u26p). The review and assist ROUTES are
served and reachable, but their content depends on data a clean demo does not yet
create: a review task still needs the offline PROCESSOR wired into the demo
(`make process` runs it over a sync run -- hy-jp0gq; the demo wiring is tracked
by hy-y1ng8), and a proposal needs a configured
write-back repo -- so those happy paths are task/configure-before-use, not WIRED. Dev/CI capabilities (the #25 and #141 eval harnesses) are real and
runnable but not reachable from the deployment, so they are DEV-ONLY, not WIRED.
The security surface (authn/authz/admin/login) is real CODE deliberately INERT by
default (ADR-0030); as of hy-w5ld the demo runs unauthenticated but LOOPBACK-PUBLISHED
(reachable from the host, not the LAN), while a genuinely network-reachable bind without
auth fails closed -- so there is no unauthenticated-LAN posture the demo exposes. The gaps in section 5
are advertised capabilities a clean deployment cannot demonstrate today; each has a
tracking bead. Every WIRED row above is reachable by a clean `make up-demo`; no
other row claims to be.
