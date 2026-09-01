# 0025: The MCP trust surface expands to the review operations, proposal-only and PII-guarded

> **Amended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** `propose_review_to_git` is the Git-target INSTANCE of a general `propose_context_change`; the neutral op is a SEQUENCED follow-on that moves `tools_hash`, and the `propose_review_to_git` Git alias is retained. Proposal-only, PII-guarded, and no-silent-merge are preserved and generalized to every authority backend.
>
> **Extended by hy-s8a6 (S1).** A SIXTH review operation, `set_review_assignee`, joins this trust surface (mayor-authorized, the Overseer's own ask requires it on MCP). It is METADATA-ONLY — it sets or clears a review task's opaque `subject@issuer` owner — so it is neither PR-opening nor PII-bearing content, and it moves `SCHEMA_VERSION` (the task view gains an `assignee` key) but not `tools_hash` (it stays off `RESOLVE_PATH_OPERATIONS`). It preserves the same hard boundary this ADR draws: assignment never approves, merges, writes governed context, or runs SQL (ADR 0012), and it is not a grant. The "five operations, proposal-only" framing below GENERALIZES accordingly: the surface's invariant is that NO review op approves/merges/writes-governed/runs-SQL; "proposal-only + PII-guarded" describes the write/model ops specifically.
>
> **Extended by hy-8f2r4 (2026-08-29).** `record_answer_feedback` and
> `lookup_answer_feedback` join the served assist/audit class described by ADR-0033.
> Recording is an append-only, trace-verified audit write; lookup is bounded and
> workspace-scoped. Neither advances review state or authority. Both remain off
> `RESOLVE_PATH_OPERATIONS`, so `tools_hash` is unchanged; their new response shapes move
> `SCHEMA_VERSION` to 26.

Status: ACCEPTED — ratified by the Overseer at PR #259's merge gate, 2026-08-09,
at reviewed head 0ffff1c (critic verdict MERGE, hy-xljx). The Overseer DIRECTED
this MCP review-ops expansion and confirmed the trust-surface diff -- the review
ops proposal-only and PII-guarded, the ADR 0012 authority boundary held, and
`tools_hash` unmoved -- as the merge-gate sign-off this ADR's own header
specified; that confirmation IS the ratification, the accepted-at-gate pattern of
ADR 0024. Supersedes the PROPOSED draft. hy-g84j.

Extends ADR 0012 (authority is a human Git merge), ADR 0019 (assist may reason,
governance may not), and ADR 0022 (the served-but-assist `discover` precedent).
It changes no part of ADR 0005's approval boundary or ADR 0012's authority
model, and it does not touch `docs/v0-foundation.md` invariant 6 ("external
execution stays external") — nothing here executes SQL.

## Context

The reviewer workflow — list open review tasks, read one, edit its drafted
definition, ask the agent to refine it, and propose it into Git — existed only
as bespoke, playground-gated HTTP handlers (`/v0/review/*`). The product goal is
that a customer's own agent can "review misses and propose context changes into
Git themselves," which requires these to be first-class SERVED operations on
both HTTP and MCP, one shape, exactly as `list_context_catalog` /
`resolve_analytics_context` / `validate_analytics_plan` / `discover` are.

Two facts make this safe to state precisely rather than leave implicit:

1. **Served is not the same as a planner tool.** `hyperset/transport/operations.py`
   has one served registry (`OPERATIONS`); both transports derive their surface
   from it (`ROUTES` in `http.py`, `tool_definitions()`/`list_tools` in
   `mcp.py`). The benchmark's `tools_hash` is computed from a DIFFERENT list —
   `RESOLVE_PATH_OPERATIONS` in `hyperset/planner/loop.py`, an explicit
   resolve-path allowlist that `tool_specs()` iterates. `discover` is the
   precedent: served on both transports, absent from the allowlist, and
   therefore `tools_hash`-neutral.

2. **The MCP trust surface is a governed thing.** It was
   catalog/resolve/validate/discover. Adding write and model operations to it —
   operations that mutate a draft, call a model, and open a Git PR — is a
   deliberate expansion the Overseer reviews, not a side effect of a refactor.

## Decisions

1. **Five review operations are served on both HTTP and MCP, one shape:**
   `list_review_tasks`, `get_review_task`, `edit_review_draft`,
   `refine_review_draft`, `propose_review_to_git`. They are entries in
   `OPERATIONS` + `OPERATION_SPECS` + `run_operation`; HTTP and MCP pick them up
   with no per-transport code, so the bytes an agent receives are identical.

2. **They are absent from `RESOLVE_PATH_OPERATIONS`.** `tool_specs()` and
   `tools_hash` iterate that allowlist only, so the served surface expands while
   `tools_hash` is unmoved by these ops. This is asserted in
   tests, and the planner-exclusion guard reds if any review op leaks in. (Its
   value is `sha256:fe930a003b731211` since hy-gh-281 item 3 added VALIDATE
   input-schema field descriptions -- a resolve-path change that did move it; the
   review ops are not among the movers.)

3. **Proposal-only, over MCP as over HTTP (ADR 0012).**
   `propose_review_to_git` may open a pull request and STOP: it never approves,
   merges, writes a governed version, creates a Hyperset-side approvable object,
   or runs SQL. `edit_review_draft` and `refine_review_draft` mutate ONLY the
   unapproved assist draft on a task; the task stays `governance=unapproved`.
   The only path to authority is a human Git merge.

4. **PII-guarded on the proposal boundary, over MCP too (hy-hbtz).** The guard
   redacts or blocks the merged manifest content before it is committed, and
   FAILS CLOSED when the guard is engaged but Presidio is unhostable — the
   proposal is refused (an `isError` MCP result), nothing is committed or
   pushed, rather than leaking PII. A URL write-back target additionally reads
   its token from the server environment by name and fails closed without it
   (ADR 0012 / hy-eji4), and the token never enters an argv, a config row, an
   API response, or a log.

5. **`list`/`get` do not leak governed authority or PII.** They return the same
   review-task view the HTTP surface already returned — the UNAPPROVED
   `proposal_payload` draft and its evidence, never a governed version.

## Consequences

- The MCP trust surface is now catalog/resolve/validate/discover PLUS the five
  review operations. Every review MCP tool is proposal-only and PII-guarded;
  those invariants are the price of being on the surface.
- `tools_hash` is unaffected by these ops (served ≠ planner tool). The pin is
  `sha256:fe930a003b731211` (moved by hy-gh-281 item 3, not by this ADR).
- The bespoke `/v0/review/*` HTTP handlers remain as thin adapters that delegate
  to the served operations, so the existing playground/review UI keeps working;
  migrating that UI to the served operation paths and removing the adapters is a
  follow-on. The admin-only write-back-config surface gate is unchanged.
- Cross-transport parity for the write/model/side-effect ops is CONTRACT parity
  (identical input schema and identical serialized errors on a shared invalid
  request), not byte-identical live output — a model draft and a fresh commit
  are not reproducible across two runs. The deterministic read ops keep full
  byte parity.
