# requirements/inbox

Place markdown files in this directory to have `scripts/gh-to-gastown-sync.sh` import them as Gas Town beads.

Each file should include YAML frontmatter and a body description.

Example file:

```markdown
---
title: Add user onboarding checklist
type: task
priority: 2
---

Document the initial user flow and create onboarding checklist tasks.
```

Supported frontmatter keys:
- `title` (required)
- `type` (`task`, `bug`, `research`; default: `task`). `research` is
  imported into Gas Town as bead type `spike` (bd has no native
  "research" type; `spike` — a timeboxed investigation — is the closest
  built-in match).
- `priority` (integer; default: `2`)

Files without valid frontmatter are skipped and logged.

The importer never moves or deletes files after import — it keys on the
filename, so editing a file re-imports it as an update to the same bead.
Leave an active requirement here as the durable record; deleting it does not delete
the bead.

**Closing the bead is what retires the file.** There is no done state on
disk and none is needed: intake rewrites a bead only while it is open, so
once the bead is closed the file stops being live input and editing it
cannot reopen the bead. Before that guard existed, a touched file brought
a closed bead back as open (hy-1lou).

An operator may move an obsolete one-off fixture to `requirements/archive`.
Archived files are deliberately outside intake and remain in Git only as
historical evidence; ordinary completed requirements stay here.
