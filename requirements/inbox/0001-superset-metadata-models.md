---
title: Map Superset's core metadata models
type: research
priority: 1
---

Document Superset's SQLAlchemy metadata models relevant to compatibility:
`Database`, `SqlaTable`, `TableColumn`, `SqlMetric`, `Slice`, `Dashboard`.

For each model, capture:

- Fields and relationships
- How virtual datasets (`SqlaTable.sql`) differ from physical tables
- Exact source file/module paths and the Superset version they were
  checked against

This is the first of the Phase 1 Superset-compatibility research tasks —
see `CLAUDE.md` and `.claude/skills/superset-compat-research/SKILL.md`
for the overall goal and what "done" looks like. Later research tasks
(API surface, annotation gap, version compatibility, from-scratch
bootstrap, and the final adapter-vs-fork-vs-parallel recommendation)
depend on the findings here, so be precise and cite sources rather than
summarizing from memory.

Output: write findings to `docs/research/superset-metadata-models.md` in this
repo.