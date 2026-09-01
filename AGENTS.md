# Hyperset Agent Guidance

This repository is set up for agent-first workflows.

## Source of truth — read this first

Before planning or changing Hyperset, read these in order:

1. `MANIFESTO.md`
2. `docs/v0-foundation.md`
3. current ADRs, especially `docs/adr/0009-vertical-slice-first.md`,
   `docs/adr/0012-git-owned-context-authority.md`,
   `docs/adr/0019-assist-mode-may-reason-governance-may-not.md`, and
   `docs/adr/0022-natural-language-selection-before-exact-resolution.md`
4. current research under `docs/research/`
5. the current GitHub issue for the requested component
6. existing code and tests

The manifesto defines the product. The v0 foundation defines the binding product loop, public contract, sequencing, and completion evidence. Existing code is last because it may reflect the older semantic-layer/Superset-replacement thesis.

When an issue, old document, package, or test conflicts with `docs/v0-foundation.md`, stop and resolve the conflict before coding. A passing old test is not proof that behavior matches the current product.

## v0 product boundary

Hyperset v0 is a local Docker, connector-driven analytics context system.

It must:

- connect read-only to real local Superset and DataHub instances;
- preserve lossless, versioned observed assets in Postgres;
- keep observed source evidence separate from human-governed context;
- detect gaps, drift, staleness, conflicts, and evaluation regressions;
- treat the configured Git repository/ref/path as the authority for domain
  meaning and retain its exact commit provenance;
- use a real lightweight planner to turn ordinary analytics questions into
  bounded context-selection directives;
- keep semantic candidate discovery separate from exact governed resolution;
- create explainable review tasks;
- route proposed context repairs back through the customer's Git review flow;
- expose one canonical `ContextBundle` through HTTP and MCP;
- prove context effectiveness with deterministic Inspect AI evaluations;
- make a typed, evidence-backed domain graph simple enough for a small local
  model to fetch and verify correct data;
- benchmark a pinned, isolated Ollama arm + Hyperset against raw-metadata
  baselines; Ollama is benchmark infrastructure, not the product runtime;
- rerun affected cases and emit one generic webhook when a critical case fails.

It must not become:

- a Superset backend or frontend replacement;
- a metric compiler or semantic query engine;
- a production warehouse query-execution service;
- a proprietary chat/agent runtime;
- an autonomous context-approval system;
- a cloud-only or model-provider-specific product.

## Vertical-slice-first rule

Do not build broad subsystems in isolation. The first required product path is:

```text
real Superset 6.1.0 + DataHub OSS evidence
  -> immutable observations and explicit links
  -> natural-language question selects relevant domain/concept candidates
  -> exact ContextDirective produced by a real lightweight planner
  -> connector change
  -> deterministic finding
  -> human-owned context remains authoritative in Git
  -> typed domain graph in ContextBundle
  -> resolve + deterministic plan validation over HTTP/MCP
  -> benchmark-only small Ollama model vs raw-metadata Inspect AI baselines
  -> affected-case rerun and webhook on failure
  -> Docker restart and replay
```

Before adding another tool, processor rule, context kind, evaluator case
family, a third connector, notification channel, or UI surface beyond the
curation control plane, show that the current walking skeleton is green or
identify a concrete failure it cannot handle.

Synthetic fixtures are useful for unit tests but never replace the real-source contract where upstream behavior matters.

## Agent-facing contract

The P0 MCP surface begins with:

```text
list_context_catalog()           -> ContextCatalog   # discovery, no meaning
resolve_analytics_context(...)   -> ContextBundle
validate_analytics_plan(...)     -> PlanValidation
```

These three are the currently served deterministic trust surface. A separate
semantic discovery operation may be added only by the bounded V0 slice of
GitHub #206 (split from #126) and ADR 0022: it returns derived candidates,
never governed meaning, and exact resolution still happens through
`ContextDirective`.
Any other tool -- `get_provenance` included -- needs evaluator evidence and an
ADR amendment before it exists; nothing is pre-authorized on a necessity test
(hy-9fq).

Semantic interpretation belongs to the calling agent's model, not to Hyperset
(GitHub #70). Retrieval takes a structured `ContextDirective` naming exact
domains and refs; it never routes on the wording of a question, and no
alias, keyword, regex, stemming, or scoring subsystem may be added to
approximate that. A request that names nothing is refused with the catalog
operation named in its recovery.

That exactness describes the governance kernel, not the user experience. V0
must also ship and evaluate a supported lightweight-planner path that starts
from the user's natural-language question, discovers a bounded set of relevant
domain/concept candidates, and produces the exact directive. The planner may
choose where to look; it may not create identity, provenance, or authority.
Before the V0 benchmark closes, that path must succeed on hidden paraphrases
across multiple plausible domains and must abstain on ambiguity or no match.

Do not recreate the pre-pivot six-tool surface or the issue-era nine-tool inventory by default. Resource inspection, sync administration, review decisions, and UI operations may use HTTP endpoints without becoming agent tools.

Every `ContextBundle` must disclose:

- exact governed and observed versions;
- lifecycle and human review state;
- freshness;
- conflicts and deprecations;
- required definitions, filters, joins, caveats, and validations;
- observed-only fallback;
- exact provenance references;
- whether Hyperset executed or validated SQL/results.

Adding an agent-facing tool requires an ADR amendment and evaluator evidence that the bundle is insufficient.

The evaluation harness may expose a controlled read-only demo query tool to
all benchmark arms. Hyperset itself does not become a production query
execution service.

## Superset connector rules

- Treat ORM, REST list, REST detail, and ZIP/YAML export as separate contracts.
- Preserve original source payloads or content-addressed raw references.
- Never discard unknown fields during normalization.
- Never infer source fields that the selected transport does not provide.
- Never use a name or slug as the sole stable identity.
- Partial syncs must not imply deletion.
- Do not persist plaintext credentials in observed payloads, logs, traces, or responses.
- A Superset version/transport is supported only after a real pinned upstream instance passes the connector contract suite.
- Hand-written fixtures are supplemental, not compatibility proof.
- A connector creates observations and change records only; it never creates governed context.

## Data-model rules

Keep these concerns distinct:

- connection, source snapshot, and sync state;
- observed asset family identity and immutable observed versions;
- connector changes;
- configured Git context authority and immutable snapshots;
- processor findings and review tasks;
- Git commit/review provenance;
- retrieval bundles and evidence;
- evaluation cases/runs/attempts;
- provenance and audit evidence.

There is no supported direct `ObservedAsset -> authoritative context`
conversion. Superset/DataHub observations may corroborate or contradict Git;
they cannot replace it. For V0, the configured Git ref and exact commit are the
customer's governance decision, as ADR 0012 specifies.

Do not restore the removed historical `Entity`/`AnalyticalIntent`, semantic,
compatibility, bridge, owned-agent-runtime, artifact, trust, or multi-tool MCP
packages as parallel stores. Git history is sufficient for migration
archaeology. New clients consume `ContextBundle`.

A supported reference skill/client is part of the V0 natural-language
selection proof. It packages the question -> candidate discovery -> exact
directive -> resolve -> validate sequence without becoming a second source of
meaning or a required agent framework.

A future curator may draft a bounded, evidence-linked Git patch or pull
request. It is never a source of truth and has no approval or merge capability.
The V0 UI is operational and read-only with respect to canonical meaning; it
does not provide a parallel context editor or approval lifecycle.

## Evaluation rules

Define the first three gating tasks before broadening the suite:

1. natural-language domain/concept selection followed by governed fetch,
   result, validation, and evidence;
2. observed-only or no-match disclosure;
3. stale, conflicting, or deprecated qualifier disclosure.

Each task must be unambiguous, have a known reference solution, run against a stable environment, and grade observable outcomes. Read the trace manually before trusting the grader.

The governed family must contain hidden paraphrases and plausible decoy
domains. A scripted directive proves transport plumbing only; V0 selection
evidence must come from the real pinned model/runtime.

Prefer deterministic graders for:

- context and source selection;
- prohibited-source avoidance;
- freshness/lifecycle behavior;
- conflict/deprecation disclosure;
- provenance completeness;
- required validations/caveats;
- fixture-backed result equivalence.

Model graders are optional and must not be the only release gate. Do not require one exact tool trajectory when multiple valid paths produce the correct outcome.

Required comparison uses a pinned small Ollama model with and without
Hyperset in the isolated evaluation harness. The served demo/runtime uses
OpenAI/Luna; Ollama/Qwen are not product dependencies. A release evidence run
also compares a pinned frontier model using
the identical raw metadata and query tool. Scripted experts may set up approved
test context only through the real review service and must be labeled test
actors.

## Completion standard

Do not close an issue because unit tests pass against invented payloads. A P0 pull request must state:

- which walking-skeleton step it enables;
- the authoritative source or fixture used;
- the before/after persisted state;
- the public contract affected;
- deterministic tests and integration evidence;
- that no approval shortcut, parallel store, unsupported compatibility claim, or unapproved agent tool was introduced.

Demonstrate acceptance criteria through the local Docker stack and the exact public API/MCP/connector boundary the issue owns.

When a requirement remains ambiguous, resolve it in `docs/v0-foundation.md`, an ADR, a real fixture, or a contract test before writing a broad abstraction.

## The gate, and how a suite size is reported

There is one named gate command. Run it verbatim:

```bash
uv run python scripts/gate.py
```

It runs `uv run pytest tests/unit tests/integration -q` and prints one line:

```text
HYPERSET-GATE v2 sha=<40-char> tree=clean tree_id=<40-char> extras=all cmd="uv run pytest tests/unit tests/integration -q" env_cleared="<none|NAME=value;...>" env_observed="<none|NAME=value;...>" collected=<n> uncollected_modules=<n> passed=<n> failed=<n> errors=<n> skipped=<n> xfailed=<n> xpassed=<n> result=PASS
```

Every `<n>` above is a placeholder, like the SHA beside it. This is the shape
of the line, never a count: a line quoting numbers is evidence only for the
SHA it was produced at, and a concrete number sitting in a document is the
exact trap this section exists to close. Run the command and paste your own.

**Paste that line whole** into any bead, PR comment, or review that reports a
suite size. Report against this command, not against whatever you happened to
run. A bare "N passed" is not evidence, and neither is a number with the
command beside it: at `f02cd60` three agents recorded 472, 463 and 439 as "the
gate", and two of them had quoted character-identical commands. The number is
a function of the command, the environment, and the tree; only the first was
ever written down.

**The gate stands on the extras-installed side.** `uv sync --all-extras
--all-groups` first; `scripts/gate.py` refuses to run at all when a declared
extra is missing, and that refusal is deliberate. A module-level
`pytest.importorskip` does not skip its tests, it removes the module from
collection and leaves one skip entry standing for all of them, so a run
without the extras produces a smaller count that looks comparable and is not.
CI syncs `--all-extras`, so a gate defined without them would measure a
different suite than CI forever.

**`extras` is a prediction; `uncollected_modules` is the evidence.** The
extras check reads distribution metadata, so `extras=all` can be printed by an
environment where a package is installed and does not import. A module that
dropped out of collection with one skip entry standing in for it -- a missing
extra, an installed extra that does not import, or a dependency group the
script never reads -- makes the run `result=NOT-A-GATE` with exit 2 even when
every collected test passed.

A collection error is **not** one of those, and this section used to say it was
(`hy-j4m4`). `_Recorder` counts a collect report only when that report is
SKIPPED, and a collection error is a report whose outcome is FAILED, so it lands
in `errors`. pytest then interrupts collection and nothing runs. Measured shape:
`errors=1 uncollected_modules=0 skipped=0 passed=0 result=FAIL` with exit 2 --
a red that names itself, never a short set wearing `result=PASS`.

**`result=PASS` means no module was observed dropping out of collection, which
is not the same as the set being whole.** A module removed by a collection-time
exclusion -- `--ignore`, `--ignore-glob`, `norecursedirs`, `collect_ignore`,
`pytest_ignore_collect`, or a narrowing whose remainder is green -- yields no
collect report at all, so it contributes nothing to `uncollected_modules` and
nothing to `skipped`. Measured at `9505de1`, clean tree, nothing else changed:
`PYTEST_ADDOPTS="--ignore=$PWD/tests/unit/evals"` printed `collected=411` where
the honest run printed 492, and `sha`, `tree`, `extras`, `cmd`,
`uncollected_modules`, `result` and the exit code were all identical. `-k` did
the same with one test left standing.

**`sha` names a commit; `tree_id` names what was measured (`hy-3esn`,
`hy-hm6h`).** The merge seat gates an uncommitted no-ff merge, where `sha=` is
the BASE and `tree=dirty` says only that the measured tree is not it -- two runs
at one base print the same `sha` for two different trees. `tree_id` is the git
object id of the tree the run actually measured: a COPY of the repository's own
index plus every path `git add -A` would add, written in a temporary index. So
the content it ranges over is every tracked path plus every untracked path
`.gitignore` does not match, and an ignored UNTRACKED path is in neither half --
two runs differing only in one print the same id (measured; live here, because
`.gitignore` holds `.env` and `tests/compose/test_superset_live_sync.py` and
`test_datahub_live_sync.py` read `REPO_ROOT / ".env"` to build the environment
they run under -- bounded, because the gate does not run `tests/compose`, so no
count on the line can move with that file). Within that content, two lines carry
one `tree_id` exactly when the two runs measured the same tree, so a post-merge
line equal to its trial line is the merge having landed what was gated, whatever
`sha` did in between.

It names the CONTENT ON DISK, which is the tree `git commit` writes only once
`git add -A` has staged it. During an uncommitted merge the real index already
holds the merged content, ignored paths included, so on a resolved-and-staged
merge the id is the tree that lands -- but this seat resolves a conflict, gates,
and only then runs `git add`, and across that gap the two diverge: a path deleted
in the working tree alone printed `21c428d6` where the tree that landed was
`9f2260ab`, and an unstaged edit printed `89bfb8c1` against the same `9f2260ab`.
Comparing a trial line to a post-merge line still works, because both lines are
computed the same way. Verifying a line against `HEAD^{tree}` of the merge commit
does not, unless the working tree was clean when the line was printed. On a clean
checkout, where neither gap is open, `tree_id` is `git rev-parse HEAD^{tree}`.

Seeding that temporary index from `HEAD` instead was not enough: it covers
ignored paths already tracked in `HEAD` and drops one a MERGE introduces, and
two trial merges landing different trees printed one id (`hy-hm6h`, measured --
`.gitignore` here holds `.claude/` and `CLAUDE.md`, and commit `1591be7` added
six files under those patterns at once). Nothing is staged to produce the id:
the real index is copied, never written.

A conflicted merge has no tree to name and prints `tree_id=unknown`, and it does
so because `git ls-files -u` is asked FIRST. Letting `git write-tree` fail was
the earlier plan and it did not work: the seed copies the unmerged entries in and
then `add -A` stages the marker-bearing file at stage 0, resolving the conflict
inside the temporary index. Measured on a real conflict -- `write-tree` on the
real index exits 128 while the gate printed `8de0d87d`, a tree whose conflicted
file holds `<<<<<<< HEAD`: an id nobody can land, printed with no hedge, which is
the failure this field exists to close.

**`env_observed` is what the run ran under; `env_cleared` is what it removed
(`hy-ia5h`).** `CI` is the member today: two tests in `tests/unit/planner` skip
when it is unset, so one SHA and one tree printed `passed=523 skipped=2` on a
laptop and `passed=525 skipped=0` on CI with `collected` and every other field
identical. It is disclosed and deliberately NOT cleared -- those two tests are
backstops against a CI that synced without `--all-extras`, so clearing `CI`
would silence them in the one place they fire. An input that NARROWS the set is
neutralised; an input that is what the environment legitimately IS gets printed.

**The version is a token, so old lines still read.** `parse_evidence_line`
accepts any `HYPERSET-GATE vN` and reports which, and `v2` is what says a line
without `tree_id` came from a script that could not print one rather than from a
run that declined to.

**The environment route is closed and disclosed (`hy-vkh0`).** The script now
clears `PYTEST_ADDOPTS`, `PYTEST_PLUGINS` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD`
before calling pytest and prints what it cleared as `env_cleared`, so `cmd=` is
true by construction and an override is neutralised in the open rather than
erased. `env_cleared="none"` is the ordinary run. You no longer have to unset
them by hand, and if you set them deliberately the line says so.

What that does **not** cover, and the reason to keep comparing lines whole: a
narrowing committed to the tree rather than passed through the environment --
`addopts` in `pyproject.toml` or `pytest.ini`, `norecursedirs`, `collect_ignore`
or `pytest_ignore_collect` in a `conftest.py` -- reaches pytest anyway, and
`cmd=` cannot see it. Measured at this commit: none of those exists in this
repository, and `pytest` is the only installed distribution whose name begins
with `pytest`, so no plugin brings its own environment input either. Both facts
are true today and neither is enforced. A single matching field is not a matching
suite.

Outside this command and reported separately, because each needs a service or
is a different kind of check: `tests/postgres`, `tests/compose`, `ruff`, and
`scripts/check_docs.py`. `CLAUDE.md` lists all of them in run order.

## What a seat may say about an object it does not hold

No check, guard, or report may call a commit **absent from the repository**. It
may say that *this checkout does not hold it*. Only the second is measurable
locally, and the two differ by the seat's own configuration.

The mechanism is the fetch refspec. Measured 2026-07-31 with `git config
--get-all remote.origin.fetch` and `git for-each-ref --format='%(refname)'
refs/remotes | wc -l`:

```text
seat                        refs/remotes   remote.origin.fetch
polecats/capable/hyperset        193       +refs/heads/*:refs/remotes/origin/*
.repo.git                        193       +refs/heads/*:refs/remotes/origin/*
crew/atlas                       166       +refs/heads/main:refs/remotes/origin/main
crew/hyperion                     52       +refs/heads/main:refs/remotes/origin/main
```

A crew seat is a separate clone that fetches one branch, so `git fetch origin`
there will never bring down a pull-request branch. Nothing is broken and
nothing repairs itself. The two crew seats share one refspec and disagree by
114 refs, because the ref count is a fossil of when the clone was made rather
than of what the seat can reach — so the blindness grows the longer a seat
lives, and no seat can read its own ref count as coverage.

Three rules follow, and they bind any code that reads object presence:

- **Publish the scope beside the absence.** A refusal that turns on an object
  being missing must print `remote.origin.fetch` and the remote ref count. One
  `git config` call. Without them the reader cannot tell a commit that does not
  exist from one this seat was never configured to fetch.
- **Name a repair that would work.** For an object a seat lacks that is
  `git fetch origin <sha>`, or `git fetch origin refs/pull/<n>/head`. Not
  `git fetch --unshallow`: that repairs truncation, and prescribing it to a
  main-only seat produces a clone that is still blind and now holds a receipt
  saying it was repaired. `crew/hyperion` ran it on 2026-07-31 and went from
  315 to 383 commits with `--is-shallow-repository` false and its truncation
  evidence emptied, while the object that prompted it stayed missing (hy-4nyg,
  hy-f1vw — their measurement, not this one). What that seat can and cannot
  reach a day later, measured here on 2026-08-01, is the shape to keep: it now
  holds `ccf7f4c`, because that commit landed on `main` and `main` is the one
  branch it fetches, and it does not hold `fda57f2`, the head of open pull
  request #187, which `crew/atlas` holds because atlas wrote it. Unshallowing
  moved neither. The absence that persists is always a ref the refspec excludes.
- **Do not widen a crew refspec to make a reader work.** Main-only is plausibly
  deliberate — a full refspec pulls every other seat's branches into a
  context-limited agent. The defect is code that reads presence as if the
  refspec were full. Widening one is a separate decision with its own cost.

Worked example: `ensure_object` in `scripts/version-collision-gate.sh`, and
the arm in `tests/unit/test_version_collision_gate.py` that proves the printed
scope is measured rather than a sentence about this rig.

## Pre-implementation drift check

Before substantial work, answer:

1. Does this directly advance the walking skeleton or a demonstrated failure after it?
2. Is source behavior backed by a real pinned upstream contract?
3. Does this create another store, lifecycle, response shape, or source of authority?
4. Can the same value be delivered through `ContextBundle` instead of another agent tool?
5. Is the human approval boundary intact?
6. Can success be graded as an observable outcome?
7. Is added complexity justified by evidence rather than possibility?

If any answer is unclear, stop and update the contract or ADR first.

## Recommended agent tools

These optional tools can help agents stay focused, but they never override the source-of-truth rules above.

- `caveman` — `.agents/skills/caveman/SKILL.md`; compress responses while
  preserving technical accuracy.
- `ponytail` — `.agents/skills/ponytail/SKILL.md`; prefer the smallest correct
  solution and avoid speculative infrastructure.

Claude and Codex workers must load both skill files for coding work. Skills
never override this file or the v0 source of truth.

## Sequential crew workflow

Run one foundation bead at a time through persistent crew workspaces. Never
target `hyperset` directly with `gt sling`; a rig-level target creates an
ephemeral polecat.

- `hyperset/crew/hyperion`: implementation with `opus48-medium` (Claude Opus
  4.8 medium effort).
- `hyperset/crew/critic`: read-only Claude review with `sonnet5-reviewer`.
- `hyperset/crew/adversary`: independent read-only Codex review with
  `codex-luna-xhigh` (GPT-5.6 Luna, xhigh reasoning).
- The Hyperset refinery is the sole merge executor and runs the write-capable
  `codex-sol-high` profile; it never supplies a review verdict.

Use `./scripts/gastown-agent.sh codex` or `./scripts/gastown-agent.sh claude` to register all
profiles, select the worker default, and sync the live Mayor directive.
`docs/directives/mayor.md` defines priority/dependency selection, monitoring,
validation, exact-SHA review loops, CI, and merge gates.

The script also writes `autoCompactWindow` into your user-scope Claude settings,
because Claude Code 2.1.206 ships a built-in auto-compact window for one model
and `claude-opus-5` is not it. **The auto-compact buffer exists only when that
setting is set**: an unset seat holds no reserve, so it fills its context and
stops at "Context limit reached" instead of compacting, which is what left
overnight sessions frozen on an unsubmitted command (hy-gh-121). Confirm a seat
with `claude -p "/context"` — an "Autocompact buffer" row means the window
resolved, and no such row means it did not. Settings reach a session only when
that session starts, so re-run the script and restart the agents after changing
it.

### Finding a frozen seat

`./scripts/frozen-seat-scan.sh` is the detection half of that mitigation, and it
is needed because **the mitigation is applied at session start only**: a seat
already running when the setting changed stays exposed until someone restarts
it, and the window value is pinned to a Claude Code version where resolution is
undocumented and has already moved once without a changelog entry. Seven seats
were found frozen at once on 2026-07-30, by hand, only after the mayor's own
seat hit it (**hy-wqyl**).

Nothing else reports this. `gt status` drew all seven as live, `gt patrol scan`
looks for `session-dead-active` and these were not dead — the process runs and
the TUI still draws — and their beads stayed `in_progress`, indistinguishable
from work in flight. A frozen seat is worse than a dead one: a dead seat is
reaped and its work re-slung, while a frozen one holds its bead hooked, its
convoy open and its branch unpushed, and every nudge sent to it queues into a
prompt that will never submit, so the sender's "Nudged, wait-idle" is itself
misleading.

```bash
./scripts/frozen-seat-scan.sh                 # 0 none, 3 seats flagged, 1 could not look
./scripts/frozen-seat-scan.sh --threshold 70
```

The default threshold is 85, deliberately below 100: at 100 the seat is
unrecoverable without a restart, and the point of a threshold is to catch it
while `gt handoff` can still be typed into a prompt that still submits.

Two limits are contract, not caveat, because the script reads a **terminal
rendering** — the number is not exposed anywhere else:

- **A missing reading is not health.** The indicator shares a rotating hint slot
  with "Update available! Run: brew upgrade claude-code", so a working seat
  usually shows no number at all. The script reports over-threshold seats and
  counts the rest as "no reading"; it never calls a seat healthy.
- **A seat that discusses context percentages reads as one that is out of
  context.** The first live sweep flagged its own seat, because its grep pattern
  was echoed into its own pane and matched it. The calling session is excluded
  for that reason, which fixes the self-match and nothing else. Over-reporting is
  the chosen direction: a false alarm costs a glance, a miss costs the fleet.

Exit codes separate "found frozen seats" (3) from "could not look" (1) so a
scheduled caller cannot read a failed sweep as a quiet town. For the same
reason the socket is discovered from `gt status --json` rather than defaulting:
Gas Town sessions are not on tmux's default socket, so a `launchd` run with no
`TMUX` in its environment would otherwise sweep an empty server and report
all-clear.

This behaviour arguably belongs in `gt patrol scan` as a state of its own,
upstream in `gastownhall/gastown`. It lives here because gastown is installed
from a Homebrew bottle with no source checkout in this workspace.

The file it edits is `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`, and
that is a machine-wide write: user scope applies to **every Claude Code session
on the machine, including non-Gas-Town ones and repositories unrelated to
Hyperset**, not just the crew seats. This is deliberate — no per-project file
covers every seat, since `witness/` has none — but it is the widest scope the
script touches, so audit it there. The write preserves the file's other keys,
preserves its indent unit where one can be detected and otherwise emits two
spaces — so a minified file gains indentation rather than keeping none — refuses
rather than overwrite a settings file it cannot parse as JSON (Claude accepts
comments there; `json` does not), and runs before any agent registration so a
refusal leaves the town unswitched.

Those are two separate refusals and the message says which happened. A file that
does not parse is one thing; a file that parses into something that is not an
**object** — `[1, 2]`, `"hi"`, `42`, `null`, `true` are all valid JSON — is
another, and it used to die one line later on `AttributeError` with no explanation
(**hy-x83h**).

Because that file can hold `env` and `apiKeyHelper`, the write also **preserves
its mode and ownership rather than hardening them**. It replaces the file by
renaming a new inode over the old one, which would otherwise substitute the
invoking shell's umask for the original mode — widening `0600` to `0644` under
`umask 022`, and narrowing `0644` to `0600` under `umask 077`. Forcing `0600`
would fix the widening and silently break other readers of a legitimately `0644`
file, so the mode is copied instead. A file the script creates for the first time
has no mode to copy and is created `0600`.

If that path is a **symlink** — as it is under most dotfiles setups — the write
follows it and updates the real file, leaving the link intact. It does not replace
the link with a regular file, which would strand the dotfiles copy as a stale
duplicate. The mode preserved is the real file's.

The write goes through a scratch file at a fixed, predictable path
(`settings.json.gastown-agent`), so an interrupted run can leave one behind. A
leftover **regular** file there is removed before the new one is opened: at mode
`000` the open itself failed, and it failed before the cleanup that would have
removed the file, so every later run failed the same way until an operator deleted
it by hand (**hy-2eez**). Anything there that is **not** a regular file — a
symlink, a directory, a FIFO — is refused instead, by name, with its own sentence
saying what to do about it: the link's victim is never written through and the link
is not deleted.

The line between the two is whether **this script can plant the shape itself**. It
plants a regular file whenever a run is killed mid-write, so removing that is
cleaning up after itself. It never creates a symlink or a directory there, so one
of those means something else did, and removing it would destroy what the script
did not create — unrecoverably, where refusing is recoverable with one `rm`
(**hy-lsrx**).

```bash
gt ready
gt crew start hyperset hyperion --agent opus48-medium
gt sling <bead-id> hyperset/crew/hyperion --merge=local
```

Do not begin the next bead until the current branch has independent approval,
passing required checks, and a verified merge.
