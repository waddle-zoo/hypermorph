# Hyperset Vision & Roadmap — V0 → V1 → V2

> [!NOTE]
> status: planning and vision document, not an ADR and not binding.
> Where this document and an accepted ADR or `docs/v0-foundation.md`
> disagree, the ADR and foundation win. V2 content is direction, not
> commitment; every V2 item still requires evaluator evidence and its own
> ADR before it enters scope (ADR 0009 gates apply).

> [!NOTE]
> **Identity status amendment — 2026-08-30.** OIDC is no longer wholly future
> work: the fail-closed RS256/JWKS bearer verifier, transport authorization,
> Authorization-Code + PKCE login routes, signed session, and CSRF protection
> have shipped and are present-but-default-off. Remaining identity roadmap work
> is a reviewed per-principal grant source/scoped policy, a live configured-OIDC
> smoke, SAML/gateway integration, and full multi-user SSO/ACL operations. There
> is no Hyperset-local username/password login.

**Hyperset is the flexible-yet-governed analytics Hive-Mind: existing systems
contain the assets; Hyperset supplies the shared understanding.**

Hyperset is a connector-driven analytics context system. It observes what
BI tools and catalogs already contain, keeps humans in control of what that
metadata *means*, and serves that meaning to AI agents through one typed
contract: the **ContextBundle**. The long-term product is an AI-first
governed context platform an enterprise can place above any analytics
stack.

**Product goal (ADR 0041):** a **flexible-yet-governed knowledge graph that
improves through use**. Humans own the canonical docs, the corpus
inclusion/ignore decisions, and the approvals; agents may inspect messy
sources, reason across them, propose nodes/edges/doc changes, and learn from
accepted/rejected/ignored feedback. The governed domain graph of V0 is the
*canonical core* of that graph, not the destination — a later layer carries
first-class **observed/proposed** nodes and edges, each provenanced, ACL'd,
confidence- and staleness-marked, and `evidence: "observation"`/`"proposal"`
(never `git`). Those edges need not be predeclared to exist, and are never
canonical authority until a human accepts them and writes them back to Git.
`declared-never-inferred` governs *canonical promotion*, not the graph's
existence, and the graph stays flexible without a graph database (a bounded
cycle-safe walk over the existing store). This is the V2 flywheel below made
the stated destination; the nine ADR-0019 floors hold at every step.

The roadmap is three phases, each earning the next:

| Phase | Name | One-line claim it proves |
| --- | --- | --- |
| **V0** | Prove | A small local model can find and correctly use human-governed Git context from an ordinary question, measurably beating raw metadata end to end. |
| **V1** | Reach | An assist mode serves questions governance is silent on — labeled, bounded, and never confused with governance. The size of that uncovered set is measured by #141, not assumed here. |
| **V2** | Platform | The governed/assist loop becomes a flywheel: assist output becomes review work, review work becomes governed coverage, across an enterprise estate. |

The shipped demo/runtime uses OpenAI/Luna. The small local model in the V0
proof is an isolated benchmark arm, not a product runtime dependency.

---

## 1. The core object: the ContextBundle

Everything Hyperset does converges on one answer shape. One `ContextBundle`
serves HTTP, MCP, and the evaluation harness — there is no second response
shape (`hyperset/bundle/schema.py`, `SCHEMA_VERSION = 9`).

### 1.1 The flow that produces a bundle

```text
Superset 6.1.0            DataHub OSS               Customer Git repo
 (REST / export)           (GraphQL)                 (manifest.yaml,
      |                        |                      context.md,
      v                        v                      evals.yaml)
  read-only connectors: immutable ObservedAssetVersion      |
  rows + ConnectorChange rows per sync                      |
      |                                                     v
      |                                        ContextSnapshot (pinned
      |                                        repo / ref / path / commit)
      |                                                     |
      +------------------ processor ------------------------+
      |   run_sync_processing: pinned Git context vs
      |   current observations -> deterministic
      |   FindingCandidate (approved_expression_drift)
      |                        |
      |                        v
      |            human review = a Git commit in the
      |            customer's repository (ADR 0012)
      |                        |
      v                        v
  ObservedEvidenceResolver: declared EvidenceRefs
  resolved to exact source-native identity (ADR 0017)
                               |
                               v
        natural-language question
                               |
                lightweight planner + bounded discovery
                               |
                    exact ContextDirective
                               |
        resolve_analytics_context(query, ContextDirective)
                               |
                               v
                        ContextBundle
        request | resolution | context_authority | instructions
        linked_evidence | domain_graph | provenance_refs | execution
                               |
              +----------------+----------------+
              v                v                v
         HTTP /v0/*       MCP tools        evaluation arms
              |                |                |
              v                v                v
        validate_analytics_plan(bundle_id, plan) -> PlanValidation
        (deterministic, never executes SQL)
```

### 1.2 Bundle anatomy and provenance classes

The bundle is a provenance ledger, not a search result. Every claim in it
carries the class it came from:

- **`request`** — the caller's query and `ContextDirective`, echoed
  verbatim. The directive names exact things to retrieve (`domains` +
  `concepts`, `asset_refs`, `max_hops`, `context_budget`). Retrieval is
  directed, never interpreted by the resolver: semantic selection belongs to
  the calling agent or Hyperset's supported lightweight reference path
  (GitHub #70, ADR 0022).
- **`resolution.status`** — a summary: `governed | mixed | observed_only |
  no_match`, always the *weakest* class any claim in the answer carries.
  V1 adds one proposed value, `assisted` (ADR 0019, awaiting
  ratification).
- **`context_authority`** — which Git repository, path, commit, and
  snapshot the governed content came from.
- **`instructions`** — the governed guidance itself: approved sources,
  required filters and joins, prohibitions, caveats.
- **`linked_evidence`** — `observed_assets` (each labeled `git_linked` or
  `observed_only`), plus `findings`, `freshness`, `conflicts`,
  `deprecations`, `uncorroborated`.
- **`domain_graph`** — typed nodes and edges; edges carrying
  `evidence: "git"` are governed.
- **`provenance_refs`** — the exact Git commit and every selected observed
  asset version, pinned.
- **`execution`** — `performed_by_hyperset: false`,
  `result_validated_by_hyperset: false`. Hyperset never runs the
  customer's SQL.

**The four governed sections** (ADR 0019, decision 2): `instructions`,
`context_authority`, `domain_graph` edges with `evidence: "git"`, and the
`git_linked` entries of `linked_evidence.observed_assets`. Each is a pure
function of exactly three inputs: the pinned `ContextSnapshot`, the
configured `ContextSource`, and the observed asset versions the same
answer pins in `provenance_refs`. A conformance check on the served
payload enforces this derivation rule; nothing else may write those
sections.

`bundle_id` is a content hash of the governed answer only. That keeps the
determinism guarantee — same commit, same repository state, same directive,
same bundle — even after assist content (which need not be deterministic)
ships alongside it.

### 1.3 Plan validation

`validate_analytics_plan` re-resolves the bundle server-side and checks a
declared plan (`source_refs`, `fields`, `joins`, `filters`, `grain`,
`checks`) against the governed instructions. Verdicts: `valid | warnings |
invalid | unverifiable`. It is string-exact, deterministic, and never
executes SQL. A plan governance does not cover is `unverifiable` — and it
stays `unverifiable` in every future phase, because the word is true.

---

## 2. The two modes

This is the heart of the product story: **one bundle, two provenance
worlds, and a boundary enforced by construction.**

### Governed mode (V0, permanent)

*Did the agent use what a human pre-wrote in Git?* Exact domain names,
exact set membership for the coverage claim, exact source-native identity
for evidence, string equality for plan validation. Deterministic,
replayable, auditable. It answers the enumerated manifest — measured at
roughly a tenth of real questions — and is silent on the rest.

### Assist mode (V1, ADR 0019: "assist may reason; governance may not")

Assist runs where governance is silent: novel joins, unenumerated grains,
on-the-fly fields, undeclared sources, cross-domain reconciliation. It may
read the question, rank, propose, and reason — everything the governed
path is forbidden to do.

The boundary, in five sentences:

1. **Mode is a property of each claim, not the request or the answer.**
   `resolution.status` summarizes to the weakest class present; assist is
   never requested into existence and can be refused but never demanded.
2. **Leakage is impossible by construction.** Governed section builders
   take one immutable `ContextSnapshot` as their only semantic input;
   assist producers have no snapshot to pass. Assist output lives in its
   own section and is never merged into a governed one — a caller reading
   only governed sections of an assisted answer gets byte-for-byte the
   governed answer.
3. **Assist may order and propose; it may not produce an identity.** A
   candidate has no field that can hold a declared ref, so "this ref means
   that asset" is unsayable in assist output — even for a candidate set of
   length one.
4. **Assist never moves a governed verdict.** It annotates `unverifiable`;
   it does not upgrade it.
5. **The only path from proposal to authority is a human Git change.** A
   ranked candidate that has been right a thousand times is still a
   candidate.

The nine floors of ADR 0019 (no governed label, no identity, no execution,
no authority by accumulation, no overriding, no suppressed disclosures, no
question-reading in the governed path, no borrowing the determinism
guarantee, no unattributed reasoning) hold in both modes, in every phase
of this roadmap, including V2.

---

## 3. V0 — Prove (current phase)

**Claim to prove:** an ordinary question reaches the right governed domain
without naming its configured identifier; then real Superset 6.1.0 + DataHub
OSS evidence, one
deterministic finding, human Git review, one `ContextBundle`, three tools
over HTTP/MCP, and a benchmark-only small Ollama model (`qwen2.5:7b`)
measurably beating raw-metadata baselines — restartable and replayable without
making that benchmark model part of the served runtime.

### Built today

- Read-only **Superset** (REST + export bundle) and **DataHub** (GraphQL)
  connectors; immutable observations and connector changes in Postgres.
- **Git-owned context authority** (ADR 0012): pinned snapshots, manifest
  parsing, evidence resolution with `ref_not_observed` / `ref_awaiting_sync` /
  `ref_ambiguous` findings instead of silent links (ADR 0017).
- **Processor** with one rule, `approved_expression_drift`, producing
  deterministic, deduplicated findings.
- **ContextBundle v8** resolver + catalog + plan validation; one
  `transport/operations.py` dispatch behind both HTTP and MCP.
- **Model-directed planner path** served through OpenAI/Luna. Historical and
  benchmark adapters remain isolated from the supported demo runtime.
- **Two-arm evaluation**: governed arm vs raw-metadata baseline, pinned
  model, recorded-trace per-PR gate with deterministic scorers (ADR 0013,
  ADR 0016).

### Open V0 work

| Issue | What closes it |
| --- | --- |
| #70 (remainder) | Real-model selection passes hidden paraphrases, multiple plausible domains, ambiguity/no-match abstention, and critical selection scoring. |
| #206 (bounded V0 slice, split from #126) | Relevance-ranked domain/concept candidates keep the planner from depending on a positionally capped catalog; a provider-neutral embedding boundary supports local or configured enterprise providers while exact resolution stays unchanged. |
| #42 | Git-owned context produces trusted agent answers end to end. |
| #25 / #33 / #34 / #36 | Benchmark families complete; affected-case rerun + one generic webhook on failure; trusted revenue answers from a clean checkout; merge gate on the trusted-answer skeleton. |
| #72 | Read-only operator view of sync health, governed context, evidence, findings, eval state. |
| #117 (v0.5) | MCP over Streamable HTTP via FastMCP — a hosted localhost endpoint. |

**Exit gate:** the walking skeleton green end to end, one command, across
restarts, with published benchmark evidence.

---

## 4. V1 — Reach (serve the uncovered questions)

Two tracks, already reflected in the crew split (`crew/hyperion` core,
`crew/atlas` assist).

### Track A — Assist mode (epic #122, unblocked by ADR 0019)

| Issue | Capability |
| --- | --- |
| #126 | **Estate-scale semantic retrieval over the context catalog** — after the bounded domain/concept selection split to #206, V1 adds large asset/document/lineage corpora, official hosted/local provider adapters, versioned re-embedding, atomic index activation, and scale guarantees. The governed selector still requires exact names. |
| #124 | **Discovery + candidate-source ranking over the estate** — ordered candidates, each with its ranking signal stated; never a link. |
| #123 | **Reasoning-assist plan validation** — reasoning rides beside an `unverifiable` verdict that does not move. |
| #125 | **General contradiction / reconciliation engine** — typed disagreements as disclosures into `linked_evidence.conflicts`, replacing the single hand-coded rule. |
| #230 | **Navigable multi-domain context graph and governed expansion** — explicit parent/subdomain and relationship edges, bounded expansion, per-domain authority/provenance, and partial-failure disclosure; the prerequisite for composition and multi-domain evaluation. |
| #129 | **Cross-domain bundle composition** — compose the graph expansion defined by #230 into one answer with per-domain provenance; the hardest boundary case, and the reason mode is per-claim. |
| #127 | **Result-trust** — judging the answer an agent already produced, not just the stated plan. Requires its own ADR (deliberately out of ADR 0019's scope). |
| #141 | **Simulated-expert adversarial benchmark** — broad tricky Q&A, LLM-judged, Hyperset vs raw data lake; the number that makes the assist claim measurable. |

Contract impact: one new `resolution.status` value (`assisted`, pending
ratification), one new assist section in the bundle, `SCHEMA_VERSION`
bump on first serve. `mixed` absorbs governed-plus-assist. The graph,
expansion, and composition response contract for #230/#129 still requires an
ADR and evaluator evidence before it is served.

### Track B — Delivery hardening (make it installable)

| Issue | Capability |
| --- | --- |
| #76 | Incremental, fully configurable Superset/DataHub sync. |
| #75 | Admin UI for connectors, connections, sync, identity. |
| #78 | Identity-aware delivery: OIDC bearer verification, PKCE login/session, RBAC, and service identity SHIPPED; per-principal grant source, live configured-OIDC smoke, SAML, and domain ACL breadth remain. |
| #79 | Secrets management for connector credentials and Git tokens. |
| #73 | Customer-facing agent toolkit over the ContextBundle graph (not an agent framework). |
| #96 | Compose E2E coverage + agent-driven eval benchmarking in the build loop. |

**Exit gate:** #141's adversarial benchmark shows assist mode materially
beats the raw data lake on uncovered questions while the governed slice
stays byte-identical; a customer can install, connect, and authenticate
without hand-editing.

---

## 5. V2 — Platform (the AI-first governed context platform)

*Vision, not commitment. Each pillar needs evaluator evidence and an ADR.*

### Pillar 1 — The governance flywheel (the defining V2 feature)

V1 leaves a deliberate gap: assist produces good answers that die with the
conversation. V2 closes the loop **without ever weakening the floors**:

```text
agent question governance cannot answer
        |
        v
assist claim (labeled, attributed, evidence-linked)
        |
        v
curator drafts a context proposal + candidate eval cases
  (evidence-linked; configurable model profile — the useful
   part of superseded ADR 0011, revived post-proof)
        |
        v
review task with explanation, affected assets, eval preview
        |
        v
curator proposes a Git patch / pull request
        |
        v
HUMAN decision — review and merge in the customer's Git workflow
        |
        v
new authoritative Git commit -> coverage grows -> fewer assist claims
        |
        v
affected-case eval rerun protects the promotion
```

Assist demand becomes the prioritization signal for governance work: the
questions agents actually ask tell domain experts exactly which context to
govern next. Coverage grows from ~10% toward the majority — one approved
commit at a time, never by accumulation.

### Pillar 2 — Estate-scale connectors

Looker, Power BI, dbt, Tableau, warehouse catalogs — added against the
smallest common connector contract the first two adapters *earned*, not a
speculative SDK. Incremental sync, webhook-driven change capture,
connector health SLOs.

### Pillar 3 — Enterprise trust fabric

- Multi-tenant deployment, HA, optional DynamoDB-backed repositories
  behind the existing narrow repository interfaces.
- Full audit chain: every served answer reconstructable from
  `provenance_refs` — commit, observed versions, approver, eval state.
- Per-answer **trust attestation**: an exportable record of exactly which
  governed versions, validations, and disclosures an agent answer rested
  on (the productized form of "trust is an inspectable chain").
- Full SSO breadth beyond the shipped OIDC flow: SAML/gateway integration,
  per-domain ACLs backed by a reviewed principal-grant source, scoped fleet
  tokens, and secrets rotation.

### Pillar 4 — Continuous evaluation as a product surface

- Every governed domain ships with its eval cases (`evals.yaml` already
  points there); promotion reruns affected cases automatically.
- Drift-triggered reruns and notification webhooks graduate from one
  channel to configurable routing.
- The adversarial expert benchmark (#141) becomes a standing scoreboard a
  customer runs against their own estate: *your* agents, with and without
  Hyperset.
- Agent fleet observability: which agents asked what, which context they
  used, where governance was silent, where assist was refused.

### Pillar 5 — Cross-stack semantic graph

Composed cross-domain bundles (#129) generalize to an org-wide governed
graph: one place where "recognized revenue" means one thing across
Superset, Looker, and dbt — with conflicts *disclosed*, not averaged.
Semantic expression equivalence (epic I6) grows the validator from string
equality to provable-equivalence, still deterministic, still never
executing SQL.

### What V2 still is not

No BI frontend, no warehouse execution, no auto-canonical metadata, no
autonomous approval, no agent framework. The nine floors hold. Authority
still changes only through the customer's Git workflow. The
moment any of these bends, the governed half of the bundle is worth
nothing — and the governed half is the product.

---

## 6. Sequencing and dependencies

```text
V0 Prove                V1 Reach                     V2 Platform
--------                --------                     -----------
#70 agent retrieval --\
#206 bounded search ---+> #25 benchmark
                           |
                           v
                   #33/#34/#36 gates ---> #42 trusted answers

#72 ops view            #126 estate search --------> flywheel curator
#117 hosted MCP         #124 discovery/ranking ----> cross-stack graph
                        #123 assist validation -----> governed coverage
                        #125 reconciliation --------> org-wide graph
                        #230 graph/expansion -------> #129 composition
                        #230/#129 cross-domain ------> trust attestation
                        #127 result trust (own ADR)
                        #141 adversarial bench -----> customer scoreboard
                        #75/#76 admin + sync -------> estate connectors
                        #78/#79 identity/secrets ---> enterprise fabric
                        #96 compose E2E ------------> continuous eval
```

Rules that survive every arrow: one issue and one walking-skeleton step at
a time; breadth follows proof; every phase exit is an evaluator number,
not a feature list.

## 7. Risks

- **DataHub overlap** (`docs/research/semantic-layer-landscape.md`): the
  wedge is lighter deployment, BI-first connectors, explicit raw-vs-
  approved separation, and *measured* context effectiveness — V0/V1 exit
  gates are the answer, not a feature race.
- **Assist erodes governance credibility** if a single leak ships: the
  derivation conformance check and the byte-identical governed-slice test
  are release blockers, not nice-to-haves.
- **The 90% is an estimate, not a measurement** — #141 exists to replace
  the claim with a number before V2 investment leans on it.
- **Curator revival re-imports ADR 0011 scope creep** — it returns only as
  a proposal generator behind the same human approval boundary, gated on
  its own ADR.
