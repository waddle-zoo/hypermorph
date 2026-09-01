# 0020: Hyperset hosts the agent loop; it never owns it

Status: accepted.

Amended by ADR 0022: the question-to-directive reference planner is a supported
product integration rather than benchmark-only infrastructure. The boundary
this ADR establishes is unchanged: Hyperset does not require or own the
customer's broader agent framework, model, prompts, or final analysis loop.

Extends ADR 0019 in the same way ADR 0019 extended the no-semantics
invariant: by scoping a standing sentence rather than deleting it. The
sentence is the manifesto's "Hyperset does not need to own the agent." It
forbids owning. It says nothing about hosting, and the V2 direction —
worked out under the name "Agent Home" — is a hosting claim: customer-
written agents live in Hyperset and inherit its trust properties, while
their code, SDK, model, and prompts remain the customer's.

This ADR changes no part of ADR 0009's vertical-slice order, ADR 0012's
Git authority, ADR 0017's corroboration invariants, or ADR 0019's floors.
All of them apply to hosted agents. The execution and result-trust
boundary (hy-gh-127) remains deliberately outside, as ADR 0019 already
ruled.

## Context

Three filed issues already point at hosting without naming its boundary:
hy-gh-70 makes the Claude Agent SDK and OpenAI Agents SDK supported
runtimes; hy-gh-73 ships a customer-facing toolkit over the
`ContextBundle` graph and says in its own title it is "not an agent
framework"; hy-gh-78 gives agents service identity. The evaluation
harness already imposes per-run accountability (`Recording`,
`hyperset/evals/recording.py`) and the transport layer already funnels
every consumer through one dispatch surface
(`hyperset/transport/operations.py`).

Assembled, those parts are a place agents live: registration, identity,
a governed tool surface, per-answer recording, and an eval gate. That is
the V2 product claim — "your agents live on when sources change" — and it
is true for a structural reason: a hosted agent binds to the
`ContextBundle` graph and directives, not to a source system's API, so a
rename or a BI migration is absorbed by connector sync and Git meaning
rather than by rewriting agent code.

The risk is equally structural. "A place agents live" sits one careless
sprint away from "an agent framework," which is the most crowded and
fastest-commoditizing layer in the industry. Hyperset has no advantage
there and a durable one underneath it: governed meaning bound to a live
estate, drift detection, provenance, and evaluation evidence. A boundary
that lives in one person's head is not a boundary (ADR 0019's phrase),
and three issues about to be built against this one would each infer a
different one.

## Decision

### 1. Host the loop; never own it.

Hyperset provides, for agents its operators register: an agent registry,
a service identity per agent (hy-gh-78), the governed tool surface
(catalog, resolve, validate, and the hy-gh-73 toolkit), mandatory
per-answer recording, and a per-agent evaluation gate.

Hyperset ships no agent framework: no planning loop as product, no
prompt library as product, no Hyperset-branded runtime a customer must
adopt. Runtimes remain adapters behind a narrow seam, as
`hyperset/planner/runtime.py` already shapes them, and the planner's own
loop remains what it is today — benchmark infrastructure, not product.
An agent enters the home with whatever SDK wrote it and leaves with the
same code.

### 2. Tenancy is a contract, and it is all-or-nothing.

An agent is "hosted" exactly when all four hold:

1. it carries a registered service identity;
2. it reaches Hyperset data only through the operations surface —
   `run_operation` is the one door, as it already is for HTTP, MCP, and
   the evaluation arms;
3. every answer it serves is recorded to the same accountability
   standard `Recording` holds the evaluation arms to — the evidence read,
   the model, the tool calls, the provenance refs of every bundle used;
4. it has an evaluation gate wired: a case set the harness can rerun.

There is no partial tenancy. An agent that skips recording or tunnels
past the operations surface is not a hosted agent and must not be
presented as one; the trust claims of the home are exactly the four
properties above, and a fifth-column tenant that lacks one would let the
home's label outrun its guarantee — the shape this repository rejects for
warning codes, for approval, and in ADR 0019 for assist claims.

### 3. Hosted agent output inherits every ADR 0019 floor.

Nothing a hosted agent produces is governed, whatever model wrote it and
however often it has been right. A hosted agent's claims are at best
assist-class: labeled, attributed, refusable, unable to produce an
identity, unable to move a governed verdict, unable to suppress a
disclosure. The only path from any agent's proposal to authority remains
a human Git change (ADR 0012). Registry and hosting metadata — names,
identities, gate results — are operational records, never context
authority, and never a parallel store of meaning (CLAUDE.md's removed-
packages rule states the general form).

### 4. The trust core stays embeddable; the home is optional.

`resolve_analytics_context`, `validate_analytics_plan`, and the
provenance derivation must remain importable as a library, without a
registry, without hosting, without operating anything beyond what the
operations layer already needs. A consumer that wants only context-only
mode — the manifesto's first customer shape, and every current MCP/HTTP
client — keeps it. No home feature may become a precondition of the
trust core; a change that makes the library path require tenancy is the
defect, not a packaging choice.

### 5. The exit stays structural.

Governed semantics live in the customer's Git repository (ADR 0012,
unchanged): definitions, approvals, evidence refs, eval cases, in plain
files, portable because they were never imported. The home may cache,
index, and serve them; it may not become a second system of record for
them. If Hyperset is ever replaced, what dies is the harness; the
meaning walks.

### 6. Sequencing is unchanged.

ADR 0009's order holds: the V0 walking skeleton and its gates come
first. The home is not a third work stream; it is the destination the
existing agent/assist track (hy-gh-70, hy-gh-73, hy-gh-78, hy-gh-122's
children) assembles into, one issue at a time. No registry work begins
before the V0 gates are green.

## Consequences

- hy-gh-70, hy-gh-73, and hy-gh-78 gain the boundary they were about to
  infer separately: runtimes are adapters, the toolkit is a consumer of
  the operations surface, identity is tenancy condition 1.
- Recording becomes an obligation of hosting, not only of evaluation.
  The `Recording` shape gains a second consumer, which is a reason to
  keep it narrow.
- The per-agent eval gate makes "are the agents getting better" a
  number per tenant. That is a product surface, and its scorers stay
  deterministic (ADR 0007, ADR 0013).
- Hosting adds no field to the served `ContextBundle`, so
  `SCHEMA_VERSION` does not move for this ADR (ADR 0018 governs if that
  changes). Registry surfaces are new, separately versioned interfaces.
- Weakening any of decisions 2–5 requires its own ADR. They are the
  floors of the home in the same sense ADR 0019's floors are the floors
  of assist, and they are what the V2 pitch promises out loud.

## Rejected alternatives

- **Ship an agent framework.** The commodity layer, contested by vendors
  whose entire business is the loop. Hyperset's defensible asset is the
  governed substrate; a framework would spend the small team's effort
  where its advantage is zero and invite direct comparison where it
  cannot win.
- **Make tenancy mandatory for tool access.** Kills context-only mode
  and every existing MCP/HTTP consumer, and converts a trust offer into
  lock-in — the exact opposite of decision 5. The home must win tenants
  by what it grants, not by what it withholds.
- **Allow partial tenancy ("recorded later", "identity soon").** A
  hosted label whose guarantees are aspirational is a governance leak
  with extra steps; ADR 0019 rejected convention-and-review enforcement
  twice already for smaller surfaces.
- **Let hosting metadata enrich governed context.** A registry entry is
  an observation about an agent, not approved meaning; folding it in
  would create the parallel authority store ADR 0012 exists to prevent.
- **Defer this boundary until after the first registry code.** The
  reason ADR 0019 exists is that four issues were about to infer four
  different boundaries. Three issues are in the same position here.
