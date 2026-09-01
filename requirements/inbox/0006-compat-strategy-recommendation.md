---
title: Recommend a compatibility strategy - adapter vs fork vs parallel
type: research
priority: 1
---

**Depends on:** all four other Phase 1 research tasks
(`0001`-`0004`) plus `0005-from-scratch-bootstrap.md`. Don't start this
until those findings exist — this is the synthesis task.

Using the findings from metadata models, API surface, annotation gap,
version compatibility, and the from-scratch bootstrap sketch, recommend
one of three compatibility strategies:

1. **Adapter mode** — Hyperset reads/writes Superset's existing metadata
   DB directly, acting as an alternate frontend/brain on top of an
   unmodified Superset install.
2. **Fork mode** — Hyperset reimplements Superset's metadata schema and
   API surface as its own system, importable from a real Superset
   instance but not dependent on one at runtime.
3. **Parallel mode** — Hyperset is its own system with an import/export
   bridge to Superset (one-time migration), no ongoing schema coupling.

State the tradeoffs explicitly for each option against the stated
requirement — "anyone with or without Superset can get started" — and
give a clear recommendation, not just a comparison table. This is the
key deliverable that unblocks Phase 2 (the integration plan).

Part of Phase 1 Superset-compatibility research — see `CLAUDE.md` and
`.claude/skills/superset-compat-research/SKILL.md` for the overall goal
and what "done" looks like.

Output: write findings to
`docs/research/compat-strategy-recommendation.md` in this repo.