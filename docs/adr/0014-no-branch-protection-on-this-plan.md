# 0014: No branch protection on this plan — one merger is the control, and checks report

Status: accepted.

## Context

Measured 2026-07-28 against `waddle-zoo/hyperset`:

```
gh api repos/waddle-zoo/hyperset/branches/main/protection
-> 403 "Upgrade to GitHub Pro or make this repository public to enable this feature."
gh api repos/waddle-zoo/hyperset/rulesets
-> 403, same message
```

The repository is private and organization-owned on a plan that offers neither
branch protection nor rulesets. There are therefore no required status checks
on `main`, and none can be configured. Three things follow that this project
had been writing as though they were false:

- A red CI run marks a pull request `UNSTABLE`. The merge button still works.
- A pull request whose branch predates a new workflow job never reports that
  job at all, and nothing notices the absence.
- `hyperset/evals/expected_failures.py` exits nonzero on an undeclared
  governed failure. That ratchet is correct and it stops nothing by itself.

The words did not match. `.github/workflows/ci.yml` calls its jobs "separately
requireable" checks and, on the branch for `#25`'s harness, "the required
benchmark gate"; `docs/directives/mayor.md` said to merge "only when the exact
reviewed SHA has passing required checks". Requireable, not required — and a
gate nothing enforces is a gate only while everyone keeps choosing to honor it.

This also corrects a premise used in an earlier ruling: merging `#103` with a
red gate was argued against partly on the grounds that a red required check
would block every subsequent pull request. It would not have. That ruling
stands on its other grounds — a red `main` misleads everyone reading it, and
merge pressure on a scorer is exactly what the benchmark freeze exists to
resist — but the blocking argument is unavailable and should not be repeated.

## Decision

**Name the control that actually holds, and stop claiming one that does not.**

1. **The control is that exactly one actor merges — by custom, not by
   capability.** Every merge to `main` goes through that one seat, after a
   critic review at the exact head SHA, with CI results read as evidence for
   that review. Which seat holds it moved on 2026-07-31: the Overseer ruled that
   the **refinery** executes merges and the Mayor dispatches scope and rules on
   findings (hy-yyzn). The control is unchanged by that move and does not
   survive being read loosely — "the refinery executes merges" is a statement
   about ownership, and this ADR's claim is the stronger one that **no second
   seat merges at all**. The Mayor confirms a merge on the remote afterwards,
   which is deliberately not the same act. Read that as weakly as it deserves.
   Measured on the token this crew shares:

   ```
   gh api repos/waddle-zoo/hyperset --jq .permissions
   -> {"admin":true,"maintain":true,"pull":true,"push":true,"triage":true}
   viewerPermission ADMIN, viewerCanAdminister true, viewer bsovs
   ```

   Every agent operating here holds admin on the repository. Any of them could
   merge a pull request, or change the repository's settings, at any moment.
   The rule is "only the refinery *does* merge", never "only the refinery
   *can*", and it is written in those words because a control whose strength is
   overstated is worse than one that is honestly weak: people stop watching the
   second kind, and watching is the only thing making the first kind work. The
   seat name in that sentence is the part that changed; the shape of the claim,
   and its weakness, are the parts that must not.
2. **Checks report; they do not gate.** CI, the expected-failures ratchet, and
   the benchmark freeze are evidence a reviewer weighs. The word "required" is
   removed from the operational documents rather than left standing as
   decoration.
3. **Enforcement is not bought or published for CI's sake.** Making the
   repository public would enable protection at no cost, and would also publish
   v0 research, evidence, and internal directives before this project intends
   to. That is a product and disclosure decision; it must not be made as a side
   effect of wanting a green checkmark to mean something. A paid plan is the
   clean fix and costs money that is the Overseer's to spend.
4. **Recorded triggers to revisit, whichever comes first**: the first
   contributor outside this crew, the first merge that lands red on `main`, or
   any decision to make the repository public for its own reasons. At that
   point, buy the plan or take the free enforcement that publication brings,
   and require the `migrations` and `test` checks the same day — with "include
   administrators" set, or the same admin bypass rebuilds the same gap under a
   green padlock.

A local `pre-push` hook was rejected as the control. It is bypassable with
`--no-verify`, absent from a fresh clone unless `core.hooksPath` is configured,
and it cannot touch the merge button — which is the place the risk actually
lives. It may exist as a convenience; it may not be described as enforcement.

## Consequences

- A green check on this repository means "the checks that ran, passed". It does
  not mean they all ran, and it does not mean nobody could have merged around
  them. Both halves are live: the benchmark job is defined on `#25`'s harness
  branch, so every other open pull request reports `migrations` and `test` and
  reports the benchmark not at all, with nothing displaying the absence.
  Anyone reasoning about the ratchet or the benchmark freeze as mechanisms
  should read them as conventions among agents, which is worth knowing before
  treating a green check as proof.
- Reviewing means reading the check rollup for what is missing as well as what
  is red. A branch that predates a job never reports it, and no plan feature
  is available to notice that.
- The refinery's merge procedure (`docs/directives/refinery.md`) is now
  load-bearing rather than ceremonial. If the single-merger rule is relaxed,
  this project has no enforcement at all, and relaxing it is the decision that
  would have to be argued. Splitting the seats does not relax it: the dispatcher
  and the grader gained no merge step when the merger gained one, and a version
  of this split in which two seats may merge would be that relaxation, argued or
  not.
- The benchmark gate on `#25`'s harness branch still carries the phrase "the
  required benchmark gate" in `.github/workflows/ci.yml`. That branch is under
  review and its SHA is deliberately not moved by this ADR; the phrase is
  corrected when that work next moves.
- ADR 0013 already said a scheduled benchmark failure does not block a merge by
  itself. This ADR generalizes that from the scheduled job to every check in
  the repository, for a different reason: not the cost of a runner, but the
  absence of any mechanism to block with.
