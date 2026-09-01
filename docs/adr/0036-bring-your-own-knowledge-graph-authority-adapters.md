# 0036: Bring-your-own knowledge graph — Git is one authority backend, and a provider-neutral adapter contract makes a customer-native KG a first-class authority for governed reads and write-backs

Status: PROPOSED — awaiting Overseer ratification. Design-first, builds nothing. This ADR
fixes the AUTHORITY-BACKEND CONTRACT so a customer's own knowledge graph can be a
first-class governed authority without migrating their content into a Hyperset-specific
shape. It adds no served operation and moves no served shape: `SCHEMA_VERSION` stays `20`
and `tools_hash` stays `sha256:fe930a003b731211`. It does NOT self-ratify
PROPOSED→ACCEPTED — that is Brandon's decision via the Overseer.

Partially SUPERSEDES ADR-0012 (Git-as-THE-authority → Git-as-ONE-authority-backend).
Extends ADR-0028 (the adapter boundary), ADR-0030 (the authorization boundary), ADR-0035
(layered config), ADR-0005 (human approval), and ADR-0001/ADR-0017 (governed/observed
split). Reconciles ADR-0025/0026/0027/0031/0034/0006 per the amendment table below. Folds
the multi-source requirement (overseer hq-hnrf).

## Context

V0 hard-wires ONE authority shape: a configured Git repository/ref/path is *the*
authoritative source of domain meaning (ADR-0012), snapshotted at an exact commit SHA. The
coupling is real and load-bearing in code, not just prose:

- `hyperset/db/models.py` — `ContextSource` is Git-shaped: non-null `repository`/`ref`/`path`
  and `UniqueConstraint("repository","ref","path")` (`uq_context_source_identity`); no
  backend-type discriminator. `ContextSnapshot` identity is `(source_id, commit_sha)`.
  `WritebackConfig` carries `repository`/`base_ref`/`manifest_path` and GitHub-only tokens.
- `hyperset/context/sync.py` — `sync_git_context(...)` is the sole ingest entry; it builds a
  `GitContextReader` and keys each snapshot on `commit_sha`.
- `hyperset/context/git.py` — `GitContextReader` is the only reader.
- `hyperset/bundle/resolver.py` — the served `context_authority` hard-codes
  `{"type": "git", ...}` and `provenance_refs` are `git_context:{id}@{commit_sha}`.
- `hyperset/flywheel/git_pr.py` — write-back is a Git PR only (`MANIFEST_FILE=manifest.yaml`).
- `hyperset/transport/review_runtime.py` + `operations.py`/`mcp.py` — the served write-back
  op is `propose_review_to_git`.

An enterprise customer often ALREADY governs their domain knowledge in a knowledge graph or
catalog (their system of record, with its own human-review/approval workflow). Today Hyperset
would force them to either (a) copy that content into a Git manifest — a second, lossy,
drifting copy — or (b) accept their KG only as *observation*, denying it as governed truth.
Both are an adoption tax the [feature-parity audit](../development/feature-parity-audit.md)
and the enterprise-readiness work flag. ADR-0028 already established that a customer's native
governance shape reaches v0 through an ADAPTER without reshaping their data; this ADR lifts
that boundary from "a customer-side file over a Git corpus" up to the AUTHORITY BACKEND
itself.

## Decision

### 1. Git is ONE authority backend, not mandatory storage or model

`ContextSource` gains a **backend `type` discriminator** and a typed **native-identity**
payload. `(repository, ref, path)` becomes the *Git instance* of that payload; snapshot
identity generalizes from `(source, commit_sha)` to `(source, native_revision)`. Git remains
the reference implementation and the default, but it is no longer the only shape a governed
authority may take.

### 2. A customer-native KG is a first-class authority backend

A native knowledge graph is a first-class authority for governed **reads AND write-backs** —
not demoted to observation, and not promoted merely by being *connected*. The
governed/observed split (ADR-0001/0017) is preserved *per backend*: a KG backend contributes
governed meaning; a connector still contributes observation. Being reachable is necessary,
not sufficient — a source is governed because it is configured as an authority, not because a
connection exists.

### 3. Hyperset's schema is a flexible runtime/adapter contract; the customer does not reshape their native schema

The customer never rewrites their KG into a Hyperset-specific schema. The adapter maps native
→ v0 at request time, and ADR-0028's four invariants hold unchanged on KG adapters:

1. a closed, deterministic transform (invisible translation, no free interpretation);
2. adapter-authored fields are ATTRIBUTED and degrade a domain's provenance to `mixed`;
3. no second store or lifecycle is created — the native backend stays the system of record;
4. an unmapped native key is an ERROR, never a silent drop.

### 4. Adapter responsibilities — the only component that knows a backend is not Git

An authority-backend adapter owns, and ONLY it owns: native-identity mapping (never a
fabricated identity — ADR-0029); shape mapping across the ADR-0028 boundary; pagination;
freshness via a native revision token (an unchanged revision is a no-op); conflict signalling;
capability discovery (declared, not assumed); exposing native revisions as authority identity;
and provenance preservation. An adapter MAY change shape; it may NOT create authority, approve,
or merge. Nothing else in the resolver, validator, or write-back path branches on backend type.

### 5. Read contract (backend-neutral, one implementation over HTTP + MCP)

Every governed read runs through the same `run_operation` path on both transports and returns
**byte-identical** output across HTTP and MCP: discovery, resolution, graph exploration
(declared-never-inferred — ADR-0031/0034), and validation. The served bundle gains a
**discriminated `context_authority.type`** (`git` | `kg` | …) plus a typed native identity, a
native **revision pin**, and disclosed `warnings`/`degraded`/`partial` states — never a silent
gap. The Git instance's identity is preserved **byte-for-byte** (`type:"git"`,
`git_context:{id}@{commit_sha}`), so existing consumers see no change.

### 6. Write-back contract (backend-neutral, human-reviewed, no silent mutation)

A write-back is always: propose → preview (a diff against the *current native revision*) →
validate. Human review is MANDATORY (ADR-0005): the proposal stays **unapproved** until a
human acts **in the backend's own workflow**. Submission is NATIVE — a KG's native
mutation/approval workflow, or a GitHub PR for Git (`git_pr` unchanged) — and returns a
receipt, the targeted revision, and an audit record. A conflict or concurrent native change is
**reported, never overwritten**. Hyperset never approves or merges on any backend. The served
op generalizes: `propose_review_to_git` → `propose_context_change` (the Git alias is retained;
this is a SEQUENCED follow-on that moves `tools_hash`, not taken here).

### 7. Multi-source routing, precedence, and isolation

Exactly **one authority per domain**. The globally-unique, collision-checked domain slug rule
(ADR-0031: sync REFUSES a second source claiming an already-claimed domain) generalizes across
backends. Explicit precedence is DECLARED config (ADR-0035), never inferred. Mixed Git and KG
sources sit side by side under one uniform catalog/resolve/graph surface. Scope is
per-workspace/domain, and **per-tenant isolation is first-class**: no cross-tenant read or
write, and NO cross-target writes (a KG-owned domain's proposal is never written to Git, and
vice versa).

### 8. Auth, secrets, authz, fail-closed, capability

The ADR-0030 authorization boundary is unchanged and backend-neutral: the `(domain,
source_ref, field)` scope and the fail-closed default-deny gate cover KG-backed sources
identically, **plus** per-authority-target and per-tenant isolation as first-class scope
dimensions. KG credentials follow the same floor as the Git write-back token
(ADR-0026/0027 are the Git *instances*): secret-by-reference (ADR-0035, `${env:}`/`${secret:}`),
encrypted-at-rest, minted-not-stored. Access is enforced **server-side in the shared
executor** — an adapter never decides access, and a backend's own ACLs are *additive*, never a
substitute for Hyperset's gate. Fail closed on an unreachable backend, an invalid credential,
an undecryptable secret, an unverifiable revision, or an unsupported capability — never a
fallback to an unauthenticated or ungoverned answer. Capability limits are DECLARED, not
assumed.

### 9. Backward compatibility and rollout

Git-only deployments stay **byte-identical**: `context_authority.type` is `"git"`, the Git PR
path is untouched, and `SCHEMA_VERSION`/`tools_hash` do not move until a specific slice takes
them. The `ContextSource` `type` discriminator lands as an ADDITIVE migration (existing rows →
`type:"git"`). Each sequenced served-shape move (the discriminated `context_authority`, the
native fields, `propose_context_change`) takes `SCHEMA_VERSION` by merge order with a full
served-surface sweep, and default-deny on an unknown enum protects old clients (ADR-0018). A
customer adopts a KG by ADDING a configured source through an ADR-0035 overlay — never by
editing upstream and never by migrating KG content into Hyperset.

## Rejected alternatives

- **Force a Hyperset-specific customer schema.** Reintroduces exactly the ADR-0028 failure
  modes — silent loss, misattribution, drift — now at the AUTHORITY level. A customer will not
  maintain a second lossy copy of their system of record.
- **Git-only write-back for a KG customer.** Either bypasses the customer's own governance
  workflow or dead-ends (a proposal no one can approve in their world).
- **Treat a native KG as observation-only.** Denies the customer's system of record its status
  as governed truth — the whole point of adoption.
- **Backend-specific logic in the resolver / write-back path.** Re-hard-codes the coupling this
  ADR removes and violates ADR-0028's rule that exactly one component knows the backend.
- **A new served status value or a bespoke per-backend bundle shape.** Per ADR-0018 and
  ADR-0028's fork discipline, a consumer would misread it and it forces a version move anyway; a
  discriminated `type` plus disclosed `warnings` carries the same information additively.

## Implementation gates

- **PROPOSED → ACCEPTED** (Brandon, via Overseer): a typed, UNWIRED adapter-interface stub
  exists — imported by no served path, `tools_hash`/`SCHEMA_VERSION` unmoved, mirroring the
  ADR-0030 stub discipline — and the supersession/amendment edits in the table below have
  landed.
- **ACCEPTED → IMPLEMENTED** (sequenced slices, each on its own gate): the `ContextSource`
  `type`-discriminator additive migration (Git rows → `type:"git"`, regression-proven); the
  discriminated `context_authority` + native fields shipped with a merged `SCHEMA_VERSION` move
  and a full served-surface sweep; `propose_context_change` shipped (Git alias retained, the MCP
  diff reviewed); and config carrying authority-backend selection/routing/precedence with
  fail-closed secret-ref credentials.

## Acceptance tests (all required before IMPLEMENTED)

1. **One native (non-Hyperset) KG READ path** — discovery + resolution + graph + validation
   against a real non-Git KG adapter: the bundle's `context_authority.type != "git"`, native
   ids and native revision are carried, nothing is reshaped, and a stale/unreachable backend
   DEGRADES with a disclosed warning (never a silent gap).
2. **One native KG WRITE-BACK path** — preview diff against the native revision; the proposal is
   held UNAPPROVED; it is submitted to the KG's native workflow; a native receipt + revision +
   audit come back; Hyperset never approves or merges; a concurrent native modification is
   reported, not overwritten.
3. **Git REGRESSION** — a Git-only deployment is byte-identical: `context_authority.type ==
   "git"`, `provenance_refs` unchanged, the `git_pr` path unchanged, and (at the pre-move
   commits) `SCHEMA_VERSION == 20` with `tools_hash == sha256:fe930a003b731211`.
4. **Fail-closed + isolation** — an unauthorized caller, an invalid KG credential, an
   unsupported capability, and a cross-target or cross-tenant write are EACH refused completely
   and non-disclosingly, with no fallback.
5. **Adapter boundary** — an unmapped native key is REFUSED; an adapter-authored field is
   attributed, owned, and degrades its domain to `mixed`; and no adapter can create authority.

## Reconciliation (surgical amendments applied by this PR)

| ADR | Action | Change |
|---|---|---|
| 0012 | PARTIALLY SUPERSEDED | "one configured Git repository/ref/path as THE authority" → one authority *backend*; authority = the backend's native human-reviewed revision (Git merge for Git). 0012's principle preserved: the customer's workflow owns governance; Hyperset snapshots, never authors or approves. |
| 0028 | EXTEND | Decision 3 "authority remains a human Git merge" → "a human-reviewed revision in the authority backend (Git merge for Git; native approved revision for a KG)". The four invariants carry to KG adapters unchanged. |
| 0030 | AMEND | The `(domain, source_ref, field)` scope + fail-closed gate cover KG-backed sources identically; per-authority-target and per-tenant isolation are first-class scope dimensions (no cross-target/cross-tenant leakage). |
| 0031 / 0034 | AMEND | "declared in the customer's Git manifest" → "declared in the authority backend, surfaced via the adapter". The `evidence:"git"` string names a provider-neutral GOVERNED class; a KG adapter maps native governed edges onto it (documented as *governed*, not from-Git), OR a sequenced follow-on renames the class to `"governed"` (a `SCHEMA_VERSION` move). `contains`/`depends_on`/`joinable_on` stay declared-never-inferred. |
| 0025 | AMEND | `propose_review_to_git` is the Git-target INSTANCE of a general `propose_context_change`; the neutral op moves `tools_hash` and is a SEQUENCED follow-on; the Git alias is retained. Proposal-only / PII-guarded / no-silent-merge preserved and generalized. |
| 0026 / 0027 | AMEND (scope note) | The Git write-back token is the Git INSTANCE of a general "authority-backend write credential": secret-by-reference, encrypted-at-rest, fail-closed, minted-not-stored. A KG target brings its own credential under the same floor. |
| 0035 | AMEND | Add an authority/sources config section: per-source backend type, adapter ref, native endpoint, secret refs (`${env:}`/`${secret:}`), and multi-source routing/precedence with per-workspace/domain/tenant scope. |
| 0006 | AMEND (FLAGGED, not taken) | `context_authority` gains a discriminated `type` plus native-id / authority-identity / native-revision / degraded fields. This moves `SCHEMA_VERSION` (currently `20`) by merge order with a full served-surface sweep — flagged by this design-first ADR, executed by a later slice. |
| 0005, 0001, 0017 | CITE (no amendment) | The human-review floor is strengthened to every backend; the governed/observed split is preserved per backend. |

## Consequences

PROPOSED and design-only: this changes no runtime behaviour. No non-Git backend exists, the
adapter stub is unwired, and a Git deployment is byte-identical. Git is preserved as
first-class and as the reference implementation. The enterprise adoption tax — copy your
governance into our shape, or accept it only as observation — is removed. ADR-0012's principle
is preserved and generalized; ADR-0028's boundary is lifted to any backend; the ADR-0030 gate,
ADR-0035 config/secrets, and ADR-0005 human-review floor all extend unchanged.

**Self-reported weakness:** the `evidence:"git"` string still names a governed class even
under a KG backend; renaming it to `"governed"` is deferred to its own `SCHEMA_VERSION` slice
so that committed recordings can be reconciled first. Ratification is Brandon's via the
Overseer; no implementation slice lands until ACCEPTED.
