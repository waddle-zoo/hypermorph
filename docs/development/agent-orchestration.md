# Gas Town agent orchestration

This document covers the repo-local tooling that runs Hyperset's own
development through Gas Town (issue import, agent scheduling, single-agent
pipelines). It is development infrastructure for building Hyperset, not
part of the Hyperset product — see [`README.md`](../../README.md) and
[`MANIFESTO.md`](../../MANIFESTO.md) for that.

## GitHub -> Gas Town sync

`scripts/gh-to-gastown-sync.sh` is a manual operator tool that can pull open
GitHub issues and import them into Gas Town beads, then sling ready beads to
available workers. `.gastown-watcher.yml` is its manual configuration file;
neither file installs or represents a running watcher.

Make it executable:

```bash
chmod +x scripts/gh-to-gastown-sync.sh
```

Run once:

```bash
GH_REPO="waddle-zoo/hyperset" RIG_NAME="hyperset" ./scripts/gh-to-gastown-sync.sh
```

The script retains `--loop` for an operator who deliberately keeps a terminal
session open. It is not the documented or installed workflow; use the one-shot
command above when directed by the Mayor.

### Configuration and markdown intake

Create `.gastown-watcher.yml` or pass `--config` to override defaults.

Example keys:
- `GH_REPO`: GitHub repository to scan
- `RIG_NAME`: Gas Town rig name
- `RIG_PATH`: local rig path for `bd import`
- `LABEL_FILTER`: GitHub label to filter issues
- `INBOX_DIR`: markdown inbox directory
- `DEFAULT_AGENT`: agent name for `gt sling` (e.g. `opus-4.8` for a single persistent Opus 4.8 runtime)
- `LOG_FILE`: log file path
- `INTERVAL`: sleep interval only when an operator explicitly uses `--loop`

## Single persistent Opus 4.8 Gas Town pipeline

To lock the rig to one incremental task at a time and force Claude Opus 4.8,
use a custom Gas Town agent alias and scheduler settings.

Example repo-local config files:

`city.toml`
```toml
default_agent = "opus-4.8"

[scheduler]
max_polecats = 1
batch_size = 1
spawn_delay = "0s"
```

`town.json`
```json
{
  "default_agent": "opus-4.8",
  "scheduler": {
    "max_polecats": 1,
    "batch_size": 1,
    "spawn_delay": "0s"
  }
}
```

Register the alias and apply the runtime config:

```bash
gt config agent set opus-4.8 'claude --model claude-opus-4-8 --effort xhigh --disable-slash-commands --tools Bash,Edit,Read --max-sub-agents=0 --no-dynamic-workflows'
gt config set default_agent opus-4.8
gt config set scheduler.max_polecats 1
gt config set scheduler.batch_size 1
gt config set scheduler.spawn_delay 0s
gt config set convoy.notify_on_complete true
```

Assign one bead at a time:

```bash
gt ready
gt sling <bead-id> hyperset --agent opus-4.8 --max-concurrent 1
```

Wait for the bead to complete, run local validation, review manually, then proceed to the next bead.

The watcher also imports markdown task files from `requirements/inbox` by default. Each file should have YAML frontmatter with `title`, `type`, and `priority`.

### Declaring order on a GitHub issue

An issue that must not start before another says so under a `## Depends on`
heading, one reference per bullet:

```markdown
## Depends on

- #31 trusted `ContextBundle` + plan validation
- #43
```

Every `#N` under that heading becomes a bd dependency edge at intake, so the
scheduler sees the gate instead of it existing only as prose the scheduler
cannot read. References anywhere else in the body are ignored, and a bullet
that names no issue declares nothing.

### Example markdown inbox file

```markdown
---
title: Add edge case tests
type: task
priority: 2
---

Write regression coverage for the new sync watcher.
```

### Automated scheduling: removed

This repo no longer ships a launchd/cron template for this script. Issue
intake is manual now: run `./scripts/gh-to-gastown-sync.sh` (see "Run once"
above) when told to pull down issues for the mayor, rather than on a fixed
interval.

The script logs to `LOG_FILE` itself. No cron or launchd job is installed.

### Design notes

This script intentionally leans on `bd`/`gt` for everything they already do
natively, rather than reimplementing it in bash. The only things it actually
owns are: translating GitHub issues and markdown files into bd's JSONL
import format, and the ID scheme that keeps that translation stable across
runs.

- **No separate state file.** `bd import` already upserts by ID and skips
  rows that aren't strictly newer than what it has (by `updated_at`) — that
  is the entire dedup mechanism. This script always builds the full current
  snapshot (every open GitHub issue, every inbox file) and hands it to
  `bd import --json` every run. There is nothing for this script to track
  or get out of sync on its own.
- **"New" is derived from ids, not from bd's counts** (hy-1lou). bd's
  `created` counts every row an import accepted, which includes rows that
  only rewrote an existing bead and rows whose `updated_at` tied with the
  local one (kept local, listed in `tie_kept_local_ids`); a row that is
  newer but identical in content is reported as created with no `updated`
  at all. No arithmetic over those numbers can separate a new bead from a
  rewritten one, and the arithmetic that was there reported "7 new" on
  every pass for ten GitHub issues that had existed for days. The script
  therefore reads the rig's ids once before importing and counts an id as
  new only if the rig did not already hold it.
- **Declared order becomes real dependencies, from structure not prose**
  (hy-9cf). Gate ordering used to live only inside issue descriptions, where
  the scheduler could not see it, so every gated issue landed as an
  unblocked, ready bead — polecat valkyrie picked up #32 out of order on
  exactly that and the resulting branch was unmergeable. Intake now reads the
  `## Depends on` section and emits a `bd dep add` per `#N` it contains. Only
  that section counts: these bodies discuss other issues constantly, and a
  phrase list ("do not begin before", "after #N is green") is a guess about
  English where a heading is a declaration. Edges are **additive and never
  removed** — the script cannot tell an edge it added from one the Mayor
  added by hand, so reconciling by deletion would destroy human decisions to
  tidy machine bookkeeping. Measured against beads 1.1.0: re-adding an
  existing edge succeeds without duplicating it, and an unknown id, a cycle,
  or a self-dependency each fail with a message and are logged rather than
  failing the pass.
- **Only an open bead is ever rewritten.** `bd import` writes the status it
  is given and there is no way to send an update that leaves status alone,
  so re-sending a settled bead rewrites it: an in-progress bead hooked to a
  crew went back to open + unassigned when its GitHub issue changed, and a
  closed bead re-sent from a touched source came back open — reported by bd
  itself as `status closed → open`. Intake skips every bead whose status is
  not `open`, in either half.
- **Active markdown files are never moved or deleted by the importer** after import, and closing
  the bead is what retires one. The inbox folder is the durable,
  human-readable record of the original requirement text; a file's mtime is
  sent as its `updated_at`, so re-editing a file updates the same bead on
  the next run via bd's own staleness check — until the bead is closed,
  after which intake leaves both alone and editing the file cannot revive
  it. An operator may move an obsolete one-off fixture to
  `requirements/archive`; archived files are outside intake and retained only
  as historical records.
- **Bead IDs are `<beads-prefix>-gh-<issue-number>` / `<beads-prefix>-md-<slugified
  basename>`** — e.g. `hy-gh-42`, `hy-md-0001-superset-metadata-models`.
  The `<beads-prefix>` (queried once per run via `bd where --json`,
  e.g. `hy` for this repo's rig) is required: `gt sling` routes bead
  lookups by ID prefix to the owning rig database, so an ID with an
  unrecognized prefix is invisible to `gt sling` even though `bd`
  itself can see it fine. The `gh-<number>`/`md-<slug>` part after the
  prefix is what keeps IDs deterministic and stable across content
  edits; a hash suffix is only appended in the markdown case if two
  different files in the same run would otherwise collide on the same
  slug. Renaming a file is therefore treated as a new item — this favors
  filename stability as the identity signal over content stability,
  matching how GitHub issue numbers behave (the identity doesn't change
  when the body does).
- **Why not `bd github sync --pull-only`** (bd's own native GitHub
  importer)? Two real reasons, not just "we already had code for it":
  it has no label-filter equivalent to `LABEL_FILTER`, and it stores its
  GitHub token via `bd config set` in `.beads/config.yaml`, which is
  git-tracked — a real regression from the `gh` CLI's keychain-backed
  auth this script uses instead. If your use case doesn't need label
  gating, `bd github sync --pull-only` (with `GITHUB_TOKEN` as an env
  var, not `bd config set`) is the more native path.
- **Assignment is one batch `gt sling <id1> <id2> ... <rig> --max-concurrent 3`
  call**, not a per-bead loop — `gt sling` already accepts multiple bead
  IDs and spawns one polecat per bead, throttled by `--max-concurrent`
  (gt's own docs recommend this to avoid overloading the Dolt server).
  Re-implementing that dispatch loop ourselves would just be redoing
  what `gt sling` already does. Note `gt sling` validates the whole
  batch before dispatching any of it, so one bad/stale ID fails the
  entire batch for that run — the unaffected beads simply stay ready
  and get retried next run.
- **Ready = whatever `bd ready --json` returns.** The watcher does not
  reimplement Gas Town's readiness/dependency logic. If a batch sling
  fails outright (capacity, spawn issue, bad ID) the watcher logs it and
  leaves the beads ready — Gas Town's own Deacon patrol (every 5 min)
  will retry a stranded convoy on its own once one exists.
- **The Mayor gets nudged** (`gt nudge mayor "..." --mode queue`) only for
  beads a run actually created that are open and unassigned when the run
  ends, and the message names them. The claim is derived from a post-import
  `bd list --status open --no-assignee` rather than asserted: three
  consecutive passes summoned the Mayor with "Ready work is waiting" for
  work triaged days earlier, which teaches its reader to ignore the
  notification (hy-1lou). A run that only rewrote existing beads is visible
  in the log file and nowhere else, deliberately. Only nudges (not
  `gt mail send`, which creates a permanent bead) per this town's own
  convention of reserving mail for things that must survive session death.
- **Assignment is skipped if Claude is rate-limited.** Before batch
  slinging, the watcher runs `gt quota scan` (gt's own rate-limit
  detector, scanning live Gas Town sessions for 429 indicators). A
  non-zero exit means something is currently blocked, so the watcher
  logs it and leaves all ready beads for the next run rather than
  spawning more sessions on top of an existing rate limit.
- **`--dry-run` over-reports rewrites, and cannot over-report new beads.**
  Measured against beads 1.1.0: `bd import --dry-run --json` applies no
  staleness comparison at all, reporting every row as created with
  `skipped: 0`, where the same rows in a real import came back as 7
  accepted and 3 stale-skipped. Because the script derives "new" from ids
  the rig does not hold, a dry run still reports the right new count; what
  it inflates is the "already held" number. Trust the real run's counts for
  what an import will skip.
