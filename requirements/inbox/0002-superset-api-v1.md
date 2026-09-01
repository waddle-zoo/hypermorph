---
title: Inventory Superset REST API v1 surface for dataset/chart/dashboard
type: research
priority: 1
---

Document the REST API v1 endpoints for dataset, chart, and dashboard
CRUD (`/api/v1/dataset`, `/api/v1/chart`, `/api/v1/dashboard`).

Capture:

- Auth model (session, API token, OAuth — whichever Superset uses)
- Request/response schemas for create, read, update, delete on each
  resource
- Pagination and filtering conventions
- Exact Superset version this was checked against

Part of Phase 1 Superset-compatibility research — see `CLAUDE.md` and
`.claude/skills/superset-compat-research/SKILL.md` for the overall goal
and what "done" looks like. Cite specific endpoint paths and schema
fields rather than general impressions.

Output: write findings to `docs/research/superset-api-v1.md` in this repo.