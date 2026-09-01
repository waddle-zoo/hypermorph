---
title: Assess how thin Superset's existing metric/column annotation support is
type: research
priority: 1
---

Determine exactly what descriptive metadata Superset already supports on
columns and metrics today (description fields, `verbose_name`,
certification flags, etc.) versus what's missing that a real semantic
layer needs — lineage, ownership, business definitions, validation
rules.

This defines the gap Hyperset's semantic layer is actually meant to
fill, so be concrete: list the fields that exist today, and list what a
dbt/Cube-style semantic layer would need that isn't there.

Part of Phase 1 Superset-compatibility research — see `CLAUDE.md` and
`.claude/skills/superset-compat-research/SKILL.md` for the overall goal
and what "done" looks like.

Output: write findings to `docs/research/superset-annotation-gap.md` in this
repo.