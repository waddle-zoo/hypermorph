# The answer record and the human review-decision path (hy-n8ms3)

This documents the minimal human decision/review loop and resolves the open
"answer-record contract" question: **how a recorded human decision points back at
the answer it was made about.**

## Answer identity: `correlation_id` + `bundle_id` (no durable `answer_id`)

A governed answer is a `ContextBundle`. Its citations are persisted in
`answer_citations` at the serving boundary (`_record_answer_citations`,
`hyperset/transport/operations.py`), keyed by:

- **`bundle_id`** — the answer's CONTENT. It is deterministic/content-addressed
  (`hyperset/db/models.py`, `AnswerCitation`), so the same question resolved
  against the same governed context computes the same `bundle_id`. It answers
  *what* the answer was.
- **`correlation_id`** — the request CHAIN. Minted per request and shared by the
  `search → resolve → citations` trace (`mcp_interaction_trace`, hy-oqevj), it
  answers *which* call produced this answer.

**Decision: we do NOT add a durable `answer_id`/`answer_ref` column.** The pair
`(workspace, bundle_id)` already content-addresses the answer and
`correlation_id` already exists as the durable per-request chain key, persisted
across `mcp_interaction_trace`, `answer_citations`, and `citation_decisions`.
Adding an opaque `answer_id` would be a third identifier that carries no
information the pair does not, and it would have to be threaded through the trace
context, the citation writer, and every reader — cost with no capability.

A durable `answer_id` would only become necessary if an answer had to be
referenced **independently of its content and its originating request** (for
example, a mutable answer whose text changes while its identity stays fixed). V0
answers are immutable and content-addressed, so that need does not exist yet.
When it does, `answer_id` is an additive column on `answer_citations`; nothing in
this contract precludes it.

## How a human decision links back

`citation_decisions` (`hyperset/db/models.py`, `CitationDecision`) records a human
`include`/`exclude`/`approve`/`reject` on ONE cited source. It carries **two**
optional back-links, and a decision uses whichever applies:

- **`review_task_id`** — the review task the decision was made from (the
  `/review/` queue path below).
- **`correlation_id`** — the answer the citation belongs to (the answer-citation
  path: a decision on a specific governed answer's citation).

Neither is required by the schema, so a decision can link to a task, an answer,
or both. The row is written by the served route `POST /v0/review/citations/decide`
(`_decide_citation`), which is REVIEW-gated, server-derives the deciding
principal, redacts caller text unconditionally, and writes only this audit store
— it approves, merges, and advances no governed status (ADR 0012).

## Seeding a real Finding + ReviewTask on the demo

There is a scripted, real-pipeline path — the row is what the offline processor
produced from a real source change, NOT a direct-seeded row (prior mayor ruling,
hy-y1ng8):

```bash
make up-demo          # full demo bring-up; runs playground-finding as one step
# or, against an already-running demo stack:
make playground-finding
```

`playground-finding` re-observes a drifted Superset re-export on the same
connection (the approved `recognized_revenue` metric changes) and runs the real
processor over that sync (`hyperset process sync <sync_run_id>`). The
`approved_expression_drift` rule opens one explainable Finding and one idempotent
human `ReviewTask` — the task the `/review/` queue (`list_review_tasks`) serves.

## Acting in the browser: `/review/`

Open `http://localhost:<port>/review/`. A seeded task shows its **Evidence**
(the cited sources). Each cited source now carries
`include` / `exclude` / `approve` / `reject` controls. Clicking one calls
`POST /v0/review/citations/decide` with `{review_task_id, citation_ref,
source_ref, decision}` and writes a durable `citation_decisions` row linked to
the task; the card mirrors the recorded decision. Recording a decision is
audit-only — it changes no governed context.

Verify the row:

```sql
SELECT decision, citation_ref, review_task_id, principal_identity, created_at
FROM citation_decisions
ORDER BY created_at DESC LIMIT 5;
```
