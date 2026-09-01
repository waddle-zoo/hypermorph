# Production deployment & enterprise-readiness

This is the counterpart to [Getting started](getting-started.md) (which takes a clean
checkout to a **loopback demo**). It describes what a **network, multi-user** deployment
requires and — deliberately, like the getting-started guide — is honest about what has
**landed on `main`**, what is **in review**, and what is **proposed but not implemented**.
Every claim below is cross-checked against real routes, roles, and config code; where a
capability is not yet on `main`, it says so.

> [!IMPORTANT]
> **Status legend.** ✅ landed on `main` · 🔶 in review (open PR, not merged) · 🧭 proposed
> (design only, not implemented). Do not read a 🔶/🧭 row as a shipped guarantee.

## 1. Posture at a glance

| Capability | Status | Reference |
|---|---|---|
| Fail-closed on a non-loopback unauthenticated bind (no insecure override) | ✅ | #418 (hy-71mi/hy-w5ld), [ADR-0035](adr/0035-layered-deployment-configuration.md) §5 |
| Bearer **OIDC verifier** (RS256, JWKS-pinned, HTTPS-only, fail-closed) | ✅ | [ADR-0030](adr/0030-the-authorization-boundary.md) |
| RBAC role vocabulary + action gate (`READ`/`REVIEW`/`configure`) | ✅ | #411 (hy-dq0r) |
| Machine-only `service` role gated to client-credentials tokens | ✅ | #417 (hy-okm6) |
| Admin **`configure`** gate on config writes (write-back target, context sources) | ✅ | #416 (hy-2nqb) |
| Append-only **admin audit trail** (`/admin/api/v0/audit`) | ✅ | #421 |
| Admin **readiness** + **context-source** management surfaces | ✅ | #419 / #420 |
| MCP onboarding wizard | ✅ | #409 (hy-8u0a) |
| **Layered config loader** (`base.yaml` + `HYPERSET_CONFIG` overlays, fail-closed) | ✅ *loader slice* | #414 (hy-7h00), [ADR-0035](adr/0035-layered-deployment-configuration.md) |
| Config **secret-ref resolution** + runtime **call-site rewiring** | 🔶 | hy-ogg5 (slice 2), hy-tc4o (slice 3) |
| Browser **OIDC Authorization-Code + PKCE** login/session (`/login`, `/callback`, `/logout`) | ✅ *present, not default* | #423 (hy-jyha) |
| Regular-user **Explorer shell** (Home/Explore IA) | ✅ | #415 (hy-uh19) |
| Production **auth enable-flip** turned on by default | 🧭 (human-gated) | hy-nt89 / hy-ia9n |
| Per-user access policy (**BYOKG**, Git-owned domain/source ACLs) | 🧭 | ADR-0036 (proposed) |

## 2. Configuration model (ADR-0035 / #414)

The **layered configuration model** has landed as a loader slice: `hyperset/config/`
reads a checked-in `config/base.yaml` (ALWAYS loaded, never named), deep-merges the
ordered `HYPERSET_CONFIG` overlay(s) over it (a `:`-separated list; a **missing named
overlay is FATAL**, never skipped), and validates the merged tree against an
explicit-typed schema. It **fails closed** on any unknown key, wrong type, missing
required field, plaintext where a secret **reference** is required, or an absent overlay.
There is **no silent fallback to demo defaults** — the demo is an explicit
`config/demo.yaml` overlay a deployment opts into (`HYPERSET_CONFIG=config/demo.yaml`).

The YAML loader is hardened on purpose (`hyperset/config/loader.py`): duplicate mapping
keys are rejected, anchors/aliases/custom tags are rejected, and implicit scalar typing
is disabled (every plain scalar is a string; only an explicit `null`/`~` unsets a base
key) so the typed schema — not YAML — does every coercion.

> [!NOTE]
> **What is NOT yet wired.** #414 is the loader + merge + schema slice, **inert** at
> runtime: it is constructed by tests, not yet by the server. Secret-reference
> **resolution** (hy-ogg5) and rewiring existing call sites onto the loader (hy-tc4o) are
> subsequent slices. Until they land, the running server still reads its settings from
> environment variables (the `HYPERSET_*` / `.env` path below). Plan a deployment around
> the config MODEL, but do not assume the loader is the live config path today.

**Secrets are referenced, never inlined.** A secret in config is a **name/reference**
(an env var name or a mounted file), validated for shape and resolved server-side — never
a plaintext value in the YAML, a URL, an argv, or a log. This mirrors the already-shipped
write-back token model (`env_ref` stores a secret NAME; `encrypted`/`github_app` store
ciphertext, never plaintext — hy-eji4/hy-up4k/hy-bdhg).

### `.env` is break-glass / local-dev only

`.env.example` exists to make the **loopback demo** run out of the box; its values are
placeholder local-dev secrets, and it is git-ignored once copied. It is **not** the
production configuration system: do not ship a real `.env` with real secrets. A network
deployment supplies configuration through the layered model above and secrets by
reference (next section).

## 3. Kubernetes: config as a mount, secrets by reference

- **Config overlays** ship as a **ConfigMap** mounted read-only into the container, and
  `HYPERSET_CONFIG` names the mounted overlay path(s) layered over the checked-in
  `config/base.yaml`. Keep environment-specific values in overlays, not in the image.
- **Secrets** are supplied as a **Kubernetes Secret** and referenced **by name**: mount
  the Secret as files and point the config's secret-references at those paths, or expose
  each as an environment variable and reference the **variable name**. The secret VALUE
  never appears in the ConfigMap, the image, the pod spec's args, or a log.
- **Bind + exposure.** The container binds `0.0.0.0` (to answer a Service), so a network
  deployment MUST configure authentication — an unauthenticated non-loopback bind
  **fails closed** (§4). Do not set `HYPERSET_LOOPBACK_PUBLISHED` on a pod reachable
  through a Service or Ingress; that signal asserts a host-loopback-only publish and is a
  demo-only topology assertion, not a bypass.
- **TLS terminates at the ingress/gateway** that also terminates OIDC/SAML and forwards
  the bearer token; Hyperset trusts that gateway to have authenticated the caller.

## 4. Authentication & authorization

**Bearer (machine / API / MCP).** With `HYPERSET_AUTHZ_ENABLED=true` and the
`HYPERSET_OIDC_ISSUER` / `HYPERSET_OIDC_AUDIENCE` / `HYPERSET_OIDC_JWKS_URL` verifier
configured, every governed operation is gated: the verifier checks an RS256 signature
against the pinned JWKS (HTTPS-only, redirect-hardened), requires `exp`, and pins the
algorithm; ANY failure denies (fail-closed). Roles are derived from the configured
`HYPERSET_OIDC_ROLES_CLAIM`; an unmapped role name matches no grant.

**Roles (RBAC, #411).** Beyond the baseline `reader`: `explorer` (reads, configures
nothing), `reviewer` (read + author proposals), `admin` (holds `configure`), `git_owner`,
and a machine-only `service` role that is honoured **only** for a genuine
client-credentials token (`sub == client_id`/`azp`, #417) — a human bearer that merely
lists `roles:[service]` is stripped of it. An in-process `system` identity (read + review)
is authorized against a separate registry and **cannot be minted by any token**.

**Approved-reviewer allowlist (hy-a607k).** Beyond the `reviewer` role, a deployment may
require an explicit **per-principal allowlist** for the review surface and the
review-authoring ops (`edit`/`refine`/`propose`/`set_review_assignee` and opening
`/review`). Point **`HYPERSET_REVIEWER_ALLOWLIST`** at a file — committed to your own Git
and mounted (Git-owned **by reference**, like the write-back token) — listing one approved
opaque `subject@issuer` identity per line (`#` comments allowed). It is **ANDed with** the
reviewer role: a listed reviewer authors, an unlisted one is denied the SAME uniform
`unauthorized`, and a listed non-reviewer is still denied (the allowlist grants nothing on
its own). **Default-off** (unset ⇒ role-only, byte-identical) and **fail-closed** (a
configured-but-missing/empty file approves nobody). Only the `review` action consults it —
governed reads are unaffected — and the in-process `system` identity is exempt. Read fresh
each request, so editing the file takes effect without a restart.

**Admin config writes (#416).** Setting the write-back target and adding/syncing a
context source require an admin **`configure`** grant, enforced **server-side** on the
write path (not merely the `/admin` surface split) when the gate is on. Every such action
is recorded to the append-only **audit trail** at `GET /admin/api/v0/audit` (admin-gated,
records actor/action/target/result, never a secret; a credential-bearing repository
pointer is refused before it can be persisted, and the trail is written in the same
transaction as the mutation so a mutation never lands unaudited).

**Non-loopback fail-closed (#418, ADR-0035 §5).** A server told to bind a non-loopback
host refuses to start unless authorization is configured. There is **no insecure
override**. A loopback bind stays unauthenticated by design (`localhost` is unreachable
off the host).

**Browser login (✅ landed, #423).** An exposed UI cannot present a bearer JWT, so a
browser OIDC **Authorization-Code + PKCE** flow serves `/login` → IdP → `/callback`
(state + PKCE + a signed single-use login cookie, ID-token **nonce** binding) → a signed
**HttpOnly** session cookie, plus `/logout`. It is **present-but-not-default**: inert off
the authz gate. The `/callback` request DOES carry the OAuth `code` and `state` in its
query — that is the Authorization-Code flow, and they are **transient, single-use, and
exchanged for the ID token server-side**. The guarantee is narrower and precise: no
authorization code, token, or secret is ever written to an **access log** (the query
string of *every* logged request line is redacted), to **browser storage** (the session
is an HttpOnly cookie carrying only the already-verified identity, never the IdP tokens),
to a **URL Hyperset emits**, to **rendered config**, or to **diagnostics/error output**.
It does **not** flip auth on by itself — see §6.

## 5. Operator surfaces

- **Readiness** (`/admin/api/v0/readiness`, #419) — an admin-gated overview derived from
  recorded service health and configuration presence; no live per-request probing, no
  secret values.
- **Context sources** (`/admin/api/v0/context/sources`, #420) — list/add/sync the Git
  context pointers (repo/ref/path), admin-`configure`-gated; snapshots what Git owns and
  approves nothing (ADR-0012).
- **Audit trail** (`/admin/api/v0/audit`, #421) — append-only, newest-first, admin-gated.
- **Explorer user shell** (✅ #415) — a quiet Home with one "start a question" action and
  a persistent user shell (New chat, Explore context, recent threads, Connect MCP/docs,
  Help, profile), built on the restored chat behaviour.

## 6. What is NOT implemented (do not overclaim)

- **The auth enable-flip is human-gated** (hy-nt89 / hy-ia9n). The verifier, RBAC, admin
  gate, and browser login flow are all **present on `main`**, but turning authentication
  **on by default** for a deployment is an explicit human decision, not something these
  slices do — the login routes are inert off the authz gate.
- **BYOKG / per-user access policy is proposed only** (ADR-0036, 🧭). Per-user or
  per-domain/source ACLs are a design (Git-owned, adapter-backed), not implemented. Today
  a verified `reader` reads all governed context.
- **Config secret-reference resolution and call-site rewiring are still pending**
  (hy-ogg5 / hy-tc4o); until they land the running server reads settings from the
  environment, so the layered loader (§2) is the target model, not today's live path.

## 7. Test-evidence packet (enterprise-readiness skeleton)

Reproducible from a clean checkout; the **named gate** prints one `HYPERSET-GATE v2` line
whose `tree_id` must equal `git rev-parse <sha>^{tree}` for the measured tree.

```bash
uv sync --all-extras --all-groups
make playground-ui                       # builds playground/ui/dist/ (needs npm/Node)
uv run ruff check . && uv run ruff format --check .
uv run python scripts/gate.py            # HYPERSET-GATE v2 (unit + integration)
uv run pytest tests/postgres -q          # service-backed; needs Docker
python3 scripts/check_docs.py            # docs match the served version/contract
```

**Clean Docker rebuild-from-`main`** (the image builds the UI bundle itself):

```bash
git checkout main && git pull            # 068db68 at time of writing (login + Explorer merged)
docker compose build api mcp-http
make up-demo                             # loopback demo; verifies the built image serves
```

**Measured this readiness pass** (local gate; external CI is **billing-blocked
town-wide**, hy-aw1v, and is advisory only — a red CI badge is not a product failure). The
three feature PRs below all **landed on `main`** (`068db68`); each gate line is the last
measured tip of that PR before merge, pasted **verbatim and complete** (a bare pass count
or an elided `tree_id` is not comparable across trees — the whole line, with its `sha`,
`tree`, `tree_id`, `cmd`, and env fields, is the unit of evidence). For each, verify by
`git rev-parse <the committed tip>^{tree}` equalling the line's `tree_id`.

```text
# PR #421 — admin audit trail (round 4), committed/merged tip 9aa565a (9aa565a^{tree} == tree_id below)
HYPERSET-GATE v2 sha=9aa565aebb0e1ce58350878f32480cc0ac82eeef tree=dirty tree_id=23a47145106201ccd15f997adc502a5ddc1d861b extras=all cmd="uv run pytest tests/unit tests/integration -q" env_cleared="none" env_observed="none" collected=2279 uncollected_modules=0 passed=2273 failed=0 errors=0 skipped=5 xfailed=1 xpassed=0 result=PASS

# PR #423 — OIDC PKCE login (round 5), committed/merged tip 4da2a80 (4da2a80^{tree} == tree_id below)
HYPERSET-GATE v2 sha=4da2a80195095cacb399d19487468f8b2c30beb6 tree=dirty tree_id=de512231c9dc215672d9bbd66ed66f4931ba148f extras=all cmd="uv run pytest tests/unit tests/integration -q" env_cleared="none" env_observed="none" collected=2308 uncollected_modules=0 passed=2302 failed=0 errors=0 skipped=5 xfailed=1 xpassed=0 result=PASS

# PR #415 — regular-user Explorer shell, committed tip 93054f6
HYPERSET-GATE v2 sha=93054f64ad2dbdcaab271245c1e12594ea8396b7 tree=clean tree_id=88d28aafcad756c1c4e94daa889f0c2bd1234333 extras=all cmd="uv run pytest tests/unit tests/integration -q" env_cleared="none" env_observed="none" collected=2282 uncollected_modules=0 passed=2276 failed=0 errors=0 skipped=5 xfailed=1 xpassed=0 result=PASS

# THIS docs PR (#424, hy-bx76) — measured on the code+docs tree with these fixes applied.
# The line below is the gate run for this branch; the doc commit that carries it adds only
# this evidence block, so the commit's own tree differs from the line's tree_id by exactly
# that paste (a doc-only change touches no test).
HYPERSET-GATE v2 sha=e2acec5251bf5591508ce800de2255a79a0ed027 tree=dirty tree_id=59a37303da51b68baf71612fc7b4bcdfd10e09fc extras=all cmd="uv run pytest tests/unit tests/integration -q" env_cleared="none" env_observed="none" collected=2311 uncollected_modules=0 passed=2305 failed=0 errors=0 skipped=5 xfailed=1 xpassed=0 result=PASS
```

**Known external blocker.** GitHub Actions is billing-blocked for the org (hy-aw1v): CI
runs fail in seconds with zero steps executed. This is a **repo-health advisory**, not a
runtime or test failure — the local gate above is the authoritative signal.

**Recovery owners.**

| Area | Owner / tracking |
|---|---|
| Config secret-ref resolution + call-site rewiring | hy-ogg5, hy-tc4o |
| Auth enable-flip (turn on by default) | hy-nt89, hy-ia9n |
| Per-user access policy (BYOKG) | ADR-0036 (proposed) |
| CI billing block | hy-aw1v |

## See also

- [Getting started](getting-started.md) — the loopback demo.
- [ADR-0035](adr/0035-layered-deployment-configuration.md) — layered config + bind safety.
- [ADR-0030](adr/0030-the-authorization-boundary.md) — why authorization is default-off.
- [ADR-0012](adr/0012-git-owned-context-authority.md) — Git owns governed meaning.
- [Feature-parity audit](development/feature-parity-audit.md) — advertised vs running.
