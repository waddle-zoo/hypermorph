# 0017: Evidence corroborates Git context; it does not gate it

Status: accepted.

Supersedes the part of ADR 0012's decision that shipped as a precondition:
"links context claims to observed evidence only when identity/lineage supports
the relationship" was implemented as a rule about whether a snapshot may exist
at all. Everything else in ADR 0012 stands, including its authority model,
which this ADR restores rather than changes.

## Context

`sync_git_context` treated any unresolved evidence ref as a failed sync. A
declared ref that no connector had observed, or that two connections carried,
produced `record_failure(...)`, `status="failed"`, and no snapshot -- and on
a first sync, no context at all.

That made connected-system sync state a precondition for Git context existing
in Hyperset, which is the opposite of ADR 0012. Git is the authority and
Superset and DataHub are evidence; a system of evidence being behind, offline,
or not yet connected is a fact ABOUT THE EVIDENCE. Rejecting the commit over
it lets an unsynced connector silently decide what the organization's
authoritative context is, and turns detection into rejection.

The cases are ordinary rather than exotic:

- a customer configures a Git context before connecting Superset at all;
- two connectors sync on different schedules, so a commit lands between them;
- a commit adds a definition for a dataset that will be created next week;
- the same native identity is observed on two connections, which is a real
  ambiguity about the evidence and says nothing about the commit.

Under the old rule the first of those produced no context, and the rest could
freeze context at an older commit with the operator told only "failed".

## Decision

**A Git snapshot is recorded for the configured commit regardless of what
evidence resolution finds, including when zero refs resolve.**

Unresolved refs are carried forward as structured findings on the snapshot --
`{code, ref, message}`, with `code` from the same vocabulary the served
contract already uses (`ref_not_observed`, `ref_ambiguous`) -- so a gap is a
fact a caller can act on rather than prose it must read.

Two invariants survive this change, and they are the whole of what the old
rule was protecting:

- **Honesty about corroboration state.** Hyperset never presents an
  uncorroborated claim as corroborated. A ref that resolved to an observation
  is a link; a ref that did not is a finding; the two never merge.
- **No false name-similarity links.** An unresolved or ambiguous ref resolves
  to nothing. Ambiguity is disclosed, never broken by picking a match, and
  display-name similarity links nothing anywhere.

Everything else is disclosure, not a gate.

What remains a genuine sync failure is unchanged: an unreachable repository,
a document that fails schema validation, and a structurally unlinkable ref --
one whose shape, connector, or asset type means it could not name an asset in
any possible world. Those are defects in the commit itself, and the previously
valid snapshot keeps serving. Unobserved is not malformed.

## Consequences

- A snapshot can now exist with fewer resolved refs than the commit declares,
  so `evidence_refs` is "declared AND observed" and `evidence_findings` is the
  remainder. Any reader of one must know about the other.
- Degradation is per ref and per domain. Some refs resolving and others not
  produces one snapshot with mixed findings, and a bad ref in one domain has
  no effect on a sibling domain, because each configured source is synced and
  snapshotted on its own.
- `last_attempt_status` stays `synced` for a commit that persisted. A sync
  with findings succeeded; the findings say what could not be corroborated,
  and the CLI prints them as `uncorroborated`, distinct from `rejected`.
- The processor gets less to compare against when evidence is missing, which
  is correct: it can only detect drift against observations that exist.
- The served bundle does not yet carry these findings. That is hy-zhv9's
  slice, and it will decide the wire shape; this ADR fixes only where the
  snapshot may exist. Until then a caller that names an uncorroborated ref
  gets `ref_not_observed`, which is an existing code and already accurate.
  That "until then" is over: hy-zhv9 served the findings, and hy-gh-118 then
  split the code, because "already accurate" held only for the half where the
  connector had read. A ref whose connector has never finished a sync, whose
  last sync failed, or that has no connection configured now gets
  `ref_awaiting_sync`; `ref_not_observed` keeps the half this ADR reasoned
  about. The decision above is unchanged -- a gap is still a coded finding on
  a snapshot that persisted -- and only the vocabulary it names grew.
- The evaluation corpus is unaffected: every case in it declares refs the
  pinned fixtures observe, so no recording changes.

## Rejected alternatives

- **Keep the gate and tell operators to sync first.** Ordering connectors
  before Git makes the evidence system the authority in practice, which is
  what ADR 0012 exists to prevent.
- **Persist the snapshot but mark the source failed.** A commit that was
  recorded did not fail, and a status that says otherwise trains an operator
  to ignore the word.
- **Resolve an ambiguous ref by picking one match, now that the snapshot
  survives.** This is the invariant, not a side effect of the old rule: two
  connections carrying one native identity are two assets, and choosing one
  invents a link Git never declared.
- **Drop unresolved refs silently.** Corroboration state that is not recorded
  cannot be disclosed, and the difference between "corroborated" and "we never
  checked" is the product.
