# Hyperset Claude directive

## Load before work

Read, in order:

1. `MANIFESTO.md`
2. `docs/v0-foundation.md`
3. `docs/adr/0009-vertical-slice-first.md`
4. `docs/adr/0010-two-source-evaluation-loop.md`
5. `docs/adr/0012-git-owned-context-authority.md`
6. `docs/adr/0019-assist-mode-may-reason-governance-may-not.md`
7. `docs/adr/0022-natural-language-selection-before-exact-resolution.md`
8. `AGENTS.md`
9. relevant current research and active GitHub issue
10. existing code and tests

Existing code comes last because pre-pivot code may be wrong even when its
tests pass.

For every coding task, load and apply:

- `.agents/skills/ponytail/SKILL.md` — smallest correct change, deletion and
  reuse before new abstraction;
- `.agents/skills/caveman/SKILL.md` — terse progress and handoff without
  dropping technical facts.

These skills never override product boundaries, validation, security, human
approval, or completion evidence.

## V0

```text
real Superset 6.1.0 + DataHub OSS evidence
  -> immutable observations and explicit links
  -> ordinary question selects bounded domain/concept candidates
  -> real lightweight planner produces an exact ContextDirective
  -> connector change
  -> one deterministic finding
  -> customer-owned Git context remains authoritative
  -> typed domain graph in ContextBundle
  -> catalog + directive-driven resolve + validate-plan over HTTP/MCP
  -> benchmark-only small Ollama model beats raw-metadata baselines
  -> affected-case rerun and webhook on failure
  -> Docker restart and replay
```

The served demo/runtime uses OpenAI/Luna. Ollama/Qwen belong only to the
isolated reproducibility benchmark and are not product runtime dependencies.

One issue and one walking-skeleton step at a time. Do not add a second
processor rule, a third connector, context kind, response shape, UI surface,
evaluator family, or notification channel before this path is green. The MCP
trust surface is catalog, resolve, validate, and the assist-class discover
(ADR 0022), plus -- per ADR 0025 (hy-jis1), extended by hy-s8a6 -- the review
operations list_review_tasks, get_review_task, edit_review_draft,
refine_review_draft, propose_review_to_git, and set_review_assignee, plus the
ADR-0033 trace-linked assist/audit operations record_answer_feedback and
lookup_answer_feedback. NO review or feedback
MCP tool approves, merges, writes governed context, or runs SQL (ADR 0012): the
write/model ops are PROPOSAL-ONLY (propose_review_to_git opens a PR and stops)
and PII-guarded on the proposal boundary, and set_review_assignee writes only
task METADATA (the opaque owner), never a proposal, a governed row, or a grant.
All of these are served but absent from the resolve-path planner allowlist
(`hyperset.planner.loop.RESOLVE_PATH_OPERATIONS`), so serving them does not move
`tools_hash`. ADR 0022 additionally requires a
supported natural-language planner path and permits the bounded V0 slice of #206
(split from #126) to add assist-class semantic candidate discovery before exact
resolution.

## Hard boundaries

- Connector creates observations and connector changes only.
- Processor creates findings and review tasks only.
- The configured Git ref/commit owns domain meaning; Hyperset snapshots it and
  never creates a parallel approval lifecycle.
- A future curator may propose a Git patch/PR only; it cannot approve or merge.
- One `ContextBundle` serves HTTP, MCP, and evaluation clients.
- Plan validation checks the bundle deterministically and never executes SQL.
- Retrieval resolves what a `ContextDirective` names. Semantic interpretation
  belongs to the supported lightweight planner/calling agent, never to
  Hyperset heuristics. Relevance may choose where to look and may not create
  authority, identity, or governed fields.
- Hyperset does not execute warehouse SQL in v0.
- Removed `semantic`, `compat`, `bridge`, `agent`, legacy `mcp`, `artifacts`,
  and `trust` packages must not return as parallel stores.

## Completion

Run:

```bash
uv sync --all-extras --all-groups
make playground-ui
uv run ruff check .
uv run ruff format --check .
uv run python scripts/gate.py
uv run pytest tests/postgres -q
uv run pytest tests/compose -q
uv run python scripts/check_expected_failure_owners.py
python3 scripts/check_docs.py
```

`make playground-ui` builds the gitignored `playground/ui/dist/` bundle (`npm ci &&
npm run build`) BEFORE the gate. Without it, a clean checkout serves the source
`playground/ui/index.html` (which references `/src/main.jsx`) instead of the built
`/playground/assets/...`, so `tests/unit/transport/test_http.py`'s served-playground
test fails -- and the failure now names this step rather than reading as a broken
assertion (hy-r8jd, #346). It needs `npm`/Node.js; the target errors loudly if absent.

`scripts/check_expected_failure_owners.py` asks `bd` whether each expected-failure
fix-row bead is still open, catching a row that retired for the wrong reason (a
stale owner keeping the ratchet green). It needs `bd`, which is absent on CI, so
it runs here at completion, not as a test -- and `check_docs.py` enforces that it
stays in this list, because a guard in no checklist is the same green as no guard
(hy-4k9u).

`scripts/gate.py` is the named gate: it runs `tests/unit tests/integration`
and prints one `HYPERSET-GATE v2` line carrying the invocation, the collected
count, the extras state, the SHA, the id of the tree it measured, and the
environment inputs it cleared and observed. Paste that line whole beside any
suite size you report; a bare pass count is not comparable across agents. See
AGENTS.md, "The gate, and how a suite size is reported".

P0 completion requires real pinned source evidence, before/after persisted
state, exact public contract impact, deterministic checks, and proof that no
approval shortcut or unapproved agent tool was added.

## Gas Town

Use persistent `hyperset/crew/hyperion` for implementation. Review is
dual-model at the same exact SHA: `hyperset/crew/critic` (Claude Sonnet 5) and
`hyperset/crew/adversary` (`codex-luna-xhigh`, GPT-5.6 Luna xhigh), both
read-only. `hyperset/crew/consultant` (`codex-luna-xhigh`) is the mayor's
cross-model consultant — advises, never merges or edits. Never sling to the rig
root. `docs/directives/mayor.md` is the live delivery loop.
