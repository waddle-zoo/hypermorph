---
title: Compare metadata schema across recent major Superset versions
type: research
priority: 2
---

Identify what's stable versus what's changed in the metadata schema and
REST API across the last 2-3 major Superset releases, using Superset's
own migration files and changelogs as sources.

This determines how narrow or broad a realistic compatibility target
is — e.g. whether Hyperset can credibly claim "works with any Superset
version" or needs to pick a supported version floor.

Part of Phase 1 Superset-compatibility research — see `CLAUDE.md` and
`.claude/skills/superset-compat-research/SKILL.md` for the overall goal
and what "done" looks like.

Output: write findings to `docs/research/superset-version-compat.md` in this
repo.