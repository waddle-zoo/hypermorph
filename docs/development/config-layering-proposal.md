# Production configuration layering -- design proposal (hy-c5p7)

Status: PROPOSAL (2026-08-16, hy-c5p7). DESIGN-FIRST: this is the config-layering
model + worked examples + impl decomposition. It does NOT build the loader. The
contract it proposes is recorded as ADR-0035 (PROPOSED). Three shape decisions are
flagged FORK and bubbled to the mayor before anything is built.

## 1. Problem (measured)

Configuration today is ~30+ `HYPERSET_*` environment variables read by
`os.environ.get(...)` scattered across ~10 modules (`db/engine.py`,
`transport/http.py`, `transport/operations.py`, `security/pii.py`,
`candidates/service.py`, `flywheel/*`, `context/git.py`, `cli.py`, ...), set in
`docker-compose*.yml`, and documented only in `.env.example`. There is no central
model, no schema, no validation, no precedence, and secrets (`HYPERSET_DB_PASSWORD`,
`HYPERSET_SECRET_KEY`, `HYPERSET_WRITEBACK_TOKEN`, `HYPERSET_DATAHUB_TOKEN`) sit as
plain env values. A customer cannot layer their config over Hyperset's defaults
without editing upstream files, and an invalid or partial config silently falls
back to whatever a given `os.environ.get(name, default)` call happens to default
to -- including demo defaults.

## 2. Goals

- A customer layers THEIR config OVER Hyperset defaults WITHOUT editing upstream.
- Documented precedence; deterministic merge.
- Schema validation, FAIL-CLOSED on invalid, NO silent fallback to demo defaults.
- Secrets by REFERENCE only; secret VALUES never live in plain config, logs, or a
  served/echoed config.
- Per-environment overlays (dev / staging / prod).
- Configurable: agents, models, providers, auth, connections, feature flags,
  integrations.
- Docker Compose today; a credible Kubernetes path.

## 3. The layering model

Four ordered sources, LOWEST to HIGHEST precedence, DEEP-MERGED into one typed
settings object at startup:

1. **Base defaults** -- a checked-in `config/base.yaml` in the image. The single
   source of truth for the config SHAPE and for safe, non-demo defaults (auth off,
   bind loopback, features conservative). Never edited by a customer.
2. **Deployment overlay(s)** -- `config/base.yaml` is ALWAYS loaded first and
   implicitly; `HYPERSET_CONFIG` names ONLY the overlay file(s) layered ON TOP of
   base, an ordered `:`-separated list (e.g. `HYPERSET_CONFIG=/etc/hyperset/prod.yaml`
   or `staging.yaml:region-eu.yaml`), each deep-merged over the previous. Base is
   NEVER named in `HYPERSET_CONFIG` -- naming it would double-load it. **Unset
   `HYPERSET_CONFIG` = base only** (the safe, auth-off, loopback default). A named
   overlay that is missing or unreadable is a FATAL error (fail-closed), never
   skipped. The demo is just another overlay: `make up-demo` sets
   `HYPERSET_CONFIG=config/demo.yaml` (a change this proposal requires in
   `docker-compose.demo.yml`, which sets no `HYPERSET_CONFIG` today), so the demo's
   playground-on posture is an explicit selection, never an implicit fallback.
3. **Allowlisted env overrides** -- a SMALL, CLOSED set of `HYPERSET_*` vars
   (break-glass ops knobs, section 9) mapped to specific config paths. NOT a
   general escape hatch -- only allowlisted keys are honored; an unknown `HYPERSET_*`
   var is REPORTED and REJECTED at startup (fail-closed), never silently applied.
4. **Secret-reference resolution** -- performed LAST, separately (section 8): a
   value of the form `${env:NAME}` reads environment variable `NAME` directly; a
   value of the form `${secret:NAME}` is looked up through a pluggable SECRET
   PROVIDER (default provider: a mounted secrets directory, one file per name --
   `${secret:superset_password}` reads `${HYPERSET_SECRETS_DIR:-/run/secrets}/superset_password`;
   the two forms are DISTINCT and never interchangeable). The resolved value lives
   ONLY in the in-memory settings and is redacted by config PATH everywhere (section 8).

**Merge semantics.** Deep-merge for mappings. An overlay REPLACES a scalar or a
list wholesale -- lists are never concatenated, so the effective value is
predictable. An explicit `null` in an overlay UNSETS a base key (so a customer can
remove a default). Precedence within a merged key is strictly source order above.

**YAML trust boundary.** Bare `yaml.safe_load` does NOT give any of the three
guarantees below and post-processing a parsed tree cannot recover them (duplicate
keys are already collapsed, alias structure is already expanded, and implicit
scalar types are already applied by the time `safe_load` returns). So parsing is
NOT `safe_load`; it is a `yaml.SafeLoader`-DERIVED custom loader
(`HypersetConfigLoader`) invoked EXPLICITLY as `yaml.load(stream,
Loader=HypersetConfigLoader)` -- never `yaml.load` with an unsafe loader, never a
full/unsafe loader (so no arbitrary object construction), and never bare
`safe_load`. The custom loader is `SafeLoader` (core tags only, no
`!!python/...`) with three additions, all applied BEFORE any schema coercion:

1. **Duplicate mapping keys REJECTED.** `SafeLoader.construct_mapping` is
   overridden to raise on a repeated key (stock PyYAML silently keeps the last),
   so a duplicate cannot quietly change effective config.
2. **Anchors/aliases and non-core tags REJECTED.** The loader raises when it
   encounters an anchor, an alias, or any explicit non-core tag (e.g. by
   overriding node composition / `compose_node` and the tag construction so
   `&a`/`*a`/`!!foo`/`!custom` are errors, not honored). Anchors/aliases add a
   merge path the precedence model does not account for; custom tags are arbitrary
   construction.
3. **Implicit scalar typing DISABLED.** The loader's `yaml_implicit_resolvers`
   are cleared so every unquoted scalar is constructed as a plain string. The
   typed schema (not YAML's implicit typing) does every coercion, so `no`/`yes`
   ->bool, unquoted versions, `null`/`~`, and sexagesimals (`1:30`->int) cannot
   silently coerce at the parse layer. (The explicit `null`-unsets-a-key semantic
   above is honored by the SCHEMA over the string `"null"` / an explicit YAML
   `null` tag, not by an implicit resolver.)

A loader that merely calls `safe_load` and post-checks the tree is REJECTED in
review: it cannot see duplicate keys or alias structure at all, and the implicit
types are already applied.

**Two-phase, fail-closed validation.** (1) The merged PRE-RESOLUTION shape is
validated against the explicit-typed schema (types, required fields, unknown keys,
`<ref>` fields hold a well-formed reference not a plaintext value). (2) AFTER secret
resolution, the RESOLVED typed values are validated again (a resolved secret is a
non-empty string, a resolved URL parses, etc.), so neither a duplicate key nor an
implicit coercion nor a bad secret can slip through between the phases. Any failure
in either phase -- unknown key, wrong type, missing required, unresolvable ref, an
absent named overlay, or the auth-safety rule (section 5) -> a FATAL startup error
naming the exact config PATH (never the value; section 8). There is NO fallback to
demo defaults: `config/demo.yaml` is an explicit overlay the demo selects, never an
implicit default the loader reaches for.

## 4. Schema (top-level sections)

```yaml
server:      { bind: loopback|all, api_port: int, mcp_http_port: int,
               ui_bind: loopback, ui_port: int, log_level: str }  # ui_bind loopback-only (section 5)
auth:        { enabled: bool, oidc: { issuer, audience, jwks_url, roles_claim } }
models:      { planner, embedding, generator, judge }        # model tags per role
providers:   { ollama: {base_url}, openai: {base_url, api_key: <ref>, reasoning_effort} }
connections: { superset: {base_url, username: <ref>, password: <ref>},
               datahub:  {base_url, token: <ref>},
               analytics_db: {url: <ref>}, system_db: {url: <ref>} }
playground:  { enabled: bool, agents: [...], default_agent, default_model,
               writeback: { repo, token: <ref> } }
features:    { pii_guard: bool, discover: bool, expand: bool, review: bool }
integrations:{ ... }                                          # extensible map
```

`<ref>` marks a value that MUST be a secret reference (`${env:...}`/`${secret:...}`),
enforced by the schema: a plaintext secret in a `<ref>` field is a validation error.

## 5. The auth toggle, enforced at every real listener (ties hy-7q3o)

Turning authorization ON safely for a non-loopback deployment is a first-class,
fail-closed rule -- AND it must be closed over EVERY ACTUAL network listener the
codebase starts, not just a `server.bind` field. There are THREE shipped listeners
today:

- `api`: `serve http --host 0.0.0.0 --port 8080` (`docker-compose.yml:113`),
- `mcp-http`: `serve mcp --http --host 0.0.0.0 --port 8010` (`:194`),
- the playground UI proxy: `playground/ui/app.py:main()` starts a
  `ThreadingHTTPServer((host, port))` binding `HYPERSET_UI_HOST` (default
  `127.0.0.1`, so loopback today) and proxying to `HYPERSET_HTTP_BASE_URL`.

The first two force non-loopback via `--host 0.0.0.0` with no
`HYPERSET_AUTHZ_ENABLED`; the third has NO authorization of its own at all and can
bind non-loopback the moment `HYPERSET_UI_HOST` is set. A rule that validated only
`server.bind`, or that only knew about `serve http`/`serve mcp`, would be bypassed
by the `--host` the deployment actually uses AND would leave the UI proxy an
unauthenticated open surface.

The rule:

- `auth.enabled: true` requires a COMPLETE `auth.oidc` block (issuer, audience,
  jwks_url) or validation fails -- you cannot enable the gate without a verifier.
- **The effective bind of EVERY network listener is governed by the validated
  config, and a non-loopback effective bind REQUIRES `auth.enabled: true`** with a
  valid oidc block, or the process REFUSES TO START. This covers ALL THREE
  listeners -- `serve http`, `serve mcp --http`, AND the playground UI proxy -- each
  reads the ONE validated `server` object for its bind. MCP over HTTP is a network
  surface exactly as the HTTP API is.
- **The playground UI proxy has no verifier of its own, so it is LOOPBACK-ONLY.**
  It cannot satisfy the `auth.enabled+oidc` predicate (it authenticates nothing and
  merely proxies to the API), so a non-loopback `HYPERSET_UI_HOST` / `server.ui_bind`
  is ALWAYS a fatal startup error -- the invariant closes over it by making its only
  permitted effective bind loopback. (If a deployment needs a network-reachable UI,
  that is a follow-on that puts the proxy behind the authenticated `serve http`
  surface, not a second unauthenticated listener.) `HYPERSET_UI_HOST`/`HYPERSET_UI_PORT`
  map to `server.ui_bind`/`server.ui_port` and are validated by this same rule.
- **A CLI `--host` (or Compose `command:`) that CONFLICTS with the validated
  config is rejected, fail-closed.** `--host`/`--port` become either (a) removed in
  favour of the config, or (b) accepted only when they AGREE with the validated
  `server` object; a `--host 0.0.0.0` while the config binds loopback, or while
  `auth.enabled` is false, is a fatal startup error, not a silent override. The
  Compose services move their bind into the overlay (`server.bind`) rather than a
  `--host` flag (section 7). Loopback is the only effective bind that may run
  unauthenticated (ADR-0030's "a mitigation, not a boundary").
- Until the settings are wired through (hy-ax9w), the loader maps
  `auth.enabled`/`auth.oidc.*` to the existing `HYPERSET_AUTHZ_ENABLED` /
  `HYPERSET_OIDC_*` env the code already reads (`security/authz.py`,
  `security/oidc.py`); the listener-bind enforcement is the specific work of that
  bead, co-owned with hy-7q3o. No runtime behaviour changes until it lands.

## 6. Worked examples

**`config/base.yaml`** (checked in; safe defaults):

```yaml
server: { bind: loopback, api_port: 8080, mcp_http_port: 8010, log_level: info }
auth:   { enabled: false }
models: { planner: qwen2.5:7b, embedding: nomic-embed-text }
providers: { ollama: { base_url: http://host.docker.internal:11434/v1 } }
features: { pii_guard: false, discover: true, expand: true, review: true }
playground: { enabled: false }
```

**`/etc/hyperset/prod.yaml`** (customer overlay; `HYPERSET_CONFIG=/etc/hyperset/prod.yaml` -- base is implicit, never named here):

```yaml
server: { bind: all, api_port: 8080 }
auth:
  enabled: true
  oidc: { issuer: https://idp.example.com/, audience: hyperset,
          jwks_url: https://idp.example.com/.well-known/jwks.json, roles_claim: roles }
connections:
  superset: { base_url: https://superset.example.com,
              username: ${env:HYPERSET_SUPERSET_USERNAME},
              password: ${secret:superset_password} }
  system_db: { url: ${env:HYPERSET_DATABASE_URL} }
features: { pii_guard: true }
```

Here `bind: all` is only accepted because `auth.enabled: true` with a full oidc
block accompanies it; the secrets are references, so the prod overlay is safe to
check into the customer's own repo. A prod overlay that set `bind: all` without
`auth` would FAIL validation at startup.

## 7. Deployment paths

- **Docker Compose (now).** Mount the overlay read-only into the container
  (`./deploy/prod.yaml:/etc/hyperset/prod.yaml:ro`), set
  `HYPERSET_CONFIG=/etc/hyperset/prod.yaml` (base is implicit in the image), put the
  bind in `server.bind` in that overlay (NOT a `--host` flag), and provide secrets
  via a mounted `secrets:` dir (`${secret:...}`) or `environment` (`${env:...}`).
  The `api`/`mcp-http` `command:` drop their `--host 0.0.0.0` so the validated
  `server` object governs the listener (section 5), and the playground UI proxy is
  never given a non-loopback `HYPERSET_UI_HOST` (loopback-only, section 5). The demo
  overlay sets `HYPERSET_CONFIG=config/demo.yaml` in `docker-compose.demo.yml`.
- **Kubernetes (credible path, sketched not built).** The overlay is a `ConfigMap`
  mounted at `/etc/hyperset/`; secret refs resolve from a `Secret` surfaced as env;
  `HYPERSET_CONFIG` points at the mounted overlay. A Helm chart's `values.yaml`
  renders the overlay ConfigMap and the Secret. No app code differs between Compose
  and K8s -- only where the overlay file and the secret env come from.

## 8. Secret resolution and redaction

- **Two reference forms, distinct and non-interchangeable.** `${env:NAME}` reads
  environment variable `NAME`. `${secret:NAME}` goes through a pluggable secret
  PROVIDER; the default provider is a mounted directory (one file per name, at
  `${HYPERSET_SECRETS_DIR:-/run/secrets}/NAME`), which is what Docker/K8s already
  surface secrets as. The provider interface (`get(name) -> str | None`) is the seam
  a customer swaps for Vault/cloud secret managers later; a `${secret:...}` whose
  provider returns nothing is a FATAL unresolved-ref error.
- **A `<ref>` field MUST hold one reference and nothing else** -- a plaintext value,
  or a string that merely CONTAINS a reference among other text, is a validation
  error, so a secret can never be half-inlined.
- **PATH-ONLY redaction.** The loader knows, from the schema, which config PATHS are
  secret-typed. Redaction is by PATH, never by string-matching the resolved value
  (value-matching misses a re-encoded secret and risks redacting innocent text). A
  resolved secret value is NEVER interpolated into: a type error, a startup failure,
  a traceback, a config dump/echo, a log line, a status/health endpoint, or an
  error's `subject`. Any config serialization renders a secret-typed path as
  `"<redacted>"`. The in-memory value is read only at the point of use (the HTTP
  client, the DB connect) and never returned by a config accessor that can reach a
  log or a response.

## 9. Legacy environment migration and the override allowlist

The ADR's "no runtime behaviour changes until wiring lands" claim only holds if
EVERY existing `HYPERSET_*` read has a defined fate, per-field and per-secret-status
-- a broad, wrongly-typed row would break real deployments once an unknown
`HYPERSET_*` seen by the SERVER LOADER is fatal. The complete map below is the
acceptance artifact of the wiring bead (hy-tc4o); each row is one of: MAPPED to a
config path, RETAINED (loader/Compose control, not a config value), OUT-OF-SCOPE
(read only by a dev/CI entrypoint that never constructs the loader), or REJECTED
loudly (an unknown `HYPERSET_*` that reaches the server loader -> fatal).

**Scope of the reject rule.** "Unknown `HYPERSET_*` is fatal" applies to the
environment of the SERVER process that constructs the loader (`serve http`, `serve
mcp --http`, and the UI listener, section 5). It does NOT apply to standalone dev/CI
entrypoints (`pytest`, `scripts/`, eval harnesses, seed scripts) that read their own
`HYPERSET_*` vars and never build the config loader; those are OUT-OF-SCOPE below so
no read is unaccounted, but they are not rejected.

Each row is one field with its exact type and secret status (SECRET = a value that
MUST become a `${secret:...}`/`${env:...}` ref and be redacted; PLAIN = non-secret):

| Env var | Type | Secret | Fate -> config path / note |
|---|---|---|---|
| `HYPERSET_DATABASE_URL` | url str | SECRET (embeds password) | MAPPED -> `connections.system_db.url` (whole URL secret-typed) |
| `HYPERSET_ANALYTICS_DB_URL` | url str | SECRET (embeds password) | MAPPED -> `connections.analytics_db.url` |
| `HYPERSET_DB_USER` | str | PLAIN | RETAINED (Compose-only; composes `DATABASE_URL`, not read by app) |
| `HYPERSET_DB_NAME` | str | PLAIN | RETAINED (Compose-only; composes `DATABASE_URL`) |
| `HYPERSET_DB_PORT` | int | PLAIN | RETAINED (Compose-only; host port publish) |
| `HYPERSET_DB_PASSWORD` | str | SECRET | RETAINED as a secret input (Compose composes `DATABASE_URL`; wired via a `${secret:...}` ref) |
| `HYPERSET_SUPERSET_BASE_URL` | url str | PLAIN | MAPPED -> `connections.superset.base_url` |
| `HYPERSET_SUPERSET_VERSION` | str | PLAIN | MAPPED -> `connections.superset.version` |
| `HYPERSET_SUPERSET_USERNAME` | str | SECRET (credential) | MAPPED -> `connections.superset.username` (ref) |
| `HYPERSET_SUPERSET_PASSWORD` | str | SECRET | MAPPED -> `connections.superset.password` (ref) |
| `HYPERSET_DATAHUB_BASE_URL` | url str | PLAIN | MAPPED -> `connections.datahub.base_url` |
| `HYPERSET_DATAHUB_VERSION` | str | PLAIN | MAPPED -> `connections.datahub.version` |
| `HYPERSET_DATAHUB_TOKEN` | str | SECRET | MAPPED -> `connections.datahub.token` (ref) |
| `HYPERSET_SECRET_KEY` | str | SECRET | MAPPED -> secret-typed signing-key path (ref) |
| `HYPERSET_WRITEBACK_TOKEN` | str | SECRET | MAPPED -> `playground.writeback.token` (ref; already referenced by name in the UI) |
| `HYPERSET_CONNECTOR_ENCRYPTION_KEY` | str | SECRET | MAPPED (RESERVED) -> secret-typed KEK path; declared in `.env.example`, no current reader -- kept as a secret path for its future consumer, never plain env |
| `HYPERSET_MODEL_PROVIDER` | enum str (`ollama`/`openai`) | PLAIN | MAPPED -> `models`/`providers` selection |
| `HYPERSET_OPENAI_MODEL` | str | PLAIN | MAPPED -> `models.*` |
| `HYPERSET_OLLAMA_MODEL` | str | PLAIN | MAPPED -> `models.*` |
| `HYPERSET_OPENAI_BASE_URL` | url str | PLAIN | MAPPED -> `providers.openai.base_url` |
| `HYPERSET_OPENAI_REASONING_EFFORT` | enum str | PLAIN | MAPPED -> `providers.openai.reasoning_effort` |
| `HYPERSET_OPENAI_MAX_COMPLETION_TOKENS` | int | PLAIN | MAPPED -> `providers.openai.max_completion_tokens` |
| `HYPERSET_OLLAMA_BASE_URL` | url str | PLAIN | MAPPED -> `providers.ollama.base_url` |
| `HYPERSET_OLLAMA_MAX_TOKENS` | int | PLAIN | MAPPED -> `providers.ollama.max_tokens` |
| `HYPERSET_EMBEDDING_PROVIDER` | enum str | PLAIN | MAPPED -> `providers.embedding.provider` |
| `HYPERSET_EMBEDDING_BASE_URL` | url str | PLAIN | MAPPED -> `providers.embedding.base_url` |
| `HYPERSET_EMBEDDING_MODEL` | str | PLAIN | MAPPED -> `providers.embedding.model` |
| `HYPERSET_EMBEDDING_API_KEY` | str | SECRET | MAPPED -> `providers.embedding.api_key` (ref) |
| `HYPERSET_PLAYGROUND_ENABLED` | bool | PLAIN | MAPPED -> `playground.enabled` |
| `HYPERSET_PLAYGROUND_AGENTS_JSON` | json str | PLAIN | MAPPED -> `playground.agents` |
| `HYPERSET_PLAYGROUND_MODELS_JSON` | json str | PLAIN | MAPPED -> `playground.models` |
| `HYPERSET_PLAYGROUND_DEFAULT_AGENT` | str | PLAIN | MAPPED -> `playground.default_agent` |
| `HYPERSET_PLAYGROUND_DEFAULT_MODEL` | str | PLAIN | MAPPED -> `playground.default_model` |
| `HYPERSET_PII_GUARD` | bool | PLAIN | MAPPED -> `features.pii_guard` |
| `HYPERSET_PII_ACTION` | enum str | PLAIN | MAPPED -> `features.pii.action` |
| `HYPERSET_PII_ENTITIES` | csv str | PLAIN | MAPPED -> `features.pii.entities` |
| `HYPERSET_PII_SPACY_MODEL` | str | PLAIN | MAPPED -> `features.pii.spacy_model` |
| `HYPERSET_AUTHZ_ENABLED` | bool | PLAIN | MAPPED -> `auth.enabled` (loader still WRITES it through until hy-ax9w) |
| `HYPERSET_OIDC_ISSUER` | url str | PLAIN | MAPPED -> `auth.oidc.issuer` |
| `HYPERSET_OIDC_AUDIENCE` | str | PLAIN | MAPPED -> `auth.oidc.audience` |
| `HYPERSET_OIDC_JWKS_URL` | url str | PLAIN | MAPPED -> `auth.oidc.jwks_url` |
| `HYPERSET_OIDC_ROLES_CLAIM` | str | PLAIN | MAPPED -> `auth.oidc.roles_claim` |
| `HYPERSET_CONTEXT_MAX_FILES` | int | PLAIN | MAPPED -> `context.max_files` |
| `HYPERSET_CONTEXT_CACHE_DIR` | path str | PLAIN | MAPPED -> `context.cache_dir` |
| `HYPERSET_UI_HOST` | str (bind host) | PLAIN | MAPPED -> `server.ui_bind` (governed by the section-5 non-loopback rule) |
| `HYPERSET_UI_PORT` | int | PLAIN | MAPPED -> `server.ui_port` |
| `HYPERSET_HTTP_BASE_URL` / `HYPERSET_BASE_URL` | url str | PLAIN | MAPPED -> `playground.upstream_base_url` (UI proxy target) |
| `HYPERSET_API_PORT` | int | PLAIN | RETAINED (Compose host-port publish `${HYPERSET_API_PORT}:8080`; app port is the CLI `--port`, section 5) |
| `HYPERSET_MCP_HTTP_PORT` | int | PLAIN | RETAINED (Compose host-port publish; app port is the CLI `--port`) |
| `HYPERSET_REVIEW_UI_PORT` | int | PLAIN | RETAINED (Compose publish; declared in `.env.example`, no app reader) |
| `HYPERSET_CONFIG` | path list str | PLAIN | RETAINED (loader control -- selects overlays, section 3) |
| `HYPERSET_SECRETS_DIR` | path str | PLAIN | RETAINED (loader control -- default secret provider dir, section 8) |
| `HYPERSET_RECORD`, `HYPERSET_STABILITY_REPETITIONS` | -- | -- | OUT-OF-SCOPE (eval harness only; never in the server env) |
| `HYPERSET_COMPOSE_DEMO`, `HYPERSET_COMPOSE_DATAHUB`, `HYPERSET_REQUIRE_LIVE` | -- | -- | OUT-OF-SCOPE (test gating only) |
| `HYPERSET_CRITIC_LOGINS`, `HYPERSET_REQUIRED_CHECKS`, `HYPERSET_MERGE_ACK`, `HYPERSET_MR_BEAD`, `HYPERSET_MIN_MERGE_APPROVALS` | -- | -- | OUT-OF-SCOPE (`scripts/` merge/refinery tooling only) |
| any other `HYPERSET_*` in the server env | -- | -- | REJECTED loudly -> fatal at startup, never silently ignored |

`OPENAI_API_KEY` (no `HYPERSET_` prefix) is read by the OpenAI model path and is out
of the `HYPERSET_*` allowlist entirely; once wired it becomes
`providers.openai.api_key: <ref>` resolving via `${env:OPENAI_API_KEY}`.

Migration is staged: the loader ACCEPTS the MAPPED legacy vars during hy-tc4o
(reading each into its typed path with a one-release deprecation warning), then the
mapped ones are removed from Compose in favour of the overlay. No var is dropped
without a row here.

## 10. Forks (DECISION NEEDED -- bubbled to the mayor)

- **F1 Format.** RECOMMEND **YAML**: comments, human-editable, native to K8s
  ConfigMaps, and consistent with the governed `manifest.yaml`. Alternatives: TOML
  (matches `pyproject`, no comments-in-data ergonomics for nested overlays), JSON
  (no comments, poor for hand-edited overlays).
- **F2 Precedence.** RECOMMEND base < ordered-overlays < allowlisted-env <
  secret-resolution, deep-merge maps / replace scalars+lists / explicit-null unsets.
  Sub-fork: env-vs-file authority. RECOMMEND FILES authoritative; env only for the
  allowlist + secret refs, so config is reproducible from checked-in files, not
  from ambient environment.
- **F3 Auth-toggle threading.** RECOMMEND the section-5 fail-closed rule
  (non-loopback requires auth+oidc) with the loader mapping to existing env until
  the wiring bead lands, owned jointly with hy-7q3o. Alternative: keep auth entirely
  in hy-7q3o and have config only reference it -- rejected because the non-loopback
  safety rule must live where bind and auth are validated together.

## 11. Impl decomposition (follow-on beads, DESIGN not built here)

1. Loader + precedence deep-merge + typed schema validation, fail-closed, no demo
   fallback (the core; base.yaml + demo.yaml overlays).
2. Secret-reference resolution (`${env:...}`/`${secret:...}`; redaction; reuse
   `security/secret_box.py` for at-rest).
3. Wire existing `HYPERSET_*` reads through the settings object, domain by domain
   (DB, models/providers, playground, connections, features).
4. Auth section + non-loopback safety validation + wire authz/oidc (with hy-7q3o).
5. Kubernetes path (ConfigMap + Secret + a Helm values example).
