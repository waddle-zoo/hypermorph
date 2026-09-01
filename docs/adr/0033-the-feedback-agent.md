# 0033: The feedback agent — open and tunable, it proposes and notifies but never authorizes

Status: ACCEPTED (2026-08-29). The trace-linked substrate in Decision 6 is served as two
HTTP/MCP assist/audit operations and durable records. The HTTP-only human citation-decision
and search-to-review proposal paths in Decision 7 are also served. The profile-configured
recommendation agent and Slack notification seam remain design-only and unbuilt. Brandon's
earlier ruling still binds those later slices: the agent stays OPEN and TUNABLE, and Slack
routes/notifies only. Original design record: hy-80v2.

This EXTENDS ADR 0012 (Git owns context authority — a maintenance feature may propose a
patch or PR and STOP, never approve or merge), ADR 0024 (AI sourcing references a live
lookup that is a READ, never warehouse SQL, and proposes but never creates authority),
ADR 0025 (the review operations are served on both transports but OFF the resolve-path
allowlist), ADR 0026 (a write-back secret is encrypted at rest, its KEK from the
environment and never the database, and fail-closed), and ADR 0027 (an installation
token is minted per operation and never stored). The implemented substrate adds no served
resolve-path operation, so `tools_hash` remains `sha256:fe930a003b731211`; it does add two
assist/audit response shapes, so `SCHEMA_VERSION` is now 26. The still-unbuilt agent and
notification seam add neither. Release-note follow-up: hy-li6oo.

## Context

The flywheel already DETECTS. The processor writes findings
(`hyperset/processor/rules.py`), and the assist authoring loop turns a resolve miss into
an UNAPPROVED `ReviewTask` whose `proposal_payload` carries `governance:"unapproved"`,
the miss, the gathered sources, a draft definition, and full provenance
(`hyperset/flywheel/authoring.py`). Two gaps remain. First, nothing RECOMMENDS a
concrete repair from that evidence and carries it to a human as one traceable proposal —
the configurable-curator loop of #39 is unbuilt. Second, nothing TELLS a human any of it
happened (#322): a proposal can sit unseen, and owner refs, though present on every
domain snapshot, are wired to no route.

Brandon's ruling sets the shape of both. The design's whole job is to make the feedback
agent and its notifications USEFUL without letting either become a shortcut around the
single authority this product has: a human merging a Git commit (ADR 0012).

## Decisions

### 1. The feedback agent is open and tunable, not a hard-coded evidence set.
The agent MAY read all governed AND observed evidence a resolve already assembles —
`linked_evidence.findings`, `linked_evidence.observed_assets`, `linked_evidence.conflicts`,
the `resolution`, the governed `instructions`, and the `domain_graph` — plus the catalog.
It is not pinned to a narrow, hard-coded evidence slice. Operators CONFIGURE it, as
experience accumulates, through a config profile (`FeedbackAgentProfile`, the shape of
#39's `CuratorModelProfile`): its evidence SOURCES and its SYSTEM PROMPT are configuration,
never code, so tuning changes behaviour with no deploy. Its reads stay within the platform
boundary: it reads references and PINNED snapshots and may trigger a live lookup that is a
READ of a referenced asset, never warehouse SQL (ADR 0024 invariant 6).

### 2. It emits a concrete repair as a traceable ReviewTask, propose-and-stop.
The agent's output is a recommendation — a concrete feedback or repair — carried as an
UNAPPROVED `ReviewTask`, reusing the EXISTING shape rather than a parallel one: it writes
through `create_task`/`set_proposal_payload`, and its `proposal_payload` carries
`governance:"unapproved"`, the affected `domain`, the evidence it used, its draft, and
`produced_by:{producer:"feedback-agent/1", model}` plus `provenance:{prompt_hash,
tools_hash, model, runtime}`. It MAY hand that draft to `propose_review_to_git`, which
opens a pull request and STOPS. It NEVER approves, merges, writes a governed row, creates
a Hyperset-side approvable object, runs SQL, or approves its own work (ADR 0012); the task
stays `unapproved` until a human merges the Git PR. The processor still owns findings; the
feedback agent is an assist-class PRODUCER of a review task and, like the review
operations, stays OFF `RESOLVE_PATH_OPERATIONS`, so `tools_hash` is frozen (ADR 0025).

### 3. Owner routing resolves the affected domain's declared owners; unrouteable is explicit.
A proposal names a domain, and that domain's owners are already on its snapshot as
`owner_refs` (each `{ref, source}`, from the manifest `owners:` list or CODEOWNERS). The
seam routes to those declared owners — no new "domain card" is invented, and no ownership
is inferred. The addressee is EXPOSED in the API, not hidden (#322 D2), and a domain with
no declared owner is an EXPLICIT "unrouteable" outcome, recorded and observable, never a
silent drop.

### 4. The Slack seam notifies and routes; it never approves.
On a small CLOSED event set — review-task-created, proposal-opened (with the PR URL), and
domain-expiry (#322 D1) — the seam delivers an outbound Slack message to the affected
domain's owner carrying the proposal summary, its evidence and provenance, and a REVIEW
LINK to the task and PR. Slack delivers a POINTER to the human review surface; it carries
NO approve or merge action, renders no approval button, and mints no authority. The human
still opens the review, reviews the evidence, and merges the Git PR (ADR 0012). It is
fail-closed and non-blocking: an unconfigured or failing channel never blocks, bypasses,
or auto-advances review — the review task and PR stand regardless, and a failed or
unrouteable delivery is OBSERVABLE (a counter / health field), never silent.

### 5. The delivery contract is idempotent, bounded-retry, and audited; the secret follows 0026/0027.
Each delivery carries an IDEMPOTENCY key over `(event, task, target)`, so a retry or a
re-fire never double-pings an owner. Delivery is BOUNDED-RETRY with backoff, and each
attempt writes an AUDIT record — delivered, failed, or unrouteable — so the notify step is
reconstructable. The Slack secret (bot token or webhook URL) is admin CONFIGURATION,
encrypted at rest with a KEK read from the environment and never the database, and it
never appears in a response, a log, JavaScript, or argv, failing closed on a missing or
undecryptable secret (ADR 0026); any per-request token is minted per delivery and never
stored (ADR 0027). Notification content crosses the SAME `guard_text` PII boundary the
proposal already crosses before it egresses, and that guard fails closed.

### 6. Trace-linked answer feedback is served as assist/audit, never authority.
Hy-8f2r4 activates the previously reserved served-assist seam with two operations:
`record_answer_feedback` appends one of `accept|reject|include|ignore|correct|needs_review`
to a hit or bundle verified against the current MCP session/correlation trace, and
`lookup_answer_feedback` reads those records by exact session/correlation/source/review-task
keys within one workspace. The write stores only redacted notes/refs and opaque linkage,
and atomically back-links its id to the trace. It never creates or advances a ReviewTask,
proposes a patch, approves, merges, resolves, writes governed context, or runs SQL.

Both operations join the ADR-0025 served-assist/review class on HTTP and MCP but remain
absent from `RESOLVE_PATH_OPERATIONS`, so `tools_hash` stays
`sha256:fe930a003b731211`. Their new served response shapes move `SCHEMA_VERSION` to 26.
The durable interaction trace also gains boundary duration, narrow staleness for sources
actually served, an explicit description of miss targets, answer bundle ids, and linked
citation-decision/feedback ids. Query/intent redaction and opaque hit ids remain mandatory.
This substrate is input to a later tunable recommendation agent; it is not that agent and
cannot invoke write-back itself.

### 7. Human citation decisions and search-to-review proposals are served over HTTP only.

The shipped feedback MVP also lets an authorized human record a citation decision and turn
trace-linked search hits into an unapproved `ReviewTask`. Both paths are REVIEW-gated HTTP
operations, deliberately absent from the MCP `OPERATIONS` registry and
`RESOLVE_PATH_OPERATIONS`. A search proposal carries its trace and evidence into the existing
review lifecycle; it does not edit Git or open a pull request. Opening a proposal branch or
PR remains a later, explicit human action, and a Git merge remains the only promotion to
canonical meaning.

These operations reuse the existing citation, trace, and review-task stores. They add no
parallel authority, agent tool, or approval state. This is the served human feedback loop,
not the still-unbuilt profile-configured recommendation agent or Slack delivery seam.

## Acceptance (for the implementing slices, not this record)

- A profile-configured agent reads a bounded evidence packet (a pinned snapshot plus its
  linked observed refs) under an operator-set system prompt; changing either the sources
  or the prompt changes behaviour with no code change.
- The agent's output is a `ReviewTask` with `governance:"unapproved"`, full provenance, and
  `produced_by:"feedback-agent/1"`, created via `create_task`; no governed row is written
  and no status is auto-advanced.
- Given a proposal on a domain with a declared owner, exactly ONE Slack notification is
  delivered, carrying the review link and idempotent under retry; a domain with no owner
  yields an explicit unrouteable audit record and no silent drop.
- An unconfigured or failing Slack channel leaves the review task and PR intact and the
  failure observable; no notification path can approve, merge, or write governed context.
- The Slack secret is absent from every response, log, and argv; a missing or undecryptable
  secret fails closed.
- `tools_hash` remains unchanged; the feedback response shapes move `SCHEMA_VERSION` to 26.
- A direct MCP search and two feedback appends (`ignore`, then `accept`) are retrievable in
  order from the same session/correlation/source chain; a fabricated or cross-workspace
  target is refused.
- Fresh trace rows carry duration, served-source staleness, and explicit miss targets, with
  no raw query credential or feedback-note secret persisted.
- A REVIEW-authorized human can record a citation decision or create an unapproved review
  task from traced search hits over HTTP; neither operation is exposed as an MCP tool, writes
  Git, opens a PR, or advances review status.

## Consequences

The trace-linked feedback substrate is now durable and queryable. Human citation decisions
and trace-linked search proposals can create review work, but perform no autonomous
recommendation or write-back. The later feedback agent becomes the flywheel's RECOMMEND
step and the Slack seam its NOTIFY step, both strictly propose-and-notify: every authority
path still terminates at a human Git merge. The later outbound surface and secret remain
contained by the 0026/0027 pattern and a fail-closed default. Rejected
alternatives: an LLM curator that edits or approves context directly (ADR 0012); Slack
buttons that approve or merge, or any "approve by reacting" affordance (an approval
bypass, and the exact shortcut this ADR exists to forbid); and a hard-coded evidence set
or prompt, which would foreclose the tuning Brandon's ruling requires.
