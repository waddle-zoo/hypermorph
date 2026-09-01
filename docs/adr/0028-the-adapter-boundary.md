# 0028: The adapter boundary — an adapter may change the shape that carries meaning, never create it

> **Extended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** The adapter boundary and its four invariants are lifted from a customer-side file over a Git corpus to any AUTHORITY BACKEND; Decision 3's "authority remains a human Git merge" generalizes to "a human-reviewed revision in the authority backend (Git merge for Git; native approved revision for a KG)".

Status: ACCEPTED (2026-08-13). **Fork 3** — the one question this ADR left open —
was resolved by Brandon as Option A (REUSE the existing governed status, degraded
to `mixed`) and implemented in #316 (283-6, hy-v5iy); see Resolved questions. The
boundary invariants below are what the landed slices enforce — the closed
transform whitelist (hy-qswh, #297) and the mapping file parser+validator (hy-9keh,
this bead, #283 sub 283-3). apply/ingest (283-4) and the loss-disclosure path
(283-5) were later sub-beads this 283-3 slice did not itself build; both have since
landed (#309 and #312 apply, 43c13fe disclosure), which is what makes Fork 3's
resolution concrete rather than hypothetical.

Extends ADR 0012 (authority is a human Git merge, and Hyperset snapshots it —
never a parallel approval lifecycle) and ADR 0019 (assist may reason, governance
may not). It adds no served operation, and the 283-3 parse+validate slice it was
written for adds no served field: an adapter is a customer-side file this package
READS, not an MCP tool, so `tools_hash` (`sha256:fe930a003b731211`) is unaffected
by the whole boundary. `SCHEMA_VERSION` is untouched by the 283-3 slice; a later
adapter sub-bead did move it once, when 283-5 added the served
`resolution.projection` disclosure (to 11), which is a served bundle field and not
an exception to any claim here.

## Context

v0's Git context format is deliberately narrow, and an unknown manifest key is a
hard error — because a customer who writes a key Hyperset ignores would otherwise
believe governed context says something it does not
(`hyperset/context/schema.py`). That protection is right, and nothing here weakens
it. The cost it imposes is on a company that already has a semantic layer: to adopt
Hyperset they must rewrite their corpus into `manifest.yaml` / `context.md` /
`evals.yaml` and maintain it as a second copy, or run an external projector that
rewrites the corpus on every change.

The projector is what real adoption looks like, and it has three structural
failure modes (#283): **silent loss** (whatever the target has no field for is
dropped, and the bundle looks complete), **misattributed provenance** (Hyperset
snapshots the projection's commit, not the reviewed commit a human approved), and
**drift** (the projection is only as fresh as the last script run). Measured once:
projecting a pipeline-docs corpus into v0 produced a snapshot accepted with zero
warnings while dropping grain, lineage, producing-pipeline refs, classification and
check severity — and INVENTING one governed field (`prohibited_sources`) from a
filename convention, with nothing marking it derived.

An adapter layer turns all of that from invisible into declared. A
`context-adapter.yaml`, checked into the customer's OWN repository beside the
context it describes, declares the mapping — so the mapping is itself Git-owned,
reviewed and versioned material, not a build artifact.

## Decision — the boundary

**An adapter may change the SHAPE that carries meaning. It may never create
meaning.** Four invariants make that line enforceable rather than aspirational.

### 1. Translation is permitted, and is invisible

Reading `title` from `$.heading`, or `grain` from `semantics.grain`, is a PARSING
decision: the same meaning, reached by a different path. It is allowed, needs no
disclosure, and is uninteresting. The mapping expresses it as `<v0 field>: <source
path> | <transform>`, where every `| transform` names a function from the CLOSED
whitelist (`hyperset.context.adapter.transforms`, #297) — `slug`, `prefix`,
`default`, `one_line`, `catalog_urn_to_source_ref`. A mapping file is deliberately
NOT an expression language: a file that can execute code is a file that can
fabricate governance, and it is a code-execution path in something that reads a
customer repository. So the transforms are a fixed set of pure functions reached
only by exact name, an unknown transform is an error, and there is no `eval`, no
`getattr`-by-name, no dynamic dispatch (the whitelist's own guarantee).

### 2. Authoring is permitted ONLY when attributed, with a named human owner

Deriving `prohibited_sources` from a filename regex is not translation — it is
AUTHORING a governed field the customer never wrote. Authoring is permitted only if
the bundle ATTRIBUTES the field to the adapter and a NAMED HUMAN OWNS the rule. An
attributed, owned derivation is a customer's declared decision; an unattributed one
is Hyperset inventing meaning, which the whole layer exists to prevent. This ADR
states the requirement; the mechanism that carries the attribution into the bundle
is the loss-disclosure contract (283-5, now landed as `resolution.projection`'s
`fields_derived`), and the unreviewed case it feeds is answered by Fork 3's
resolution below.

### 3. Neither translation nor authoring creates AUTHORITY

An adapter produces a runtime projection of the customer's mapping file, never a
second store of meaning and never a second approval lifecycle. Authority remains
what ADR 0012 says it is: a human Git merge in the customer's repository. The
adapter file is itself Git-owned material in that repository, so the mapping is
reviewed the same way the context is; parsing it changes nothing about who
approves what.

### 4. "Unknown key is an error" becomes "UNMAPPED key is an error"

The v0 protection survives translation unchanged, because the failure it prevents
is identical: silence about a field the customer actually wrote. A key the adapter
file names that the schema does not understand is REFUSED, at every level, never
dropped and never warned-and-continued (`hyperset/context/adapter/schema.py`
collects every such reason and raises). A source key the map does not map will be
an error at apply time (283-4) for the same reason. The schema introduces no
silent-drop path of its own. The one exception it inherits — unchanged from the v0
format it mirrors — is a DUPLICATE key, which `yaml.safe_load` collapses (last
wins) before the parser sees it: both keys are mapped, and YAML merges them below
the schema, exactly as in `hyperset/context/schema.py`. Closing that would mean a
custom loader stricter than v0, and is a whole-format decision, not an adapter one.

## What this slice builds, and does not

Builds (283-3): `hyperset/context/adapter/schema.py` parses and VALIDATES a
`context-adapter.yaml` — `schema_version: 1` (the file's own version), `adapter`,
`discover{unit, manifest, context_doc, evals}`, and `map{domain, title, owners,
definitions}` — resolving every transform to the whitelist and refusing every
unknown key and unknown transform. It does not read the customer corpus, evaluate a
path, or apply a transform.

Does not build: apply/ingest (283-4) and any disclosure/status path (283-5). This
slice opens neither, so it moves no served field.

## Resolved questions

**Fork 3 — RESOLVED by Brandon as Option A (REUSE / `mixed`).** When the adapter
authors a derived field that no human has reviewed as a v0 field (item 2's
attributed case), how does the bundle represent it? Brandon ruled REUSE, not a new
status value, and it landed in #316 (283-6, hy-v5iy).

* **Option A — reuse the existing governed status, degraded to `mixed` — CHOSEN.**
  The derived field rides the field that already exists, and the bundle `status`
  degrades from `governed` to `mixed`. As landed: the resolver reads
  `resolution.projection.fields_derived` (283-5), whose entries are ONLY
  adapter-authored fields — never a governed Git field — so the governed sections
  stay authoritative BY CONSTRUCTION and are never downgraded; an entry is
  "reviewed" only when it carries a non-empty `reviewed_by`, and any entry that
  does not (blank, absent, or unreadable) is treated as unreviewed and degrades the
  status to `mixed` (fail toward degrading, because an unattributed derived field is
  the exact silence this layer exists to break). Because REUSE adds no new served
  status VALUE, it is `SCHEMA_VERSION`-NEUTRAL — proven concretely: #316 did not
  touch `SCHEMA_VERSION`. `mixed` means the governed part is authoritative and an
  unreviewed derived part is not, so a plan may still validate against the governed
  sections of a `mixed` bundle.
* **Option B — a NEW status value for an unreviewed derived field — REJECTED.** A
  distinct status names the case exactly, but a consumer that has not seen the value
  would MISREAD it, and introducing it would MOVE `SCHEMA_VERSION` (ADR 0018).
  Rejected: the governance signal Option A already carries (a `mixed` bundle whose
  `fields_derived` names the unreviewed field) is enough, without forcing a version
  move on every consumer to say it.

No open question remains. With Fork 3 resolved and its implementation landed, this
ADR is ACCEPTED.

## Consequences

- A customer governs the corpus they already have, in its own shape, with the
  mapping reviewed as Git-owned material — no second copy, no external projector,
  no silent loss.
- Every reshaping is either an invisible translation over a whitelisted transform
  or an attributed, human-owned authoring; there is no third, silent category.
- The v0 unknown-key protection is preserved verbatim as unmapped-key-is-error, so
  adoption via an adapter cannot reintroduce the silence it was built to stop.
- The disclosure of authored fields (`resolution.projection.fields_derived`, 283-5)
  and the representation of an unreviewed derivation (Fork 3) are both decided and
  landed: an unreviewed adapter-authored field degrades the bundle from `governed`
  to `mixed`, reusing the existing status rather than adding a new one (#316). That
  Fork-3 CHOICE adds no new status value and so moved no `SCHEMA_VERSION`; the
  adapter path as a whole is not SV-frozen, though — disclosing `resolution.projection`
  itself moved `SCHEMA_VERSION` to 11 (283-5). Reuse-vs-new-status was the SV-neutral
  decision, not the act of serving an adapter projection.
