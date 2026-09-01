# Hyperset: priority-driven crew delivery loop

## PRIORITY 1: the flywheel IS the product (Overseer, 2026-08-08)

The Overseer's clearest statement of what Hyperset sells: **the flywheel is the
main product and the selling point, and it is the V1 we push. Priority 1.** "That
is what we sell." You start with little governed context; the flywheel builds it
out through use and keeps it fresh, so governed coverage compounds.

Rank the flywheel above everything else. Make this loop **testable end to end**
so the Overseer can drive it — it is not "item 10 of a list," it is the product:

1. **Miss** — a question finds no governed context (no_match / observed_only).
2. **Gather sources** — gather the relevant observed sources/assets for that miss
   (reference-not-ingest, live lookup; the AI-sourcing work feeds this).
3. **Present to an expert** — a clean surface: the miss + gathered sources + a
   drafted proposal, for an expert to review.
4. **Expert updates WITH AN AGENT** — the expert works with an agent that drafts
   and refines the correct governed context; the expert edits and approves.
5. **Into Git** — approval produces a Git **PR proposal** into the customer's
   context repo. Proposal only: the human approves/merges in Git; the curator
   never approves or merges itself (ADR 0012).
6. **Evals keep it fresh** — evals watch the governed context and flag staleness,
   so it stays current.

Existing beads cover parts: `hy-jrpm` (miss-log), `hy-ghwo` (suggestion),
`hy-8b5h` (Git-PR writer). The **expert-review surface**, the **agentic curation
step**, and the **eval-freshness watch** need their own beads. Keep the loop
**minimal** — the smallest thing that demonstrably closes miss → expert+agent →
Git → fresh, not a gold-plated curation product (the onboarding/no-overbuild lens
still applies). This reprioritizes the pillar tier: flywheel first; governance,
AI-sourcing, semantic, observability, and the evals migration are sequenced as
what the flywheel needs, in that service.

## North Star (set by Overseer, 2026-08-07)

Ship a **testable, interactable V1** with **minimal token spend**. The bar is
concrete: the Overseer can drive Hyperset through the **playground UI** and the
**MCP server**, ask questions, and **see the visuals** the answers produce. That
interactable path is how the flywheel above gets exercised and tested — the
flywheel is the product; the playground/MCP/visuals are how the Overseer drives
it.

Rank every ready bead by how directly it moves that demo path forward. A bead
that makes the playground/MCP loop work against a realistic large-scale example
outranks polish, refactors, and internal hardening that the demo does not touch.
Deprioritise (do not start) work that does not visibly advance the interactable
demo unless it is a hard blocker for something that does.

**Spend tokens like they cost money.** Prefer the smallest bead that unblocks
the demo. Reuse and delete before building. Skip speculative generality. If a
cheaper path reaches the same demo capability, take it and say so.

**Human-in-the-loop is mandatory for anything you are not sure of.** The
Overseer owns all major decisions from now on. When a choice is ambiguous,
irreversible, changes product scope/boundaries, picks between materially
different approaches, or you simply are not confident — **stop and bubble it up
to the Overseer** with the options and your recommendation. Do not guess on a
major call to keep the loop moving. Routine, unambiguous, reversible steps still
proceed without asking.

### Active push: drive to a running demo (Overseer, 2026-08-07)

The Overseer said: *"keep working until we have the v1 demo I can interact with,
since I'll have obvious feedback once I can see it in action."* So the priority
is a runnable thing, fast, not more merged beads in the abstract.

**Definition of demo-ready** (all must hold before declaring it and handing to
the Overseer). Expanded by the Overseer on 2026-08-08 from four points to the
full v1 slice — do not declare done on the old four-point bar:

1. `docker-compose.demo.yml` stack comes up clean; playground chat serves at
   `localhost:8000/playground/`.
2. A question typed in the playground returns **relevance-ranked `discover`
   candidates** (the assist path, hy-gh-206) against a **visibly industry-scale
   estate** (hy-zx0q extends the revenue/supply_chain examples).
3. The same estate renders **visuals** (Superset dashboards/charts) the Overseer
   can see.
4. The **MCP** surface is connectable from a **fresh Claude session** — hosted
   Streamable HTTP endpoint (hy-gh-117), with exact connect instructions, not
   only stdio-spawn.
5. **Corrected governance modeling** is testable in the demo (defect + "correct"
   model defined by the Overseer; item 1 of the 2026-08-08 expansion).
6. **Semantic search** (hy-gh-126) is testable from playground + MCP.
7. **AI sourcing / "AI mode that gathers observed assets"** is testable —
   built to a boundary the Overseer ruled, never letting AI create observations
   or authority (connectors still own observation; hard boundary).
8. **Evals demo**: one-command, readable way to run the **shipped customer eval
   runner** (thin, inspect_ai-backed) against example cases that live **outside**
   the package (a mounted example repo/config), plus #25 available as dev
   tooling. Corrected by the Overseer 2026-08-08: eval cases/recordings/harness
   must **not** ship in the `hyperset` wheel — customers point the runner at
   their own cases (GitHub/build config). Hyperset's own #25 benchmark moves to a
   dev/CI-only location (out of `packages=["hyperset"]`) with its function
   preserved. Consolidate onto inspect_ai as the handler (it owns run/dataset/
   task/reporting); delete the hand-rolled `scorers.py` engine; keep exactly one
   domain `@scorer` (governed-vs-assist, source-identity, no_match). Decouple
   `hyperset.evals.recording.provenance()` from the evals package so the planner
   no longer depends on it. There is no DeepEval layer — do not add one.
9. **Agent observability**: the lightest OSS agent trace monitor is bundled in
   the stack and shows live planner/agent traces (OTel GenAI conventions so the
   tool stays swappable).
10. **Flywheel start**: a request that finds no governed bundle logs the miss,
    and that miss can be fed back into context through a human-in-the-loop that
    approves a **Git PR proposal only** — the curator proposes, never approves
    or merges (ADR 0012).

**Onboarding is the #1 adoption lens (Overseer, 2026-08-08).** "If onboarding
sucks no one will adopt." Local-then-deployed onboarding must be dead simple: a
newcomer should be able to start Hyperset, connect a warehouse + some context +
a catalog, and get going locally without fighting the repo. Treat the demo as
**doubling as the local quickstart**, and hold the whole build to this lens: is
the path to run it obvious? This is a **keep-in-mind principle, not a build
order** — the Overseer said explicitly *not to go overboard*. So:

- The integration shape to design toward (not build in full now): point Hyperset
  at folders in one or many GitHub repos for context (read, Git-authoritative);
  a designated repo for HITL write-back (the flywheel, PR-proposal-only); a
  warehouse (Presto/Snowflake/Databricks) + catalog + Superset; a clear
  tokens/secrets path; a clean local→hosted deploy path.
- Do **not** build all three warehouse connectors now — one connector at a time
  is the V0 boundary, and a three-warehouse build is exactly "overboard."
- Docs stay minimal: one clear QUICKSTART and one short ARCHITECTURE/hosting
  page, not doc sprawl. Prefer opportunistic de-chaosing of the repo over an
  onboarding epic.

**Demo-path bead chain** (drive to completion, one at a time, smallest first):
hy-gh-206 slice 3a (discover served) → hy-zx0q (industry-scale estates) →
hy-gh-117 (hosted HTTP MCP) → any thin wiring bead needed to make the playground
actually call `discover` and render results. File a wiring bead only if a real
gap exists; do not invent one.

**Reduced HITL until the demo is up.** The Overseer has raised the momentum bar:
rule tactical calls yourself from this directive (slice granularity, review
sequencing, hold-vs-drain, allowlist shape). Reserve Overseer HITL for genuine
**product-boundary changes, irreversible/destructive actions, or a materially
different approach** — not for every slice A/B. When you do rule one yourself,
say so in one line and proceed.

**Prove it runs before declaring done.** A merged bead is not a demo. Before
reporting demo-ready, either stand the stack up and confirm the four points
above, or hand the Overseer's oversight seat a verified runbook to stand up and
smoke-test. "Beads merged" is not the bar; "the Overseer can poke it" is.

## Crew

Hyperset uses persistent crew workspaces, never ephemeral polecats. As of
2026-08-07 there is **one** implementation worker:

- `hyperset/crew/hyperion` (`opus48-medium`, Claude Opus 4.8 medium effort): the
  sole implementation track. It owns the whole V1 demo path — playground UI, MCP
  server, large-scale example estates and their visuals, and whatever core
  resolve/validate/evidence work those require. Run it one bead at a time.

The second worker (`atlas`) and its separate agentic/assist track were retired
on 2026-08-07 to cut token spend; do not restart it without an Overseer ruling.

Hyperion may use sub-agents (`Task` tool, in the `opus48-medium` alias) for
parallelizable investigation/research -- reading unfamiliar code, searching for
prior art, drafting options to evaluate. It is still solely accountable for its
own branch: sub-agents may inform a decision, they do not commit, push, or merge
on its behalf, and it still does its own diff self-review (step 2.7) before
reporting done.

Review is **dual-model**: two independent read-only critics rule on every PR at
the same exact head SHA, one Claude and one Codex, so a false-clear has to slip
past two different model families rather than one.

- `hyperset/crew/critic` (`sonnet5-reviewer`, Claude Sonnet 5) is the Claude
  critic. Route review beads to it at an exact head SHA.
- `hyperset/crew/adversary` (`codex-luna-xhigh`, GPT-5.6 Luna at xhigh reasoning)
  is the cross-model **adversary** critic. Route the SAME review bead to it at
  the SAME SHA, worded to find the FALSE-CLEAR / the leak / the boundary
  violation the Claude critic might share a blind spot on. It is a persistent
  seat, not a spawned `Agent` — the mandatory second reviewer, distinct from the
  optional expert reviewers in step 2's "Work with expert reviewers" section.

Mayor rules on scope and on the UNION of both verdicts; the refinery executes
the merge once the gate is verifiably met, and neither critic replaces the
other nor the merge seat. In the live Hyperset rig the refinery runs the
write-capable `codex-sol-high` alias: it is the merger, not a grader. Hyperion's
own diff self-review (step 2.7) and required CI still apply on top. Neither
critic gets the `Task` tool -- both must stay strictly read-only,
and a spawned sub-agent could otherwise carry broader access than the critic
itself has (the `codex-luna-xhigh` alias is already `--sandbox read-only
--ask-for-approval never`, which enforces this at the runtime, not just by
instruction).

`hyperset/crew/consultant` (`codex-luna-xhigh`, GPT-5.6 Luna at xhigh) is the
mayor's **cross-model consultant**: a read-only second opinion that monitors the
project alongside the mayor. Consult it on ambiguous rulings, ordering calls,
scope decisions, and "is this the smallest correct change" questions before
committing to a direction — the same lever the Overseer gets, one model family
removed from the Claude seats that do the building. It advises only; it never
selects beads, rules on merges, or edits a branch. When it and the mayor
disagree on a major or irreversible call, bubble to the Overseer per the
human-in-the-loop rule rather than letting either seat break the tie alone.

If token spend becomes a concern, pausing the adversary critic and/or the
consultant is the lever — but the Claude critic pass is never skipped, and pause
either explicitly here, because a Mayor restarting cold reads this file as the
current loop.

These aliases apply only when a seat starts. To repair a stale live seat, stop
and start the two reviewers explicitly, then restart the merge seat; do not use
the plain `gt crew restart` shortcut because it falls back to Claude:

```bash
gt crew stop hyperset/critic hyperset/adversary
gt crew start hyperset critic --agent sonnet5-reviewer
gt crew start hyperset adversary --agent codex-luna-xhigh
gt refinery restart hyperset --agent codex-sol-high
```

Leave Hyperion and Mayor on their Claude profiles when applying this repair.

Do not target `hyperset` directly with `gt sling`; a rig-level target spawns a
polecat. Do not start a second implementation on hyperion while its current bead
is in the delivery loop -- exactly one bead is in flight at a time.

## 1. Select the next bead

1. Run `bd ready --json`; never select blocked work. Treat the ready list as
   incomplete, not authoritative: an ordering ruling that lives only in bead
   notes or mail is not a gate, and hy-9cf records that the GitHub sync drops
   declared dependencies too. Whenever you rule that A must land before B, wire
   it with `bd dep add B --blocked-by A` in the same breath — otherwise B sits
   at the top of `bd ready` as unblocked work and the next cold start picks it.
2. Choose the lowest priority number first (`P0` before `P1`, and so on).
3. Within a priority, prefer the bead that unblocks the most dependent work,
   then higher user impact or risk, then oldest ready work.
4. Confirm hyperion is idle and the prior bead is closed.
5. Start `hyperion` with `opus48-medium` if needed, then run:
   `gt sling <bead-id> hyperset/crew/hyperion --merge=local`.

## 2. Implement and validate

Hyperion must:

1. Read the repository guidance and bead acceptance criteria.
2. Load `.agents/skills/ponytail/SKILL.md` and
   `.agents/skills/caveman/SKILL.md`; use Ponytail for smallest-correct-code
   decisions and Caveman for terse progress.
3. Create a focused feature branch and inspect the existing behavior before
   editing.
4. Implement the smallest complete solution.
5. Add or update tests that fail without the change and pass with it, and know
   which kind each new test is. A regression test goes red against the unfixed
   code. A guard test cannot -- it protects a property the old code satisfied
   trivially, so it must be shown red against the plausible wrong change it
   exists to catch, not against the pre-fix code. Report which test went red
   against what; a guard test nobody has seen fail is a comment with a test
   framework around it.
6. Never modify the working tree to produce red. Prefer constructing the wrong
   version **in process** — subclass the handler and skip the override,
   monkeypatch the function via an in-memory fixture — which leaves nothing
   behind and cannot half-apply if the call is interrupted. Fall back to a
   detached worktree only when the wrong version cannot be built in process,
   and then copy the new test into it rather than pointing pytest at the
   branch's path, or a differing conftest silently mixes the two
   configurations. The working tree only ever holds the change you intend to
   ship. Swapping a source file in place risks committing the unfixed file with
   green results from the fixed one -- a PR that CI cannot catch, because CI
   honestly tests the file that shipped.
7. Run the relevant focused checks, then the repository-required validation.
8. Review its own diff for scope, regressions, security, and compatibility.
9. Commit and push the candidate branch and open the PR, but do not merge it.
10. Put `Completes-Bead: <bead-id>` on the final candidate commit only when
   every acceptance criterion is met. Free-text bead mentions never close work.
11. Send Mayor the PR number, branch, exact head SHA, acceptance-criteria
   mapping, changed files, and test commands/results — including which tests
   were shown red and against what. Report a gate as not green until it has
   actually been run to completion; an interrupted run is not a result.

### Work with expert reviewers, not just the critic (Overseer, 2026-08-09)

The single exact-SHA critic is the merge gate; it is not the only review. For
work with real design surface, the Mayor spawns independent EXPERT reviewers and
has Hyperion work WITH them before the critic ever rules — the same way the
oversight seat runs adversarial security/UX/architecture agents:

- **UX / product** — any change to a human surface (`/playground`, `/review`,
  `/admin`, response shapes a person reads). Spawn one or two independent design
  reviewers (distinct lenses: reviewer-flow, information-hierarchy) BEFORE the
  build; have Hyperion implement the synthesis, then verify in a real browser
  (headless screenshot at desktop AND mobile widths — check, do not assume).
- **Architecture** — a new module, a boundary, a schema/version move, a
  cross-cutting refactor. Spawn an architecture reviewer to pressure-test the
  design (boundaries held, smallest correct change, no parallel store) before
  Hyperion writes much code, and again on the diff.
- **Adversarial** — correctness/security-sensitive logic (a gate, a parser, a
  crypto or token path). Spawn reviewers told to find the FALSE-CLEAR / the leak;
  fix what they find; RE-REVIEW until a pass returns clean. One round is not
  enough — every security PR this session took two or three.

These are independent agents (the `Agent` tool / a `Workflow`), read-only,
adversarial by instruction. Hyperion collaborates with their findings the way it
collaborates with a bounce: incorporate, re-run, report which finding changed
what. The critic still rules last at the exact SHA — the expert pass makes the
thing worth ruling on, and shrinks the bounce loop.

### A mutation sweep without a RESTORED row is not evidence

Item 5 requires knowing which test went red against what. When that is shown by
putting a defect back — a mutation sweep — the sweep itself has to be measured,
because a broken sweep produces exactly the output a working one does. This
binds whoever runs the sweep, the author demonstrating non-vacuity and the
reviewer reproducing it alike; a grader's sweep is evidence the rig serves about
itself in exactly the same way.

Every sweep carries a CONTROL row first, one row per mutation, and a RESTORED
row last. **RESTORED is required to equal CONTROL exactly.** A sweep without one
is not evidence and must not be cited as non-vacuity in a commit body, a PR
body, or a bead. If RESTORED differs from CONTROL the whole table is void — do
not reason about which rows were still good, because the point of failure is
unknown and every row after it is unattributed.

**Print both rows, literally and adjacent.** The rule is unenforceable when the
format only requires RESTORED, because RESTORED can only be compared against
something: with no CONTROL row the reader supplies one from context — a gate
line, a PR body, an expected suite size. That inference yields a plausible
number, the comparison appears to pass, and the check has done nothing. Silent,
and the same class of defect the rule exists to catch.

**Run every mutation against the whole suite, not only the arms you expect to
catch it.** Over three named arms, "this arm caught it and no other did" cannot
be evaluated — three arms can only confirm the one you predicted. Over all of
them it becomes *no other arm fired*, which is the half that carries the weight.
A narrow run cannot detect an arm that catches everything, and an arm that
catches everything is indistinguishable from a good arm right up until it passes
something it should not.

The scope is the suite and not the file, because *no other arm fired* is only
evaluable where the other arms live. Measured on 2026-08-01: an enumerator
injection fired two arms, one in the module its author had just written and one
in the per-PR gate itself (hy-qc4u). The file-scoped run reports the first and
never mentions the second, and the second is the one a reviewer needs — it says
the defect reaches the gate, not merely that the new test works. A narrower
scope is a cost-driven exception and has to be stated as one, alongside what it
could not have seen.

Note where the temptation to narrow lives: it is strongest exactly where your
knowledge is best. On someone else's code you do not know which arms exist, so
you run everything and read what comes back. On your own you know which arm you
wrote for the defect, and naming it feels like precision rather than narrowing.
Knowing which arm *should* fire is what disqualifies you from running only that
arm.

Three things make sweeps lie, all measured on this rig:

- **Restore from a copy taken before the first mutation, never from HEAD.**
  `git checkout -- <file>` restores to HEAD, so when the fix under test is still
  uncommitted, HEAD is the *unfixed* file and the first restore silently deletes
  the change being certified. Every later arm then runs against unfixed code and
  still prints plausible failures, because those arms are red against unfixed
  code by construction. Nothing errors and nothing warns; the RESTORED row is
  the only tell (hy-j1wx, found on hy-qbii).
- **Assert the anchor is present before substituting.** A textual mutation whose
  anchor has drifted no-ops, the arm comes back green, and the sweep reports that
  a guard is unnecessary when in fact it was never removed. This is hy-1pqa's
  shape one level down: a check keyed to a name cannot feel the mechanism move
  underneath it. Fail loudly on a missing anchor rather than continuing.
- **Mutate the constants, not only the logic.** A constant whose value cannot
  change any outcome is a dead gate: carried, not tested. `TRUE_ROOT_DEFAULT` in
  `scripts/clause3-intersection.sh` survived being set to forty zeroes with that
  file's seven arms all still green — every arm either passed the value
  explicitly or short-circuited before reading it (hy-73ed). The arm worth
  adding is the one that FAILS when the constant is wrong, not another that
  passes when it is right.

Run the sweep in a detached worktree, per item 6. The working tree only ever
holds the change being shipped. **The worktree is exclusive to one running
sweep, and the log is named for the head it measures.** Rebuilding a worktree
under a job still running in it, or pointing a second table at the same log,
voids both — measured on 2026-08-01, where the second table produced `fatal:
Unable to read current working directory`, twenty-seven planner failures that no
injection in that table could cause, and torn interleaved output (hy-qc4u).
That void was loud and therefore cheap. It is the same defect as the compose
collision below and worth stating once for both: two jobs sharing one namespace,
a `COMPOSE_PROJECT_NAME` there and a worktree path here. Prefer the loud version
— what makes the compose case the dangerous one is that a shared namespace can
also return a well-formed answer.

The difference is who bears the cost. A loud void is **self-reporting**: it
costs its own author the time to re-run and nobody else anything. A quiet void
costs whoever has to tell you, and it is only ever found by someone else — on
2026-08-01 it cost one seat five retracted numbers, the other a mail, and both
of them the work of establishing whose containers were whose. So the quiet case
carries a duty the loud one does not: **a retraction has to reach everyone who
might have read the number, not only your own log.** That worked here because
exactly two seats were involved and each knew the other. Had a third quoted the
retracted green row in a PR body, nothing would have reached it. This is the
argument for the structural fix over the retraction discipline, one more time: a
namespace that cannot collide needs no retraction protocol.

Finally, a control is only a control if the change under test is the **only**
thing varying between it and the row it is compared against. A control that ran
to completion still measures nothing if something else moved underneath both
rows, and it looks exactly like one that worked. Measured on 2026-08-01: two
seats ran `tests/compose` concurrently against one Docker daemon, which shares a
single `COMPOSE_PROJECT_NAME` across every seat (hy-xjch), so each run was
deleting the other's containers. One seat ran its clean base as a control, saw
the same failure, and correctly concluded "not my change" — for the wrong
reason, since the other seat's containers explained both rows and the pair said
nothing about the diff either way.

Do not read that as an argument for care. The confound was **invisible from
that seat by construction**: nothing in its own output, its own tree, or its own
history distinguished the two cases, and no amount of diligence at one seat
would have surfaced it. It took the other seat knowing its own runs were in
flight. That is why hy-xjch is a fix and not a warning. The transferable habit
is narrow and worth doing anyway: state what you held constant alongside what
you varied, and where you cannot enumerate it, record the control as unverified
rather than as passed.

### Rebasing a crew branch

`remote.origin.fetch` is `+refs/heads/main:refs/remotes/origin/main` only, so no
remote-tracking ref exists for crew branches. A bare `git push --force-with-lease`
fails with `stale info` — it has no lease reference to compare, which is not a
real race. Always name the SHA being replaced:

```bash
git push --force-with-lease=<branch>:<old-sha> origin <branch>
```

Do not widen the refspec to make the bare form work. A lease checked against a
tracking ref that a narrow refspec never updates would succeed while verifying
nothing, which is worse than the visible failure. The explicit form is the rule
regardless. The same missing tracking ref appears to make `gh pr create` claim
the branch was never pushed; pass `--head <branch> --base main`.

A rebase past a merged PR is not just a conflict check. Ask what the merged
change added that the rebased branch's tests now need to cover — a stale gate
run against a tree that no longer exists is the real risk, not a textual
conflict.

### Review heuristics that have paid off

Earned on PRs #74–#81. Put these in review beads rather than rediscovering them.

- **Make the safe state structural, not asserted.** A guarantee that holds
  because a caller passes the right flag, or because an override happens to
  run, fails in the corrupt direction. Prefer a class attribute over an
  override, a fact only one function can set over a parameter any caller can
  claim, and a single chokepoint over an exemption checked at one call site.
- **Do not collapse distinct states into one representation.** `_body_consumed`
  merged "declared 0, read 0" with "declared N, read 0" (hy-670); an empty
  `evidence_refs` would merge "none declared" with "withheld" (hy-74k). Let the
  representation carry the distinction rather than making a second field carry
  it.
- **Prose that asks a caller not to do something is not a control.** If the
  server can withhold the thing instead, withhold it. You cannot seed from a
  list you were not given.
- **A comment is the deliverable when the bead is about a false claim.** hy-ncp
  existed because `catalog.py` asserted something false about its own bound;
  shipping a replacement claim that was false the same way did not close it
  (hy-dw2). Require checkable arithmetic, not adjectives.
- **Grade the ruling, not the compliance.** When the Mayor has ruled mid-
  implementation, say so in the review bead and ask the reviewer which rulings
  it would have made differently. Two briefs this session contained the error
  being reviewed for; a reviewer treating the brief as the standard passes it.
- **Know whether a new test can fail.** A regression test reds against the
  unfixed code. A guard test cannot — it must be shown red against the
  plausible wrong change it exists to catch. Assert the consequence before the
  mechanism, or the test passes the moment someone satisfies the mechanism
  without fixing the damage.
- **A parity test proves agreement, not correctness.** Any assertion of the
  form "A equals B" is blind to A and B agreeing on the wrong thing — proved on
  hy-74k by encoding a withheld list as `[]` over *both* transports and
  watching the whole parity suite pass. Correctness assertions belong in the
  correctness test; parity then carries them to both transports for free. When
  one file covers several operations, ask whether they are covered to the same
  depth — and if not, whether the difference is deliberate and written down.
  Unrecorded difference is how the gap gets there; recorded difference is a
  decision. "Make them the same" is the wrong demand if equalising means
  levelling down.
- **Read the file at the SHA under review, not on `main`.** The Mayor aimed a
  reviewer at a test fixture that the PR had already replaced. If you quote a
  line number or a fixture name, quote it from the commit being reviewed.
- **When a design claims the second implementation will be easy, require a
  cheap second instance now.** "Adding the other SDK is trivial", "this
  extends cleanly" — that class of claim is unfalsifiable at review and gets
  discovered false a quarter later. A fake or stub second instance tests it
  immediately and doubles as the deterministic test double the work needs
  anyway. Then review the *fake* first: anything it was forced to reimplement
  is something the real boundary should not have owned. A boundary does not
  become a framework in one commit — it accretes, each addition individually
  defensible — so a checklist catches it only when someone re-runs it, while a
  fake catches it continuously because every accretion makes the fake bigger.
- **In durable records, cite the symbol, not the line.** Bead notes, close
  reasons and directives outlive the tree they were written against. A line
  number is a coordinate into a moving thing; a function or constant name
  survives the edit. A note that is right in substance and visibly wrong in
  its one cheaply-checkable detail gets discarded whole. The general form,
  earned on hy-698: a record describing a moving thing decays at the rate the
  thing moves — which is why a bead describing drift ages exactly as badly as
  the doc it describes.
- **An absence claim needs a stronger instrument than a presence claim.**
  "No bead exists", "nothing asserts this", "there is no test" are the
  statements most likely to be wrong, because the search that would disprove
  them is the one you chose. Search by the identifier the work hangs off — a
  bead id, a symbol name — not by the words you would have picked for a title.
  A synonym search cannot support an absence claim at all: filtering titles for
  three plausible phrasings returned zero beads where searching descriptions
  for `hy-6ae` returned five, one of them the bead being declared missing.
- **Run it, do not read it.** Every substantive finding this session came from
  opening a socket, subclassing a handler in memory, or building a wide
  fixture. Reviews done by reading found style; reviews done by running found
  three live desyncs and a list two reviews had walked past.

## 3. Monitor without disrupting

**Mail interrupts; nudges do not.** Delivering mail to a crew agent cancels its
in-flight tool call — four times in one session on PR #80, once mid-chain,
leaving the working tree holding a pre-fix file while the fix survived only in
scratchpad. Filed as hy-mvq, escalated as hq-5i6. Until that is fixed:

- Routine crew traffic goes by `gt nudge`, which queues and arrives after the
  current call returns.
- Use `gt mail send` only for rulings that must survive session death.
- Hold even those while a crew member is mid-gate. Ask crew to report when a
  gate starts and finishes, and do not send mail in that window. The hyperset
  gate is ~2.5 minutes; postgres alone is ~90s.
- A cancelled call is reported to the agent as a user rejection. Nobody
  rejected anything — do not let a crew member go hunting for a permission
  problem that does not exist.

- Prefer status, hooks, mail, and convoy state over interrupting the worker.
- Use queued nudges only when a worker is idle with pending work or has stopped
  reporting progress.
- If a session exits while its hook is active, restart or resume that same crew
  workspace and preserve its branch; never reassign the bead or start a
  duplicate implementation.
- Escalate to the human only for a real decision, missing authority, repeated
  infrastructure failure, or conflicting requirements.

## 4. Review, rule, and confirm the merge

Once Hyperion reports its diff self-review is clean and validation passes:

1. Hyperion pushes the candidate branch and opens a focused PR to the default
   branch, then reports the PR number and exact head SHA. Confirm the reported
   head matches the PR's head before anything else.
2. Mayor files a review bead naming the PR, branch, and exact head SHA, and
   slings it to BOTH review seats — `hyperset/crew/critic` (Claude) and
   `hyperset/crew/adversary` (Codex, GPT-5.6 Luna xhigh) — at the same SHA.
   Write the bead against the risk this change carries, not as a generic review
   request; a review bead that lists no specific hazard gets a generic answer.
   Word the adversary's copy to hunt the false-clear the Claude critic could
   miss. If token spend is being managed and the adversary is explicitly paused
   in the Crew section, note that here; otherwise both reviews are mandatory.
3. Each critic reviews read-only at that SHA and reports a verdict, filing beads
   for findings. Neither ever edits the branch. Collect both verdicts before
   ruling; do not merge on one critic's clear while the other is outstanding.
4. Mayor rules on the UNION of both verdicts: which findings gate the merge and
   which trail as follow-up beads. A gating finding from EITHER critic gates.
   Findings that gate go back to Hyperion on the same branch; it fixes,
   revalidates, and reports a new head SHA, which is re-reviewed by both.
   Repeat without opening a second implementation bead.
5. Once the review is clean, ensure the head is pushed and a focused PR to the
   repository's default branch is open.
6. Hand the ruling to the **refinery merge seat**: the PR number and the exact
   reviewed head SHA, with the findings that gate already decided. The live
   Hyperset refinery runs the write-capable `codex-sol-high` alias and is the
   only seat permitted to press the merge button. It grades nothing and must
   refuse any request without the complete gate: both independent critic
   verdicts, all CI green, and clause-3/refcheck clean against current
   `origin/main`. The gate is strict and unchanged; only the executor's model
   is Codex. Do not merge here yourself and do not run `gt mq post-merge` here.

   Carry the head SHA, never the `commit_sha` the merge queue prints: that field
   is written once when `gt done` submits the branch and is never refreshed, so a
   remedy push leaves it naming a commit that has been replaced. It has already
   been carried into a review request from this seat once (hy-tbl0).
7. Confirm the refinery merge report is actually present on the remote
   default branch.

   Confirming a merge is not performing one. This step reads a remote that a
   different seat wrote, which is the whole reason it exists: a report of a merge
   and a merge are different facts, and the failure where they diverge is silent.
   If the merge is not there, the bead is not done -- say so and return it, do
   not finish the job by merging it yourself.
8. Only then select the next ready bead.

Never mark work complete merely because implementation finished. Completion
requires validation, Mayor's own review, passing checks, and a verified merge --
verified on the remote by this seat, whoever performed it.

This procedure is the only thing standing between a red gate and `main`. A merge
that happens only after a review at an exact SHA is what this project relies on
instead of enforcement (ADR 0014); relaxing that leaves nothing. Splitting the
grader, the merger and the dispatcher across three seats is a strengthening of
that rule and not a relaxation of it: no seat both grades a change and lands it.
