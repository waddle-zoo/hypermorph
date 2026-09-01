# Hyperset: refinery merge execution

The refinery executes merges. The mayor dispatches scope and rules on findings;
the critic grades at an exact SHA. This file is the merge half of the loop that
`mayor.md` describes, and the two are deliberately separate documents: the
grader, the merger and the dispatcher are different seats, and a single file
describing all three invites one seat to read another's steps as its own
(Overseer ruling, 2026-07-31).

What arrives here is a ruling, not a request: the mayor has read the critic's
verdict, decided which findings gate, and named the reviewed head. This seat
does not re-open that judgement. It checks that the SHA it was handed is the SHA
it merges, and refuses when it is not.

## The single-merger rule lives here

The Hyperset refinery runs the write-capable `codex-sol-high` agent profile.
That profile is an execution identity only: it may land a fully gate-met PR,
but it is not one of the read-only critics and must never supply a review
verdict for the same change.

**Exactly one seat merges, and it is this one.** That is the whole of the
project's enforcement, and it is weaker than it sounds: branch protection and
rulesets both return 403 on this repository's plan, every agent here holds admin
on the repository, and any of them *could* merge at any moment. The rule is
"only the refinery *does* merge", never "only the refinery *can*" (ADR 0014).

Read the difference between that and "the refinery executes merges". The second
assigns ownership; the first forbids a second merger. Only the first is a
control, and a reader who takes the weaker sentence for the stronger one has
lost the only thing standing between a red gate and `main`. If this seat is
unavailable, work waits -- the correct response to a stalled queue is to say so,
never for another seat to press the button once "just to unblock it", which is
exactly the relaxation ADR 0014 says would have to be argued rather than done.

The mayor confirms afterwards that a merge this seat reported is present on the
remote default branch. That is a read of a remote, not a second merge button,
and it is the reason that confirmation is safe to keep in another seat.

## 0. The default loop is a poll, not a wait

This seat's default state is a POLL, not an idle wait for a nudge. The stall that
forces the change: a critic-and-adversary-approved, green, MERGEABLE pull request
sat unmerged -- #298 for 5h, #300 for 16h -- because the merge waited to be told.
It must not wait. `scripts/refinery_poll.py` runs on a loop and lands every open
PR that is already clear to land:

```bash
uv run python scripts/refinery_poll.py                  # one pass, then exit
uv run python scripts/refinery_poll.py --interval 300   # a pass every 5 minutes
```

Each pass enumerates the open PRs and, for each, LANDS it only when ALL of these
hold at the LIVE head, and SKIPS it (leaving it for the next pass) on any one
failing:

* the PR is OPEN and the forge itself calls it MERGEABLE -- not CONFLICTING, and
  not the still-computing UNKNOWN;
* `scripts/merge_precheck.py`'s own `evaluate` is CLEAR at the live head: every
  required CI check COMPLETED and SUCCESS, head != base, a trusted MERGE verdict
  NAMING the exact head, the `Completes-Bead` trailer present, and every
  structured `constraints=` condition (hy-sofx `merge_after` / `do_not_close`)
  honoured;
* the merge is authorized DUAL-MODEL AT THE LANDING TREE: two DISTINCT review
  ROLES (`role=critic` AND `role=adversary`) each posted a MERGE verdict that PINS
  a reviewed tree (`tree=<oid>`) STILL equal to the tree that would land NOW
  (`gate.gate_describes_would_land`, #300). Distinctness is by ROLE, not by forge
  login: this town posts every review comment as ONE GitHub account, so a
  distinct-login count is always 1 and nothing would ever auto-land (hy-irni).
  Role distinctness is discipline -- the reviewers must tag their verdicts
  `role=critic` / `role=adversary` -- as forge authorship has always been here;
  one comment cannot supply two roles (a comment naming two roles counts for
  none). The count is `HYPERSET_MIN_MERGE_APPROVALS`, floor two, config can only
  RAISE it. A verdict with no `role=` tag, no `tree=` pin, or a pin a base advance
  has since made stale authorizes no auto-land.

This last clause is the fix for the #302 BLOCKER, and it is the load-bearing one.
Checking only that the would-land merge has *a* tree (no conflict) was NOT enough:
on any main advance from base B to B', the poll would have landed `merge(B', H)`
-- a tree no verdict reviewed, and one that (ADR 0014: no branch protection, so a
red run still merges) was never CI-retested. `--match-head-commit` pins the HEAD,
not the base, so the advance is invisible to it. The tree-identity check is what
closes it: if the current would-land tree still EQUALS the reviewed, pinned tree,
the merged content is exactly what CI ran and the reviewers named (the head
already contains the base delta -- a fast-forward, or the same content by another
commit); if the advance CHANGED that tree, no pin matches and the poll re-gates
rather than landing something unreviewed. Tree identity SUBSUMES CI-freshness:
matching the tree is the whole check, and a base move that changes it is never
trusted on the old green.

The button is pinned: `gh pr merge --match-head-commit <head>` makes the forge
refuse if the head moved between the decision and the merge. The base cannot move
under the decision either, because this is the ONE refinery seat running ONE
sequential loop (below): each PR is fetched fresh, and no other merger advances
main between a decision and its button. The loop REUSES the human control
(`merge_precheck.evaluate`), the authorizer selection
(`merge_precheck.merge_authorizers`), and the tree-identity primitive
(`gate.gate_describes_would_land`) unchanged -- it imports them, it does not
re-implement them -- so the autonomous gate and the one a human runs cannot drift.

This does not relax the single-merger rule below. The poll runs in the ONE
refinery seat; being poll-driven makes the one merger stop waiting, it does not
make a second merger appear.

### Running it as a real process, and reading its log

A Claude seat cannot BE the loop: it reports "polling" but does not reliably
re-invoke the pass across context-cycling, so #305 -- every gate clear -- sat OPEN
for >2 intervals and never landed (hy-wpqa). Run the poll from a real detached
process, never by hand and never from a scratch copy:

```bash
HYPERSET_CRITIC_LOGINS=bsovs scripts/refinery_poll_daemon.sh start    # detached
scripts/refinery_poll_daemon.sh status                                # running? + tail
scripts/refinery_poll_daemon.sh dry-run                               # one pass, no merge
```

Two environment facts are load-bearing, and the poll asserts BOTH at the start of
every pass, refusing to run and naming the fault ONCE rather than skipping every
PR silently:

* it must run inside a real **git work tree** (`gate.is_git_repo()`). The #305
  no-land was the whole poll running from a non-git scratchpad copy, where every
  `gate.would_land_tree` failed for the ENVIRONMENT -- so every verdict's
  tree-identity check came back empty and `merge_authorizers` went to zero roles,
  while the pane still said "polling". `gate.would_land_tree` now RAISES
  `GitEnvError` on such a fault (a genuine merge conflict is still a quiet `None`),
  so an env fault can never again masquerade as "nothing to land". Run it from a
  checkout or a detached worktree at `origin/main`;
* `HYPERSET_CRITIC_LOGINS` must name the reviewer login(s), or no verdict is
  trusted and every PR skips as unauthorized.

Every pass logs a start line (echoing the trusted logins, required checks, and
approvals floor), one `WOULD MERGE` / `SKIP -- <gate(s) unmet>` line per open PR,
and a summary. So "is it really polling, and why did a clear PR not land" is
answered from the log, not guessed: `would_land None for every PR` reads as an
environment fault (now a startup FATAL), a single PR's `SKIP` names the exact gate
it missed.

**On a merge, the poll runs the §4 reconciliation itself (hy-n0ge).** The
hands-off loop closes the source bead and clears the merge-queue entry without a
human. It fires ONLY after an actual merge, and is fail-safe, idempotent, and
never closes the wrong bead:

* the bead it closes is the SINGLE `Completes-Bead:` trailer the merged commits
  carry. Zero (no trailer) or more than one (ambiguous) closes NOTHING and logs it
  for a human -- the reconciler never guesses which of several beads to close;
* the landed tree must EQUAL the graded tree, or it closes nothing and shouts: the
  reason it writes ("graded == landed") is verified, not asserted, so a race that
  landed a different tree is caught;
* a bead that does not resolve is left open; a bead already `closed` is skipped, so
  a second pass does not error;
* a matching `gt mq` entry is cleared with `--skip-branch-delete` -- the branch
  outlives the merge (delete_branch_on_merge is off) and its deletion is the
  guarded, separate `scripts/delete_branch.py` path (hy-mlj0). A crew PR has no mq
  entry, which is a logged no-op.

**What the poll still deliberately does NOT do.** The clause-3 / refcheck coupling
analysis of §2 -- the merged-tree-red NOMINATION class (#202, #197), where a clean
merge with a green head and a green main is nonetheless red at the merged tree --
is measurement-and-judgement whose arms end in "run the suite at the merged tree"
or "read the referencing text and decide", which the poll cannot resolve
fail-closed without running the suite per PR. The poll lands the class the
dual-model review at the exact head has already judged; a PR whose merged-tree
risk needs §2's instruments is landed by a human running §1-§2. This boundary was
called out to the mayor on hy-8b6c; narrow it only by a ruling, never by widening
the poll to press the button on a nomination it did not measure.

## 1. Fetch the reviewed head

Fetch the branch head yourself. The reviewed SHA is the branch head you fetch,
never the `commit_sha` the merge queue prints. That field is written once when
`gt done` submits the branch and is never refreshed, so a remedy push leaves it
naming a commit that has been replaced -- on #154 it named the commit the critic
had already bounced, two heads behind the branch, while the entry still read
`ready` (hy-tbl0). It caught the mayor directive's own change too: the entry for
that work read `commit_sha ea22d73` while the branch head was `05ac93f`, and that
stale value is the SHA the mayor carried into the review request. Nothing in the
merge path reads it either: gt's own refinery formula merges a branch it fetched.
Treat it as a label on the submission, not as an instruction.

Refuse the merge if the fetched head is not the SHA the ruling names. A branch
that moved after the verdict has not been reviewed, whatever the queue says.

## 2. Merge only the reviewed SHA, with its checks read

Merge only when the exact reviewed SHA has passing checks. Nothing on GitHub's
side enforces this: branch protection and rulesets are both unavailable on this
repository's plan, so a red run marks the pull request `UNSTABLE` and the merge
button still works, and a branch that predates a job never reports it at all.
Checking is therefore this step's job, not a formality on top of a mechanism
(ADR 0014). Read the rollup for missing checks as well as failing ones. CI green
plus MERGEABLE is not the gate on its own -- the gate is the critic verdict plus
passing required checks, both read at the exact reviewed SHA.

A CI failure is not this seat's to fix. Return the bead to the implementer on the
same branch through the mayor, and merge nothing until a new head is reviewed.

### The clause-3 checks, before the button

Two scripts run here, in that order, and this seat is the one that runs them.
`scripts/clause3-intersection.sh` computes hy-fy7d clause 3's merge base and its
file-level intersection. `scripts/refcheck.sh` is hy-asip's reference-level
companion and is HANDED a merge base rather than computing one -- a file-level
intersection cannot see a docstring, a skip reason or a documentation line naming
the module a branch is rewriting, which is why the second is a grep over the
merged tree rather than an import graph. Compute first, then consume: the second
cannot be run correctly without the first's answer.

Neither script's arguments are lying around. Step 1 leaves this seat holding a
fetched branch head and `origin/main`, and neither of those is a merged tree.
Build all three values from those two commits:

```bash
H=$(git rev-parse "origin/${branch}")                 # the reviewed head, step 1
M=$(git rev-parse origin/main)
MB=$(git merge-base "${M}" "${H}")
T=$(git merge-tree --write-tree "${M}" "${H}"); MTRC=$?
[ ${MTRC} -eq 0 ] || echo "CONFLICT (rc=${MTRC}): T is the conflict report, not a tree"

bash scripts/clause3-intersection.sh "${H}" origin/main
# 0 intersection empty   4 intersection NON-EMPTY   3 histories do not meet
# 5 intersection empty but a diverged side touched a fixture/recording dir: run the merged-tree suite
# 2 REFUSE               64 usage
# OPEN SET: `|| exit $?` at :108-109 can deliver a code not listed above

bash scripts/refcheck.sh "${T}" "${M}" "${MB}"
# 0 no references   3 NOTE (first half)   2 HOLD (both halves)   1 REFUSE or usage
```

`merge-tree --write-tree` prints a tree oid on a conflict as well as on a clean
merge, so its exit code is the only conflict signal and a pipe eats it -- `git
merge-tree --write-tree A B | cat` returns rc=0 on a conflict. Keep that
assignment bare, and capture `$?` on the same line: a later command overwrites it.
Brace every one of these variables: unbraced `"$REF:refs/..."` is eaten by zsh's
`:r` modifier and the fetch fails against a mangled refname.

**Nothing below that line is safe to run when `MTRC` is not 0, and no `exit` will
stop you** -- this is a block pasted into an interactive shell, where `exit`
closes the seat's terminal. The earlier form, `|| echo "CONFLICT: stop here"`,
printed and returned 0 and execution carried straight on (hy-6746). Read the
line and stop by hand.

What `T` holds on a conflict is not a tree oid but the whole conflict report --
measured on git 2.39.5, rc=1, with the tree oid on line 1, the three conflicted
stage entries after it, then `CONFLICT (content): Merge conflict in <file>`.
Handed that, `refcheck.sh` answers `REFUSE: cannot resolve <the whole blob>` at
exit 1. That fails closed, which is why this is a note rather than a hazard, but
the exit code sends the reader to "the check could not see" when what they had
was a conflict. `MTRC` is what tells them apart.

**Exit 4 is a NOMINATION, never a bounce.** What a non-empty intersection
obliges is a measurement -- the trial merge, then the suite at the merged tree --
and the outcome of that measurement may perfectly well be "merge it". Naming a
file is not showing a breakage. The arm earned its keep once, on #202: the
intersection named two files, one of them was implicated, and the merge was
genuinely red while main was green, the head was green, and git reported no
conflict. Nothing else in this loop looks at that class. Reporting the
intersection as a finding in its own right would have bounced a branch on a
filename.

**Exit 5 obliges the merged-tree SUITE, not just the trial merge (hy-1ryd).** A
file-level intersection is blind to coupling through data a test reads at runtime:
a test that globs a fixture/recording directory reads files it shares no path
with, so main rewriting one of those files and the head editing the test
intersect to nothing -- and the conflict-only trial merge is blind to it too,
because the two sides touch different paths and merge clean. Measured on #197,
which touched only `test_committed_recordings_resolve.py` while main had gained
#194's rewrite of two recordings that test reads: empty intersection, clean
merge, and only the suite run at the merged tree could have seen it. So when the
intersection is empty but a diverged side touched a read-set path -- a fixture or
recording directory, anything under `docs`, or a contract file a test checks
against source (`ci.yml`, `pyproject.toml`, `AGENTS.md`, `CLAUDE.md`,
`expected_failures.yaml`) -- the script exits 5 and you run the suite at `T` (the
merged tree), not merely the trial merge. Like exit 4 this is a nomination -- the suite may
well come back green -- and it does not fire when main did not diverge, because
then the head contains main and its own CI already ran that suite.

**Both sides pass `--no-renames`, on all four diffs (hy-8v1k).** A rename is
reported as one moved file and the old path never enters the changed set, while a
stale reference cites the old path by definition -- so rename detection was blind
to exactly the class these checks exist for. Measured on #203 on 2026-08-01 over
merge base `21f5deb4`: main's side read 40 files with detection and 44 without.
Four files invisible, in a live case. The direction is why it was a P1 and not a
tidy-up: fewer files, smaller intersection, `NO INTERSECTION: clause 3 satisfied`.
It failed toward the reassuring answer and it did it silently, so nothing in the
output said the set was a lower bound. Three arms hold it now -- one in
`test_clause3_intersection.py` and two in `test_refcheck.py` -- each measured red
against the detecting form before the flag went in.

**An empty main-side set means the head CONTAINS main, which is the strongest
state and not a blind one.** It happens exactly when the merge base equals main,
so there is no divergence and the class clause 3 nominates cannot exist. Empty
evidence is blindness; an empty question has a correct trivial answer. And in
that state the head's own gate line is already the merge gate line: measured on
#202's final head `0deee38c`, `head^{tree}`, `merge-tree --write-tree` against
main, and the `main^{tree}` that actually landed were all
`614ce956d6fd115cdb73df0aa0be2fbaa3984b19`. The integration has already happened
and has already been run, which is why the same pull request was red at
`3ce417ae` and clean after the rebase. Do not read that rc=0 as a check that
failed to look, and do not make it a refusal: it would then refuse on every
properly rebased branch -- the state authors are told to reach, and the state
carrying the best evidence -- and a check that fires on the healthy majority is
ignored by the time it matters.

What that rc=0 does not distinguish is a comparison of two non-empty sets from a
comparison with nothing on one side. Read the line above the verdict, which
prints both counts: #203 reads `head changed 1 file(s); main changed 40 file(s)
since`, and that is a real answer. A rebased head reads `0` on the main side.
Either way the answer is TIME-LOCAL -- true only while main is still the commit
the base was computed against, expiring silently at the next landing -- so
recompute it rather than carrying it forward (hy-kiwk).

A HOLD obliges the reader to open the referencing text and say whether this
merge falsifies it. It does not oblige a fix -- that is a bead. A REFUSE is
never an answer: the check could not see, and a merge on a refusal is a merge on
nothing. `refcheck.sh` spends exit 1 on its usage error as well as on REFUSE, so
a mis-called script and a blind one are indistinguishable by exit code; read the
stderr line before deciding which you have. It lived in this seat's scratchpad
until hy-ou0o, where it was ported with the four instrument defects it found in
itself preserved as arms in `tests/unit/test_refcheck.py`; the five-merge base
rating is one of those arms, so a change that moves any of #190, #187, #189,
#192 or #193 fails the suite.

## 3. Verify the merge on the remote default branch

Verify the merge is present on the remote default branch before reporting it.
The mayor confirms this independently afterwards; two seats checking the same
fact is the point, because this is the one step whose failure is silent.

## 4. Reconcile the merge queue

`gt done` leaves a merge-request bead behind, and merging the pull request on
GitHub does not touch it: it survives its own merge, keeps scoring `ready`, and
sorts to the top of a queue with nothing outstanding in it. Two entries did
exactly that on 2026-07-30 (hy-tbl0). gt's refinery formula marks this cleanup
REQUIRED, but a merge performed through a pull request does not run that
formula, so nothing runs it unless this step does.

```bash
gt mq list hyperset                 # ID column, matched by the BRANCH column
gt mq post-merge hyperset <mr-id>
gt mq list hyperset                 # confirm the entry is gone
```

`gt mq list <rig>` is both the find and the confirm, and the reason is the rig
argument: it names the database it is asking. `bd list` does not. Which database
answers a `bd` command is decided by the directory you are standing in, and
neither the command nor its output tells you which one replied. From `~/gt/mayor`
`bd list` answers from the TOWN database and returns no rig merge requests, with
the flag, with the label, or by `--id`. So a correct command with the correct
flag still prints `No issues found.` there, and it is telling the truth about the
wrong database.

What you lose from the town cwd is ENUMERATION, not access. `bd show
hy-wisp-c98` from `~/gt/mayor` returns the entry in full; only `bd list` refuses.
So you can inspect an entry whose id you already have, and cannot discover one
you do not -- which is precisely the half this step needs. The reach is one-way:
`bd show hq-1vkf` from a rig directory errors.

That example has since expired, which is the section's own lesson arriving on its
own text: on 2026-07-31 `bd show hy-wisp-c98` answers `no issue found matching
"hy-wisp-c98"`. The entry is gone, so the id is now only an illustration of the
shape. Read the mechanism, not the id.

These entries are INFRASTRUCTURE beads, hidden from `bd list` by default. A
documented flag does reach them, from a rig directory:

```bash
cd ~/gt/hyperset
bd list --include-infra --label gt:merge-request
# -> hy-wisp-c98, hy-wisp-fbh, hy-wisp-hxs
```

Two directory changes and a flag, to get what `gt mq list hyperset` prints from
anywhere. Use `gt mq list`. Why `--include-infra` lifts these is not documented
-- bd's help for it names "agent/role/message" and mentions neither wisps nor
merge requests -- so do not build on the mechanism, only on the observation.

Without that flag no `bd list` filter finds them, and the three ways it fails are
not equally honest. Line counts below were measured on 2026-07-30 and move with
the database; the omission is the stable part, not the number.

`--label gt:merge-request` prints `No issues found.` -- ambiguous, invites a
second look.

`--wisp-type merge-request` errors outright, which is the honest failure:
`invalid wisp-type "merge-request" (must be heartbeat, ping, patrol, gc_report,
recovery, error, or escalation)`.

`--type=task` and `--all` are the ones to distrust hardest, because they do not
look empty. From `~/gt/hyperset`, `--type=task` printed 130 lines and `--all`
printed 512, and both omitted all three entries without a word. From `~/gt/mayor`
the same two commands printed 54 and 410 lines, from a different database, and
also omitted them. Grepping either listing for `hy-wisp` does not save you: the
single hit was an ordinary bead with a wisp id in its TITLE. A long confident
listing sends the reader away sure that `bd list` works and that no merge-request
beads exist -- a firmer false belief than the one this step exists to prevent,
reached with more apparent evidence (hy-173b).

That one command closes the merge-request bead, closes the SOURCE issue, and
deletes the merged remote branch, which covers the bead and issue half of this
step. It closes the source issue only, so a fix bead whose commits rode in on
another bead's merge request survives its own merge -- check the branch's commits
for a second bead id and close it by hand. Then update the linked GitHub issue.

`delete_branch_on_merge` is off on this repository, so the branch does outlive
the merge and does need deleting; the pull request is already recorded as merged
by then, so removing its head branch afterwards does not change that.

### The deletion argument comes from the forge, bound to the PR, and is checked

`gt mq post-merge` deletes the branch from the merge-request entry, and that is
the ordinary path. But a fix bead whose commits rode in on another bead's merge
request survives its own merge, and deleting ITS branch is a by-hand step -- and
by hand is where the deletion argument has no provenance. On #172 the refinery
wrote a merge subject naming the WRONG branch: a live ref, `crew/hy-ving-...`,
belonging to #160, not the `crew/hy-f37x-...` it had actually merged. Had it taken
"delete the branch" from that message -- the obvious source, one line up -- it
would have deleted a live remote ref for a PR it was not working on, and nothing
in the path would have stopped it (hy-mlj0). A branch name from a message or from
scrollback is UNVERIFIED INPUT one step from a destructive command.

So never delete a branch by a name read from a merge message, a subject, or
scrollback. Delete by PR NUMBER -- the one value in that subject that was correct
and that resolves -- through the guarded boundary:

```bash
uv run python scripts/delete_branch.py <pr-number>   # exit 0 deleted, 2 refused
```

It reads `head.ref` from the forge at the moment of use (`gh api
repos/{owner}/{repo}/pulls/<n> --jq .head.ref`), VALIDATES the name against a safe
charset (no shell metacharacter, no `..` traversal, no leading dash), and asserts
the ref tip is an ANCESTOR of the default branch before it deletes -- a merged
branch is safe to delete, an unmerged one (or a wrong name that somehow validated)
is refused. The name is passed as a single argument, never interpolated into a
shell. Any failure ABORTS with exit 2 and deletes nothing; the destructive command
never runs on an unverified name. The ancestor check is the durable half: it makes
the step self-verifying regardless of where the name came from, which is what the
refinery did by hand on #188 and what now lives in the procedure.

Reconcile in this step, not later, because the queue keeps no history to come
back to: a superseded or rejected entry does not close, it disappears. `bd show
hy-wisp-cq8`, rejected earlier the same day, answers `no issue found matching
"hy-wisp-cq8"`. Whatever the entry asserted is gone with it.

## 5. Report the merge

Report the merged SHA and the queue state back to the mayor, which is what that
seat's confirmation step reads. Reporting a merge is this seat's last action on
the bead; selecting what runs next is the mayor's.
