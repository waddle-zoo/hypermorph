"""What commit a recording is evidence about, established rather than reported.

A recording carries `git_commit`, and `docs/development/benchmark.md` makes that
field load-bearing twice: a committed recording is only evidence about the
commit that scores it, and any prompt, tool or case edit "from the commit that
recorded these runs" invalidates it. Both sentences are claims about a commit a
reader has to be able to resolve.

WHAT WENT WRONG (hy-r1i0, measured). `run_case` read `git rev-parse HEAD` per
call. One 17-minute `HYPERSET_RECORD=1` session over four case/arm pairs wrote
TWO different commits, neither of which was the commit under test: an
auto-checkpoint hook committed the tree twice while the run was in flight, so
every later recording quietly re-labelled itself with whatever HEAD had become.
Squashing those checkpoints away -- the normal cleanup -- left three of the four
recordings pinned to objects reachable from no ref at all, which would not
survive a fresh clone or a `gc`. `stability_report` could not see it: it
compares `git_commit` across the REPETITIONS of one case, and each case's three
repetitions happened to fall inside one checkpoint window.

THE SHAPE OF THE FIX, and why the other one is worse. A recording session
establishes its commit ONCE -- refusing a dirty tree, and refusing a HEAD that
is reachable from no ref -- and every later run re-checks that HEAD has not
moved, so a mid-run commit is a loud refusal instead of a silent re-label. The
alternative on the bead was to stop the checkpoint hook from firing and to
validate only at write time. Write-time validation is kept, in `run.py`, because
it is cheap and it is owed (hy-tz03); it is not sufficient on its own, because
at the moment the hook commits, the checkpoint IS the branch tip and therefore
IS reachable -- it only becomes dangling later, when the checkpoints are
squashed away. The check that can see the defect while it is happening is the
one that notices HEAD moved. Suppressing the hook is worse still: it is a fix in
another repository's configuration, it holds only for the agent who remembers
it, and nothing about it fails when it is forgotten.

Untracked files count as a dirty tree. The question being asked is whether the
named commit describes what ran, and a module nothing has committed yet is
imported exactly like a modified one.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]

DIRTY_PATHS_SHOWN = 5
"""How many changed paths a refusal names before it counts the rest. Enough to
recognise the tree, few enough that the reason stays on screen."""


class ProvenanceRefused(RuntimeError):
    """A run or an artifact that cannot honestly name the commit behind it."""


class TreeDirty(ProvenanceRefused):
    """The tree does not match the commit a recording would claim.

    Named apart from the two below because the answer is different and the
    caller should be told which one they are looking at: this one is fixed by
    committing the tree, not by re-recording.
    """


class CommitUnresolvable(ProvenanceRefused):
    """A commit nobody else can look up: empty, absent, or dangling."""


class CommitMoved(ProvenanceRefused):
    """HEAD moved while a recording session was in flight.

    Separate from `CommitUnresolvable` because it reports what that one cannot:
    at the instant a checkpoint hook commits, the new commit is perfectly
    resolvable. What is wrong is that the session now describes two trees.
    """


class CommitOffDefaultBranch(ProvenanceRefused):
    """A resolvable commit the default branch does not contain.

    Apart from `CommitUnresolvable` because the repair differs and the reader
    needs to know which they have: a dangling object is repaired by finding the
    commit, and this is repaired by landing it or by re-recording.
    """


class DefaultBranchUnverified(ProvenanceRefused):
    """The default branch moved, or may have, and nothing here has measured it.

    Apart from `HistoryIncomplete` because the repository is not missing
    anything: every object resolves and the histories connect. What is missing
    is a reason to believe the tracking ref still describes the branch, and the
    repair is `git fetch` rather than `git fetch --unshallow` and nothing like
    re-recording.

    Named for the same reason as its neighbours: this is the refusal that would
    otherwise arrive as `CommitOffDefaultBranch` -- the arm the docstring calls
    a genuine finding -- carrying a sentence that is simply false about a commit
    that landed on main after this seat last fetched.
    """


class HistoryIncomplete(ProvenanceRefused):
    """The repository cannot see enough history to answer, so it does not.

    The whole reason this is a class and not a `False` return (hy-narn, and the
    rule hy-jkqi now carries). `git merge-base --is-ancestor` exits non-zero for
    "not an ancestor", for "that object is not here", and for "this clone was
    truncated and I cannot tell", and a caller reading the exit code alone
    reports the first whichever one happened.

    Measured rather than imagined: a shallow checkout of THIS repository
    answered exit 1 for a commit 124 commits deep in main, with the object
    resolving perfectly the whole time, and a P1 defect was filed on the
    strength of it. A guard that cannot establish its own preconditions refuses,
    loudly, naming what it could not establish. It never answers no.
    """


def _git(*args: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def head_commit(root: Path = REPO_ROOT) -> str:
    """`git rev-parse HEAD`, or a refusal naming what git said.

    A `CalledProcessError` would reach the caller as a stack trace about a
    subprocess, leaving the thing that actually happened -- there is no commit
    to pin -- encoded in an exit code.
    """
    done = _git("rev-parse", "HEAD", root=root)
    if done.returncode != 0:
        raise CommitUnresolvable(
            f"nothing resolved HEAD in {root} -- git said {done.stderr.strip()!r}; a run that "
            "cannot name its commit cannot be evidence about one"
        )
    return done.stdout.strip()


def uncommitted_changes(root: Path = REPO_ROOT) -> tuple[str, ...]:
    """The porcelain lines for everything not in the commit, ignored files aside."""
    done = _git("status", "--porcelain", root=root)
    if done.returncode != 0:
        raise CommitUnresolvable(
            f"nothing read the working tree state in {root} -- git said {done.stderr.strip()!r}"
        )
    return tuple(line for line in done.stdout.splitlines() if line.strip())


def refuse_a_dirty_tree(root: Path = REPO_ROOT) -> None:
    """Refuse to start a session from a tree its own commit does not describe.

    This is also what keeps the checkpoint hook out of the run rather than only
    detecting it afterwards: the hook commits because there is something to
    commit, and a session that starts clean gives it nothing to take. dementus
    reached the same workaround by hand -- commit the tree, then record -- and
    this makes it a precondition instead of a habit.

    It holds for EVERY live run, not only the ones that persist, and that is a
    choice rather than an oversight: `run_case` produces a `Recording` whichever
    way `HYPERSET_RECORD` is set, the `HYPERSET-STABILITY` line quotes its
    `sha=`, and a mode flag threaded down to mark which runs have to be honest
    about their commit is a flag somebody can pass wrongly. Nothing required per
    pull request reaches here -- `hyperset evals score` reads committed
    recordings and starts no run -- so a strict rule costs one clear refusal in
    the weekly job or a local session, and buys that no run reports a `sha=` it
    cannot stand behind.
    """
    changes = uncommitted_changes(root)
    if not changes:
        return
    hidden = len(changes) - DIRTY_PATHS_SHOWN
    shown = ", ".join(line.strip() for line in changes[:DIRTY_PATHS_SHOWN])
    rest = f", and {hidden} more" if hidden > 0 else ""
    raise TreeDirty(
        f"{len(changes)} uncommitted change(s) in {root} ({shown}{rest}); a recording names a "
        "commit, so the tree that produced it has to BE that commit -- commit the tree first"
    )


def refuse_an_unresolvable_commit(commit: str, *, root: Path = REPO_ROOT) -> None:
    """Refuse a commit a later reader could not look up.

    Reachability from a ref, not mere existence, and that difference is the
    defect: the checkpoint commits hy-r1i0 measured DO exist as objects, in the
    one shared repository that made them, and nowhere else. An object no ref
    reaches is one `gc` or one fresh clone away from being nothing at all, so a
    recording pinned to it cites a provenance only its author can resolve.
    """
    if not commit.strip():
        raise CommitUnresolvable(
            "this recording pinned no commit at all; an empty field must not read as a commit "
            "that resolved"
        )
    if _git("cat-file", "-e", f"{commit}^{{commit}}", root=root).returncode != 0:
        raise CommitUnresolvable(
            f"{commit} is not a commit in {root}; a recording naming an object nobody has cannot "
            "be checked against the code that produced it"
        )
    containing = _git(
        "for-each-ref", "--contains", commit, "--count=1", "--format=%(refname)", root=root
    )
    if containing.returncode != 0 or not containing.stdout.strip():
        raise CommitUnresolvable(
            f"{commit} is reachable from no ref in {root} -- a dangling object that would not "
            "survive a fresh clone or a gc, so nobody but this checkout could resolve what tree "
            "produced this evidence"
        )


DEFAULT_BRANCH_REF = "refs/remotes/origin/main"
"""Where the LOCAL opinion of the default branch is read from. A tracking ref is
a stored measurement of a moving reference, so nothing below treats it as
current on its own -- see `default_branch_sha` on the guard."""

_NAMES_AN_OBJECT = re.compile(r"[0-9a-fA-F]{7,40}")
"""What a measurement of a moving reference is allowed to look like (hy-eowf).

Deliberately a SHAPE test and not `rev-parse`: resolution is what admitted a
branch name. Whether a name resolves, and to what, is a fact about the seat --
`origin/main` and `refs/remotes/origin/HEAD` resolved and compared equal to
themselves on critic's seat and on this one, while plain `main` resolved to a
stale local branch here (0734f26 against origin/main at 6ce4b3f) and refused,
so the same input earned opposite verdicts from two checkouts of one repository.
A guard whose answer moves with the seat cannot certify anything about the
world.

Seven is git's own floor for an abbreviation it will print (`--short`), and the
upper bound is the width `rev-parse` returns. Below that, hex short enough to
collide is also hex short enough to be a branch name."""


def observe_the_default_branch(
    root: Path = REPO_ROOT, *, remote: str = "origin", branch: str = "main"
) -> str:
    """Ask the remote where the default branch is NOW, over the network.

    Deliberately not called by the guard. A check that reaches the network
    decides differently depending on when it ran and whether a proxy was up,
    which is the property this module exists to refuse. So the measurement is
    taken by the caller, at a moment it chooses, and passed in as a value.
    """
    done = _git("ls-remote", "--exit-code", remote, f"refs/heads/{branch}", root=root)
    if done.returncode != 0 or not done.stdout.strip():
        raise HistoryIncomplete(
            f"nothing answered for refs/heads/{branch} on {remote} from {root} -- git said "
            f"{done.stderr.strip()!r}; a default branch nobody could look up is not a default "
            "branch this repository may draw conclusions about"
        )
    return done.stdout.split()[0]


def truncation_evidence(root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Every reason to think this repository's object store is incomplete.

    A LIST rather than a boolean, and positive evidence rather than a guess,
    because the three ways to hold a partial repository are configured
    separately and only one of them announces itself through
    `--is-shallow-repository`. Each entry names its own repair, since a caller
    told only "incomplete" has to go looking for which of the three it has.
    """
    found: list[str] = []
    if _git("rev-parse", "--is-shallow-repository", root=root).stdout.strip() == "true":
        found.append("this is a shallow clone -- repair with `git fetch --unshallow origin`")
    promisors = _git("config", "--get-regexp", r"remote\..*\.promisor", root=root)
    if promisors.returncode == 0 and promisors.stdout.strip():
        found.append(
            "a partial-clone filter is configured ("
            + ", ".join(line.split()[0] for line in promisors.stdout.splitlines() if line.strip())
            + ") -- repair with `git fetch --refetch origin`"
        )
    grafted = _git("rev-parse", "--git-path", "info/grafts", root=root).stdout.strip()
    if grafted and (root / grafted).exists():
        found.append(f"grafts are in effect ({grafted}) -- the history here is rewritten")
    replacements = _git(
        "for-each-ref", "--count=1", "--format=%(refname)", "refs/replace", root=root
    )
    if replacements.returncode == 0 and replacements.stdout.strip():
        found.append("replace refs are in effect -- objects here stand in for other objects")
    return tuple(found)


def _covers_every_branch(spec: str) -> bool:
    """Whether one configured refspec asks for every branch the remote has (hy-m78p).

    A trailing `*` was reading as "fetches everything" and does not mean it:
    `+refs/heads/feature/*:refs/remotes/origin/feature/*` is a wildcard and is
    still narrow, and a seat configured that way was told nothing at all. What
    makes a refspec total is an EMPTY prefix in front of the wildcard, so that is
    what this matches rather than the wildcard itself.
    """
    return spec.split(":", 1)[0].lstrip("+") in {"*", "refs/*", "refs/heads/*"}


def fetch_scope_evidence(root: Path = REPO_ROOT, *, remote: str = "origin") -> tuple[str, ...]:
    """Every reason to think this repository never asked for the object at all.

    Beside `truncation_evidence` and deliberately not merged into it, because the
    two name opposite repairs for absences that look identical. Truncation is
    history CUT from refs this seat does track, and `git fetch --unshallow`
    restores it. A narrow refspec is refs this seat never tracked, which
    `--unshallow` does not touch: run it and the seat is still blind, now holding
    a receipt saying it was repaired.

    Measured on this rig rather than argued (hy-4nyg, hy-4dci): `crew/hyperion`
    unshallowed from 315 commits to 383, `truncation_evidence` there went EMPTY,
    and the head it could not resolve is still absent. One list would have
    described that seat correctly before the fetch and wrongly after it.

    Keyed to the configured refspec and NOT to how many remote refs are on disk.
    The count is a fossil of when the clone was made -- 166 refs here against 52
    in hyperion under identical configuration -- so it records a seat's age and
    says nothing about what it can see now.
    """
    configured = _git("config", "--get-all", f"remote.{remote}.fetch", root=root)
    specs = [line.strip() for line in configured.stdout.splitlines() if line.strip()]
    if not specs:
        return (
            f"no fetch refspec is configured for `{remote}` here, so this repository tracks no "
            f"branch automatically and an object arrives only when it is named -- `git fetch "
            f"{remote} <sha>`",
        )
    if any(_covers_every_branch(spec) for spec in specs):
        return ()
    named = ", ".join(spec.split(":", 1)[0].lstrip("+") for spec in specs)
    return (
        f"`{remote}` is fetched here through a refspec that covers part of the namespace "
        f"({named}), so every other "
        f"ref was never fetched and a commit only they reach was never asked for -- repair by "
        f"naming it, `git fetch {remote} <sha>` or `git fetch {remote} refs/pull/<n>/head`, and "
        f"NOT with `--unshallow`, which repairs a different disease",
    )


def _suspected_truncation(root: Path) -> str:
    """What to tell someone whose repository could not answer, in their words.

    A hint on a refusal rather than a check of its own, because
    `--is-shallow-repository` is necessary and not sufficient: a partial-clone
    filter and a graft truncate history identically and both report false. When
    `truncation_evidence` recognises the case the caller gets the exact repair;
    when it recognises nothing the refusal still stands, because the primitive
    that raised it -- an empty merge-base -- sees all three modes.
    """
    found = truncation_evidence(root)
    if found:
        return " Evidence of an incomplete repository here: " + "; ".join(found) + "."
    return (
        " Nothing here reports itself as truncated, so this may be two genuinely"
        " unrelated histories -- but in a repository whose refs came from one origin"
        " that is the less likely of the two, and neither reading supports an answer."
    )


def refuse_a_commit_off_the_default_branch(
    commit: str,
    *,
    root: Path = REPO_ROOT,
    default_branch_ref: str = DEFAULT_BRANCH_REF,
    default_branch_sha: str | None = None,
) -> None:
    """Refuse a commit the default branch does not contain (hy-narn).

    Strictly stronger than `refuse_an_unresolvable_commit`, which asks only
    whether SOME ref reaches the commit. Some ref includes a crew branch nobody
    has merged and a branch about to be deleted, so a recording can satisfy that
    rule today and cite a dangling object the week its branch is cleaned up. A
    commit the default branch contains is one every clone of the repository can
    resolve for as long as the repository exists, which is the claim
    `docs/development/benchmark.md` actually makes about `git_commit`.

    TWO PRECONDITIONS, each of which produces a refusal naming itself rather
    than a verdict, and then ONE PRIMITIVE READ THREE WAYS (hy-jkqi):

    1. the object resolves HERE, and an ABSENCE in a repository that is either
       truncated or narrowly fetched is reported as this repository's ignorance
       rather than as a fact about the commit -- "nobody has this object" is a
       claim about the world, and a partial seat is not entitled to make it. The
       two evidence lists stay separate because their repairs are not
       interchangeable. A DANGLING object is the opposite case and keeps its own
       words: the seat holds it, so nothing about a partial repository softens
       the finding;
    2. the default branch resolves HERE -- an unfetched or differently-named
       default branch must not read as "your commit is not on it".

    Then `--is-ancestor`, whose exit code is not the answer:

    - YES is always accepted. Truncation can hide a path; it cannot fabricate
      one, so this direction needs no precondition at all. It also survives a
      STALE `default_branch_ref`, because a commit an older default branch
      contained a newer one still contains -- unless the branch was rewritten,
      which is a different accident with its own alarm.
    - NO with an EMPTY merge-base is refused as suspected truncation. Both
      commits resolve and git can still find no common ancestor, which in a
      repository whose refs came from one origin means history was cut away.
      One primitive covers all three cutting modes -- shallow clone, partial
      clone, graft -- where `--is-shallow-repository` sees only the first.
    - NO with a NON-EMPTY merge-base is a finding only once the tracking ref is
      shown to be current, which is what `default_branch_sha` is for. Pass what
      `observe_the_default_branch` (or `git ls-remote origin refs/heads/main`)
      said. Without it this refuses instead of answering, because the whole arm
      turns on how long ago the seat last fetched: the same clone, the same
      pin, nothing changed but staleness, flips between ACCEPTED and a
      confident "the default branch does not contain this". The repair for
      that state is `git fetch`, and reporting it as "land the commit or
      re-record" would send someone to re-record a commit that is already on
      main -- which is hy-narn's own loop.

    The asymmetry is the same one that governs truncation: a found path is a
    proof and needs no precondition, an absence is what a stale or partial view
    manufactures, so only the NO direction carries the discriminators.
    """
    # ABSENT and DANGLING are both `CommitUnresolvable` and only the first one is
    # this seat's opinion. An object the store does not hold may be one every
    # other clone holds, so a truncated seat must not call it missing; an object
    # the store DOES hold with no ref reaching it is the one commit whose
    # unreachability a partial repository cannot be wrong about in that
    # direction, and relabelling it would assert three falsehoods at once --
    # that the root cannot resolve it, that this seat lacks it, and that
    # somewhere else has it. Measured tonight in `crew/hyperion`: #164's own head
    # present, zero refs containing it, one `gc` from nothing, and `git fetch
    # --unshallow` would not have changed a thing (hy-narn, third bounce).
    if commit.strip() and _git("cat-file", "-e", f"{commit}^{{commit}}", root=root).returncode != 0:
        # BOTH lists, because a seat can lack an object for either reason and the
        # repairs are not interchangeable. Reporting only truncation prescribes
        # `--unshallow` to a seat whose refspec never asked for the ref, which
        # leaves it exactly as blind while looking repaired (hy-4nyg).
        evidence = truncation_evidence(root) + fetch_scope_evidence(root)
        if evidence:
            raise HistoryIncomplete(
                f"{root} does not hold {commit}, and it is not entitled to conclude anything from "
                "that: " + "; ".join(evidence) + ". An object this seat does not have may be one "
                "every other clone has -- resolvability is relative to a repository, and reporting "
                "a local absence as a global fact is the defect this guard exists to refuse"
            )
    refuse_an_unresolvable_commit(commit, root=root)

    target = _git("rev-parse", "--verify", "--quiet", f"{default_branch_ref}^{{commit}}", root=root)
    if target.returncode != 0 or not target.stdout.strip():
        raise HistoryIncomplete(
            f"{default_branch_ref} does not resolve in {root}, so nothing here knows what the "
            f"default branch contains; fetch it before asking whether {commit} is on it. Refusing "
            "rather than answering: reporting this as 'not on the default branch' is the same "
            "false a shallow clone produces, one level up"
        )

    if _git("merge-base", "--is-ancestor", commit, default_branch_ref, root=root).returncode == 0:
        return

    base = _git("merge-base", commit, default_branch_ref, root=root)
    if base.returncode != 0 or not base.stdout.strip():
        raise HistoryIncomplete(
            f"{commit} and {default_branch_ref} have no common ancestor in {root}, so this "
            "repository cannot tell an off-branch commit from one whose history was truncated "
            "away, and it refuses rather than answering. Measured, not imagined: exactly this "
            "state reported NOT-AN-ANCESTOR for a commit 124 commits deep in main, and a P1 "
            "defect was filed on the strength of it." + _suspected_truncation(root)
        )

    local = target.stdout.strip()
    if default_branch_sha is None:
        raise DefaultBranchUnverified(
            f"{commit} is not an ancestor of {default_branch_ref}, which this repository last saw "
            f"at {local} -- and a tracking ref is a stored measurement of a moving reference, so "
            "that is not enough to call it a finding. Measure the branch (`git ls-remote origin "
            "refs/heads/main`, or `observe_the_default_branch`) and pass it as default_branch_sha. "
            "Refusing rather than answering: the repair for a stale ref is `git fetch`, and this "
            "arm would otherwise tell you to re-record a commit that is already on main"
        )
    measured = default_branch_sha.strip()
    if not _NAMES_AN_OBJECT.fullmatch(measured):
        raise DefaultBranchUnverified(
            f"default_branch_sha={measured!r} does not name an object, and only an object name can "
            f"settle whether {default_branch_ref} is current. A revision expression is resolved "
            "HERE, against the very refs whose currency is in question, so passing a branch name "
            "-- or this ref itself -- makes the guard compare a stored measurement to itself and "
            "then report the answer as confirmed. Measure the branch (`git ls-remote origin "
            "refs/heads/main`, or `observe_the_default_branch`) and pass the sha it prints"
        )
    # Compared as a PREFIX, because the other side is 40 characters of `rev-parse`
    # output: an ABBREVIATED sha of this very commit compares unequal on equality and
    # earns a refusal naming a staleness that is not there (hy-htov). A prefix test
    # reaches that without a subprocess, so it also costs a truncated seat nothing --
    # an abbreviation is checked against the ref this repository holds, never against
    # an object it may be missing.
    if not local.startswith(measured.lower()):
        raise DefaultBranchUnverified(
            f"{default_branch_ref} is at {local} in {root}, and the branch was measured at "
            f"{measured} -- two different commits, and nothing here establishes which of them is "
            f"current, so it refuses rather than answering whether {commit} landed in between. If "
            "the ref is behind, `git fetch origin` settles it; if the measurement is stale, "
            "measure it again. Not by re-recording either way"
        )

    raise CommitOffDefaultBranch(
        f"{commit} is not an ancestor of {default_branch_ref} ({local}, confirmed current) in "
        f"{root} -- the two histories do "
        "connect, and some ref reaches this commit, but the default branch does not, so the day "
        "that ref is deleted this recording cites an object no clone can resolve. Land the commit "
        "or re-record at one that is on it"
    )


@dataclass(frozen=True)
class RecordingSession:
    """The commit one recording session is pinned to, and the tree it runs in.

    Frozen, and the commit a field rather than a method, for the reason
    `RunPins` is frozen: a provenance the run can re-read is a provenance the
    run can change, which is precisely what happened.
    """

    root: Path
    commit: str
    run_id: str
    """What tells this session's recordings from another session's (hy-qc4u).

    NOTHING ELSE IN THIS FILE CAN DO IT. Two sessions of one case at one tree --
    the exact pair #25's close condition requires compared -- share a commit by
    construction, and share every pin by construction too, because
    `StabilityReport` refuses a pin drift between repetitions. Every identifier
    the recorder already had is constant across the two runs that must coexist,
    so the discriminator cannot be derived from provenance and has to be minted.

    Random, and carrying no meaning is the point rather than a shortcut. Wall
    time is unstable under clock skew between seats, churns every diff, and
    invites a reader to sort by it and call that "latest" -- a correctness claim
    it cannot support. A monotonic index needs a coordinator, races between
    seats, and means nothing outside the store that assigned it. This can only
    be used to tell two runs apart, which is all it is for.
    """

    @classmethod
    def establish(cls, root: Path = REPO_ROOT) -> RecordingSession:
        """Fix the commit this session records under, or refuse to start."""
        refuse_a_dirty_tree(root)
        head = head_commit(root)
        refuse_an_unresolvable_commit(head, root=root)
        return cls(root=root, commit=head, run_id=uuid4().hex)

    def reaffirm(self) -> str:
        """The session's commit, having checked that HEAD is still it."""
        head = head_commit(self.root)
        if head != self.commit:
            raise CommitMoved(
                f"this recording session pinned {self.commit} and HEAD is now {head}; something "
                "committed while the run was in flight -- an auto-checkpoint hook, a rebase in "
                "another worktree, an agent -- so every later recording would claim a commit the "
                "earlier ones did not run under. Stop what is committing, then re-record"
            )
        return self.commit


_SESSION: RecordingSession | None = None
"""The session this process established, if it has recorded anything yet.

Module state because the run it describes is a property of the process rather
than of any one caller: `run_case` is invoked once per case per arm from a
single pytest session, and a commit each of those observed for itself is the
defect."""


def recording_session(*, root: Path = REPO_ROOT) -> RecordingSession:
    """The session every recording this process writes belongs to.

    Established on the first call and only re-checked afterwards, which is the
    inversion hy-r1i0 asks for: what was a fresh observation per case is now one
    observation plus a comparison that can fail. The run id comes from the same
    object as the commit for the same reason -- a run id minted per case would
    make one session's cases look like several sessions, which is the defect
    this is here to detect rather than to manufacture.
    """
    global _SESSION
    if _SESSION is None or _SESSION.root != root:
        _SESSION = RecordingSession.establish(root)
        return _SESSION
    _SESSION.reaffirm()
    return _SESSION


def session_commit(*, root: Path = REPO_ROOT) -> str:
    """The commit every recording this process writes is pinned to."""
    return recording_session(root=root).commit
