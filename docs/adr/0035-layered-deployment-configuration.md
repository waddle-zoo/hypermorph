# 0035: Layered deployment configuration — customer overlays over checked-in defaults, fail-closed, secrets by reference

> **Amended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** Adds an authority/sources config section to this model: per-source backend `type`, adapter ref, native endpoint, secret references (`${env:}`/`${secret:}`), and multi-source routing/precedence with per-workspace/domain/tenant scope — all under the same fail-closed, secret-by-reference rules this ADR fixes.

Status: PROPOSED — awaiting Overseer ratification. Design-first, builds nothing: this
ADR fixes the CONFIGURATION CONTRACT — how a customer layers their deployment config
over Hyperset's defaults without editing upstream — so the later loader/validation/
wiring slices all speak one agreed model. It adds no served operation and changes no
served shape: `SCHEMA_VERSION` does not move and `tools_hash` stays
`sha256:fe930a003b731211`. The worked schema, precedence table, and examples live in
`docs/development/config-layering-proposal.md` (hy-c5p7); this ADR records only the
contract and the decisions. Three shape decisions are FORKS pending the ruling
(format, precedence semantics, auth-toggle threading — proposal section 8).

Extends ADR-0030 (the authorization boundary: the gate is default-off and enabling it
is deliberate; this ADR makes ENABLING it a fail-closed config act rather than a loose
env flag) and ADR-0012 (the customer's own Git/repo owns their deployment inputs;
Hyperset ships defaults and never requires editing upstream to deploy). It is the
config companion to the feature-parity audit (hy-w8q2), which recorded that Hyperset
runs unauthenticated by default; this ADR is how that posture is changed SAFELY.

## Context

Configuration today is ~30+ `HYPERSET_*` environment variables read by scattered
`os.environ.get` calls, set in compose files, documented only in `.env.example`. There
is no schema, no validation, no precedence, and secrets sit as plain env values. A
customer cannot layer config over defaults without editing upstream, and an invalid or
partial config silently falls back to whatever a call site defaults to.

## Decision

1. **Layered sources, deep-merged, lowest to highest:** checked-in `config/base.yaml`
   (ALWAYS loaded implicitly, NEVER named in `HYPERSET_CONFIG`) < ordered overlay
   file(s) named by `HYPERSET_CONFIG` < an allowlisted set of env overrides <
   secret-reference resolution. Unset `HYPERSET_CONFIG` = base only. Deep-merge
   mappings; overlays replace scalars and lists wholesale; an explicit `null` unsets.
2. **Safe parse + two-phase, fail-closed validation:** files are parsed by a
   `yaml.SafeLoader`-DERIVED custom loader invoked EXPLICITLY (`yaml.load(stream,
   Loader=HypersetConfigLoader)`) — NOT bare `yaml.safe_load`, which cannot reject
   duplicate keys or anchors and cannot disable implicit typing, and which
   post-processing cannot recover. The custom loader REJECTS duplicate mapping keys
   (overridden `construct_mapping`), custom/unknown tags and YAML anchors/aliases
   (overridden composition), and has its implicit resolvers CLEARED so every scalar
   is a string and the typed schema (not YAML implicit typing) does every coercion.
   The merged PRE-resolution shape and the
   POST-secret-resolution typed values are BOTH validated. Any unknown key, wrong
   type, missing required field, unresolvable ref, absent named overlay, or auth-
   safety failure is FATAL, naming the config PATH (never the value). There is NO
   silent fallback to demo defaults — `config/demo.yaml` is an explicit overlay.
3. **Secrets by reference only, redacted by path:** a secret-typed field MUST hold
   exactly one reference — `${env:NAME}` (an environment variable) or `${secret:NAME}`
   (a pluggable secret provider, default a mounted secrets dir; the two forms are
   distinct). The resolved value lives only in write-only in-memory settings and is
   redacted by config PATH — never string-matched — everywhere: no secret value is
   interpolated into a type error, startup failure, traceback, config dump, log, or
   status endpoint. A plaintext or partially-inlined secret in a reference field is a
   validation error.
4. **Safe auth enablement, enforced at EVERY listener (ties hy-7q3o):**
   `auth.enabled: true` requires a complete `auth.oidc` block, and a non-loopback
   effective bind on ANY network listener REQUIRES `auth.enabled: true`. The rule
   closes over all THREE shipped listeners — `serve http`, `serve mcp --http`, AND
   the `playground/ui/app.py` proxy (`HYPERSET_UI_HOST`) — each reads the validated
   `server` object, NOT a CLI `--host` or Compose `command:`, for its bind; a
   `--host`/`--port` that conflicts with the validated config (a non-loopback bind
   while auth is off, or a bind disagreeing with `server.bind`) is a fatal startup
   error, not a silent override. The UI proxy has no verifier of its own and so is
   LOOPBACK-ONLY: a non-loopback `HYPERSET_UI_HOST` is always fatal. A config cannot
   expose any surface to a network without a verifier; loopback is the only effective
   bind permitted to run unauthenticated (ADR-0030).

## Consequences

- A customer keeps a checked-in overlay (secrets by reference) in THEIR repo and never
  edits upstream; per-env is per-overlay.
- The default posture is unchanged until the wiring slices land: base defaults keep
  auth off and bind loopback, and the loader maps `auth.*` to today's
  `HYPERSET_AUTHZ_ENABLED`/`HYPERSET_OIDC_*`. The "no runtime behaviour changes" claim
  holds ONLY once every existing `HYPERSET_*` read has a defined fate — the complete
  MAPPED / RETAINED / REJECTED-loudly migration table (proposal section 9) is the
  acceptance artifact of the wiring bead; no env var is dropped without a row.
- Docker Compose mounts the overlay, moves the listener bind into `server.bind`
  (dropping `--host 0.0.0.0`), and provides secrets via a mounted dir/`environment`;
  Kubernetes uses a ConfigMap for the overlay and a Secret for references, with no
  app-code difference.
- Impl is decomposed into follow-on beads (proposal section 11): loader+precedence+
  validation; secret-ref resolution; wire existing settings through; auth section +
  safety; the Kubernetes path. This ADR builds none of them.
