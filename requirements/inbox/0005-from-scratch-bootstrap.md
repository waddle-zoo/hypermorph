---
title: Design a from-scratch bootstrap path with zero Superset history
type: research
priority: 2
---

**Depends on:** `0001-superset-metadata-models.md` (and benefits from
`0002-superset-api-v1.md` if it's already done — pick this up after
those land).

Based on the metadata-model and API findings, sketch how a brand-new
org with no existing Superset install could get the same underlying
schema/API surface without ever installing real Superset — so
"start from scratch" users aren't second-class relative to orgs
migrating from an existing Superset instance.

Cover:

- What a minimal bootstrap needs to create (empty metadata tables?
  equivalent in-memory/embedded structures?)
- Whether this is a full schema reimplementation or something lighter
- Any gaps this exposes that the adapter/fork/parallel decision (next
  task) needs to account for

Part of Phase 1 Superset-compatibility research — see `CLAUDE.md` and
`.claude/skills/superset-compat-research/SKILL.md` for the overall goal
and what "done" looks like.

Output: write findings to `docs/research/from-scratch-bootstrap.md` in this
repo.