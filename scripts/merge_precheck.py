#!/usr/bin/env python3
"""The merge precheck: turn "one merger is the control" into an enforced control.

ADR 0014 established that this repo can carry NO branch protection (private repo
on a plan that 403s on protection and rulesets), so a red CI run still leaves
the merge button working and `gh pr merge` succeeds on a `UNSTABLE` PR. The
control is the merger verifying the exact head before merging -- but a control
that lives only in a human's habit is the hazard hy-4xps names: three green
merges in a row were an outcome, not a control. This script IS that control,
runnable and testable, so the merger runs one command and gets a hard pass/block
naming every reason, instead of eyeballing four separate `gh` outputs.

It refuses (exit 2) unless ALL of these hold at the PR's LIVE head:

  1. head != base (hy-yq4i): an MR pin equal to the target branch's own sha
     merges main into main -- a no-op that closes the PR and the bead with every
     signal green and nothing landed. A merge whose head is the base is refused.
  2. every CI check at the head is COMPLETED and SUCCESS (hy-4xps, hy-o12b):
     no pending, no failure, and at least one check -- an empty rollup means CI
     never ran for this head and green cannot be confirmed. `statusCheckRollup`
     is the head's checks by construction, so this reads freshness for free.
  3. a HYPERSET-VERDICT names the live head and rules MERGE (hy-6hwt, hy-z2vh):
     the verdict must NAME this exact head, so a stale verdict from before a
     re-push does not carry; a BOUNCE or HOLD at the head blocks (fail SAFE, not
     open); and no verdict at the head blocks. The reader accepts BOTH verdict
     spellings seen in the wild (below), so a real verdict is never misread as
     absent. A verdict VOIDED by a head move leaves a RESIDUE (`stale_residue`,
     hy-bclr): the 40-hex rule voids both dispositions symmetrically, but their
     consequences are not symmetric -- a stale MERGE fails SAFE (someone waits on a
     merge that does not happen, so it surfaces), while a stale BOUNCE fails OPEN
     (nothing refuses, nothing waits, so a bounced-then-rewritten head reads as
     ungraded and the rejection degrades into silence). The residue, folded into
     the 'no live MERGE' block, marks a stale-BOUNCE head a RE-GRADE of a rejected
     change (re-review still permitted -- fail open -- but no longer silent) and a
     stale-MERGE head a re-gate; it never blocks a fresh valid MERGE.
  4. when the MR names a bead, a commit in the range carries the EXACT
     `Completes-Bead: <id>` trailer (hy-jasw). A PR that completes a bead but
     omits the trailer lands green with the bead left OPEN -- a silent, inverted
     ledger loss the consumer (`scripts/gh-to-gastown-sync.sh`'s
     `completion_commit_shas`, `--grep="^Completes-Bead: ${bead_id}$"`) reads as
     "never closed". The predicate here reproduces that grep character for
     character; the bead id is taken explicitly (refinery passes it) or inferred
     from the branch, and a PR that names no bead is not held to a trailer.

HYPERSET-VERDICT, the two forms this reader accepts (hy-z2vh). A single-format
reader returned NOTHING for PRs #190/#192 -- which DID carry MERGE verdicts --
and "found nothing" looked identical to "has no verdict", failing silent toward
no-verdict. Both forms are parsed here so that can't recur:

  Form A (key=value, one line):
    HYPERSET-VERDICT v1 role=critic pr=189 sha=<40-hex> verdict=MERGE tree=<40-hex>
  Form B (header, then a markdown heading):
    HYPERSET-VERDICT v1

    ## VERDICT MERGE - PR #190 @ <40-hex> role=adversary tree=<40-hex>

To AUTHORIZE the unattended poll-merge (`scripts/refinery_poll.py`) a MERGE
verdict additionally carries `tree=<40-hex>`, the reviewed tree it was issued at
(it must still equal the current would-land tree), and `role=critic` or
`role=adversary`, its self-identified review role. Both go on the SAME operative
line the ruling is read from -- the banner line for Form A, the `## VERDICT`
heading for Form B -- since each verdict is read from one line. The poll lands only when TWO
DISTINCT roles (critic AND adversary) so authorize -- distinctness is by role, not
by forge login, because this town posts every review as one account (hy-irni). The
human-run `evaluate` precheck ignores `tree=`/`role=`; they gate only the
autonomous path.

A verdict counts only when it NAMES THE FULL 40-hex head oid, NAMES THIS PR, and
was posted by a configured trusted reviewer login (HYPERSET_CRITIC_LOGINS) -- the
only real authentication, since the `seat=`/`role=` fields are forgeable text in a
comment (role distinctness is discipline, as authorship has always been here).
Abbreviated shas, cross-PR verdicts, and verdicts from any other author name no
head here and cannot clear a merge. With no trusted login configured, the
precheck honors no verdict at all and blocks. The required CI checks
(HYPERSET_REQUIRED_CHECKS, default test/migrations/benchmark) must each be
present and green, so a required job that never ran is caught, not missed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

VERDICT_WORDS = {"MERGE", "BOUNCE", "HOLD"}
# The CI jobs that MUST have run and passed at the head. An empty rollup, or a
# rollup missing one of these, cannot confirm the suite ran -- a required job
# that never fired (path filter, removed job) is invisible otherwise (hy-o12b).
# Overridable via HYPERSET_REQUIRED_CHECKS (comma-separated) for other repos.
DEFAULT_REQUIRED_CHECKS = ("test", "migrations", "benchmark")
# The comment-author logins whose HYPERSET-VERDICT counts. The ONLY real
# authentication of a verdict is the GitHub identity that posted it -- the
# `seat=` field is convention, forgeable in a comment body. Without this set,
# ANY commenter (including the PR author) could self-issue a MERGE, so the
# precheck refuses to honor a verdict when no trusted author is configured.
CRITIC_LOGINS_ENV = "HYPERSET_CRITIC_LOGINS"


# The verdict's structured `constraints=` field carries the conditions that bind
# a merge or a close, so a consumer HONORS them mechanically instead of a human
# reading them out of prose (hy-sofx). Two kinds, and they clear differently:
#
#   merge_after  -- MERGE-blocking. `ref` is a PR (`#221`): the merge must not
#                   proceed until that PR has MERGED. It clears when the forge
#                   says merged, checked here, so a "MERGE once #221 lands"
#                   cannot read as clean while #221 is open.
#   do_not_close -- CLOSE-blocking. `ref` is a bead: it must not be closed on
#                   THIS PR. It is not a merge condition, so it clears by the
#                   merger ACKNOWLEDGING it (committing to honor it at close),
#                   which is the point -- the merger is required to acknowledge
#                   each one rather than to notice it. The bead-close step
#                   (refinery) then reads the same structured field.
#
# Anything else -- an unknown kind, a token missing its `:` or its ref -- is an
# `unknown` constraint that NEVER clears and always blocks: a condition a
# consumer cannot read is one it cannot honor, and merging past it is the exact
# silent pass this field exists to remove. Fail toward blocking.
MERGE_BLOCKING_KINDS = frozenset({"merge_after"})
CLOSE_BLOCKING_KINDS = frozenset({"do_not_close"})
KNOWN_CONSTRAINT_KINDS = MERGE_BLOCKING_KINDS | CLOSE_BLOCKING_KINDS
MERGE_ACK_ENV = "HYPERSET_MERGE_ACK"

# A known blocker is self-identifying: `<kind>:<ref>`. This matches one wherever
# it sits on a verdict line, case-insensitively, so it cannot vanish because a
# human wrote a bare space instead of a ';', capitalised the kind, or placed it
# after another `key=value` field. `(?<![\w:])` keeps it from firing inside a
# longer word. The ref charset is a TIGHT positive set -- `#`, word chars, `-`,
# what a PR (`#221`) or bead (`hy-z8dd`) ref is made of -- so ANY other character
# ends the ref. A separator this reader never enumerated (`|`, `&`, `/`, ...) thus
# cannot bury a following `merge_after:`/`do_not_close:` inside the first ref: it
# terminates the ref, and the next kind is matched on its own. An unusual ref
# character over-splits into an extra `unknown` token that BLOCKS -- fail safe,
# never a silent pass.
_KNOWN_KIND_TOKEN = re.compile(
    r"(?<![\w:])(" + "|".join(sorted(KNOWN_CONSTRAINT_KINDS)) + r"):([#\w-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Constraint:
    kind: str  # merge_after | do_not_close | unknown
    ref: str  # the PR (#221) or bead (hy-z8dd) it references; "" when unreadable
    raw: str  # the token exactly as written, the key an acknowledgement names


def parse_constraints(raw: str | None) -> tuple[Constraint, ...]:
    """The constraints a verdict's `constraints=` field carries (hy-sofx).

    `constraints=<kind>:<ref>[;<kind>:<ref>...]`, whitespace-free so it is one
    token on the verdict line; `constraints=none`, an empty value, or an absent
    field is no constraint. A token missing its `:`, carrying an empty ref, or
    naming a kind this gate does not know becomes an `unknown` constraint rather
    than being dropped -- a silently dropped constraint is the failure this field
    removes, so an unreadable one is preserved and blocks downstream.
    """
    if not raw or raw.strip().casefold() == "none":
        return ()
    constraints: list[Constraint] = []
    # ';' AND ',' both separate: `_acknowledged_constraints` accepts either, and a
    # ref that swallowed the next kind (`do_not_close:hy-z8dd,merge_after:#221` read
    # as one token) would bury a merge-blocker inside a close-blocker's ref -- the
    # silent pass this field removes. Split on both, so neither hides the other.
    for token in re.split(r"[;,]", raw):
        token = token.strip()
        if not token:
            continue
        kind, separator, ref = token.partition(":")
        kind, ref = kind.strip().casefold(), ref.strip()
        # A ':' remaining IN the ref means a second `kind:ref` was glued on with no
        # delimiter (`do_not_close:hy-z8ddmerge_after:#221`), so the second kind is
        # hidden in this ref: unreadable, so `unknown` and blocking, never an
        # ackable close-blocker that would clear the hidden merge-blocker with it.
        if not separator or not ref or ":" in ref or kind not in KNOWN_CONSTRAINT_KINDS:
            constraints.append(Constraint("unknown", ref, token))
        else:
            constraints.append(Constraint(kind, ref, token))
    return tuple(constraints)


def _line_constraints(text: str) -> tuple[Constraint, ...]:
    """Every constraint a verdict line carries, fail-closed against silent drops.

    Two channels, unioned and de-duplicated by raw token text:

    1. The explicit `constraints=` field -- a ';'-separated run, whitespace after
       '=' tolerated. This is where an UNKNOWN kind (`frobnicate:x`) is recognised
       and preserved as a blocking `unknown`; a lone word or a URL, which naming
       no known kind belongs to no channel, is left as prose.
    2. A whole-line scan for the KNOWN kinds (`merge_after`, `do_not_close`),
       case-insensitively, wherever they sit. A known blocker is self-identifying
       (`<kind>:<ref>`), so it can never vanish because a human wrote a bare space
       instead of a ';', capitalised the kind, or placed it after another field --
       the three fail-open holes a positional-only reader had. This is the
       security-critical channel: a merge/close blocker must never silently pass.

    Both forms feed the same reader, so a constraint on the heading line reaches
    parity between them. A constraint a critic writes on a SEPARATE line from the
    verdict banner is out of scope -- the field is defined as one line -- and,
    being unread, does not clear anything either; it is not a silent pass.
    """
    constraints: list[Constraint] = []
    seen: set[str] = set()

    def add(constraint: Constraint) -> None:
        if constraint.raw not in seen:
            seen.add(constraint.raw)
            constraints.append(constraint)

    field = re.search(r"constraints=\s*(\S+(?:\s*;\s*\S+)*)", text)
    if field:
        for constraint in parse_constraints(field.group(1)):
            add(constraint)
    for match in _KNOWN_KIND_TOKEN.finditer(text):
        # A ref the tight charset stopped right before a ':' is a glued second
        # token (`do_not_close:hy-z8ddmerge_after:#221`): its kind was absorbed
        # into this ref. Read it as `unknown` so it blocks, rather than as a
        # clean ackable close-blocker that would clear the hidden merge_after.
        if text[match.end() : match.end() + 1] == ":":
            add(Constraint("unknown", "", match.group(0)))
        else:
            add(Constraint(match.group(1).casefold(), match.group(2), match.group(0)))
    return tuple(constraints)


def _tree_pin(text: str) -> str | None:
    """The reviewed tree oid a verdict pins via `tree=<40-hex>`, lowercased.

    hy-5hgh / hy-8b6c: an autonomous merge lands ONLY the tree the dual-model
    verdicts were reviewed at, so the verdict must carry that tree. Matched by the
    literal `tree=` -- a `tree_id=` or `subtree=` does not match, the lookbehind
    rejects a word character before it -- and only a FULL 40-hex oid counts. A
    verdict with no pin authorizes no unattended merge (the caller fails closed).
    """
    match = re.search(r"(?<![\w])tree=([0-9a-fA-F]{40})\b", text)
    return match.group(1).lower() if match else None


# The review roles whose DISTINCT presence authorizes an unattended merge
# (hy-irni). Autonomous-merge distinctness cannot key off the forge LOGIN: this
# town posts every review comment as ONE GitHub account, so a distinct-login count
# is always 1 and nothing ever auto-lands. A verdict instead self-identifies its
# role (`role=critic` / `role=adversary`), and the poll counts DISTINCT roles from
# this set. This is DISCIPLINE, not cryptographic authorship -- the same reality
# the `seat=` field already lived under (ADR 0014: no forge authorship to key on)
# -- and it keeps the checkable parts checkable: the reviewed-tree `tree=` pin, CI,
# MERGEABLE, fail-closed. Only a role in THIS set counts, so a `role=critic` plus a
# `role=anything-else` cannot pass as two; reaching two means critic AND adversary.
MERGE_APPROVAL_ROLES = frozenset({"critic", "adversary"})


def _role(text: str) -> str | None:
    """The review role a verdict self-identifies via `role=<name>`, lowercased.

    Matched by the literal `role=` -- the lookbehind rejects a word character
    before it, so a `myrole=`/`subrole=` does not match -- and read from the
    code-stripped line like the other fields. A verdict with no `role=` returns
    None and authorizes nothing (the caller counts only roles in
    `MERGE_APPROVAL_ROLES`), so two untagged comments cannot pass as two.
    """
    match = re.search(r"(?<![\w])role=([A-Za-z][A-Za-z0-9_-]*)", text)
    return match.group(1).lower() if match else None


@dataclass(frozen=True)
class Verdict:
    verdict: str  # MERGE | BOUNCE | HOLD | (other, ignored)
    sha: str | None  # the FULL 40-hex head oid it names, lowercased
    pr: str | None  # the PR number it names, as a string, if any
    seat: str | None  # the seat= field (advisory; not authentication)
    author: str | None  # the comment-author login (the real authenticator)
    form: str  # "A" or "B", for diagnostics
    constraints: tuple[Constraint, ...] = ()  # the merge/close conditions it binds
    tree: str | None = None  # the reviewed tree oid it pins (`tree=`), lowercased
    role: str | None = None  # the review role it self-identifies (`role=`), lowercased
    comment: str | None = None  # the forge comment it came from, so one comment counts once


def parse_verdicts(
    body: str, author: str | None = None, comment: str | None = None
) -> list[Verdict]:
    """Every HYPERSET-VERDICT in one comment body, across both spellings.

    A comment usually carries one, but a body pasting several is handled. Lines
    that carry the `HYPERSET-VERDICT` banner but no `verdict=` field (Form B's
    banner line) yield nothing here -- Form B's ruling is on its `## VERDICT`
    line, matched separately -- so the two passes never double-count one verdict.
    Only a FULL 40-hex sha is captured: an abbreviated sha is under-specified and
    an adversary who chooses the head can grind a short prefix, so a verdict that
    names only a short sha names no head here (fail safe -> block).

    Fenced blocks, inline-code spans, and blockquote lines are stripped first, so
    a verdict that is merely QUOTED (a reminder of the format, an example) is not
    read as an operative ruling -- only a verdict written as running text counts.
    """
    # ponytail: strips closed fenced blocks and blockquotes so a QUOTED verdict is
    # not read as operative. The fence pattern is LINE-ANCHORED (`^[ \t]*` before
    # the marker): only a real fenced block -- a fence marker beginning a line --
    # is removed, so a mid-line inline `` ```token``` `` on an operative verdict
    # line survives into the raw line the constraint read trusts (it would else be
    # erased before that read). An UNTERMINATED fence, a 4-space-indented code
    # block, and a lazy blockquote continuation (a `>`-quoted block whose middle
    # line drops the `>`) all still parse as operative -- each requires a TRUSTED
    # critic to post malformed markdown, and fails toward "verdict text a human
    # sees as code" read as a ruling, never an unauthenticated bypass, so they are
    # left. Close them too only if a real case appears.
    body = re.sub(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", " ", body)
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    out: list[Verdict] = []
    for raw_line in lines:
        # Inline code disarms the BANNER (a quoted `HYPERSET-VERDICT ...` is not a
        # ruling), so detection reads the stripped line. Constraints, though, are
        # read from the RAW line: a blocker a critic inline-coded on an otherwise
        # operative verdict line must still bind, never vanish into a code span.
        line = re.sub(r"`[^`\n]*`", " ", raw_line)
        # Form A: the banner line itself carries verdict=... sha=... pr=... seat=...
        vm = re.search(r"verdict=([A-Za-z]+)", line) if "HYPERSET-VERDICT" in line else None
        if vm:
            sm = re.search(r"sha=([0-9a-fA-F]{40})\b", line)
            pm = re.search(r"pr=#?(\d+)", line)
            seat = re.search(r"seat=(\S+)", line)
            out.append(
                Verdict(
                    vm.group(1).upper(),
                    sm.group(1).lower() if sm else None,
                    pm.group(1) if pm else None,
                    seat.group(1) if seat else None,
                    author,
                    "A",
                    _line_constraints(raw_line),
                    _tree_pin(line),
                    _role(line),
                    comment,
                )
            )
        # Form B: a `## VERDICT <WORD> ... PR #<n> ... @ <full-sha>` heading.
        bm = re.search(r"##\s*VERDICT\s+([A-Za-z]+)\b([^\n]*)", line)
        if bm:
            sm = re.search(r"\bsha=([0-9a-fA-F]{40})\b|@\s*([0-9a-fA-F]{40})\b", bm.group(2))
            pm = re.search(r"PR\s*#(\d+)", bm.group(2))
            out.append(
                Verdict(
                    bm.group(1).upper(),
                    (sm.group(1) or sm.group(2)).lower() if sm else None,
                    pm.group(1) if pm else None,
                    None,
                    author,
                    "B",
                    _line_constraints(raw_line),
                    _tree_pin(bm.group(2)),
                    _role(bm.group(2)),
                    comment,
                )
            )
    return out


def _names_head(verdict: Verdict, head_sha: str) -> bool:
    """True when the verdict names THIS head by FULL 40-hex equality.

    Prefix matching is unsafe: an attacker who controls the head can grind a
    commit whose oid shares a short prefix with an old, honestly-issued verdict,
    re-targeting it. Only a full-oid equality names the head; anything shorter or
    absent names no head, so it cannot clear a merge.
    """
    return bool(verdict.sha) and len(verdict.sha) == 40 and head_sha.lower() == verdict.sha


def has_completion_trailer(commit_messages: list[str], bead_id: str) -> bool:
    """True when some commit in the range carries the EXACT completion trailer.

    The predicate is `^Completes-Bead: <bead_id>$` anchored both ends, matching
    the consumer at scripts/gh-to-gastown-sync.sh: a passing mention of another
    bead, the id as a substring, or a trailer for a different bead is not
    completion proof. Missing it is a silent, inverted ledger loss (hy-jasw): the
    PR lands, the bead stays OPEN, and the next agent redoes the work -- so the
    absence must block the merge, not wait for a reviewer to notice.
    """
    pattern = re.compile(rf"^Completes-Bead: {re.escape(bead_id)}$", re.MULTILINE)
    return any(pattern.search(message or "") for message in commit_messages)


_COMPLETION_TRAILER = re.compile(r"^Completes-Bead: (\S+)$", re.MULTILINE)


def completion_beads(commit_messages: list[str] | None) -> list[str]:
    """Every DISTINCT bead id a `Completes-Bead:` trailer names, in first-seen order.

    Reads the SAME anchored trailer `has_completion_trailer` checks, so the gate
    and this extractor cannot disagree about what a trailer is. The autonomous
    reconciler (`scripts/refinery_poll.py`) uses it to decide WHICH bead a merged
    PR closes and fails safe on the two off-nominal counts: zero (no trailer, close
    nothing) and more than one (ambiguous, close nothing) -- it never guesses which
    of several beads to close, so it can never close the wrong one (hy-n0ge).
    """
    seen: list[str] = []
    for message in commit_messages or []:
        for match in _COMPLETION_TRAILER.finditer(message or ""):
            bead = match.group(1)
            if bead not in seen:
                seen.append(bead)
    return seen


# The bd short-id a Gas Town crew branch names: `crew/<bead-id>-<slug>`. A bead
# id is `hy-gh-<number>` (a mirrored GitHub issue) or `hy-<short id>` (bd's own
# base32-ish id, seen from two to eight characters in this repo -- hy-it, hy-2d2,
# hy-jasw). It is bounded by a path separator or a hyphen so a word of the
# branch's description is never taken for part of the id, and NO length is
# assumed: an earlier four-char pattern silently skipped every two- and
# three-char bead (~15% of them), reopening for those exactly the loss this
# guard closes, so the id runs to the first separator (`[a-z0-9]+`) whatever its
# length. A crew branch's first path token IS the bead id, so matching toward
# requiring a trailer is the safe direction for a merge gate: an over-match would
# at worst require a trailer that is easily added, never let a bead land open. If
# bd's id format changes, this pattern and its test change with it.
#
# The id is terminated by any NON-id character via a negative lookahead, not by an
# enumerated `-|/|$` set: an off-spec boundary a branch might carry -- `hy-ab12_slug`
# (underscore), `hy-ab12.slug` (dot) -- would otherwise fall off the `-|/|$` set and
# return `None`, a silent loss (the bead lands un-held). `re.IGNORECASE` similarly
# resolves an upper/mixed-case branch to the bead it names; the match is casefolded
# to the canonical lowercase id (`bead_id_from_branch`). No current lowercase-alnum-
# hyphen id hits either path, so this is a latent-loss hardening, not a live bug.
_BRANCH_BEAD_ID = re.compile(r"(?:^|/)(hy-(?:gh-[0-9]+|[a-z0-9]+))(?![0-9a-z])", re.IGNORECASE)


def bead_id_from_branch(branch: str | None) -> str | None:
    """The bead id a crew branch names, or `None` when it names none.

    The trailer requirement must not depend on the merger REMEMBERING to pass the
    bead id (hy-jasw): a merger who forgets the trailer forgets the id too, and
    the silent, inverted ledger loss recurs. The branch is the one
    MACHINE-STRUCTURED, per-bead signal -- `crew/hy-jasw-...` -- so it is read
    here to make the requirement fire on its own.

    Deliberately NOT inferred from the PR TITLE or BODY: matching a bead id in
    human-written prose is the heuristic-over-prose error class hy-80xq removed --
    a body mentioning a dependency, an example, or a 'relates to' bead would
    false-require the wrong trailer.

    A branch with NO `hy-`-shaped segment at a path-segment start (a pure-infra
    `safety/bump-x` or `chore/deps`) yields `None`, and such a PR is NOT held to a
    trailer, so this never false-fails. PRECISELY, though: a `safety/`/`chore/`
    branch whose segment DOES read as a bead id -- `safety/hy-drate-config`, where
    `hy-drate` is `hy-`-shaped -- resolves to that id and IS held to a trailer. That
    is an OVER-require in the safe direction (a trailer is trivially added; a bead is
    never let land open), not a miss. An unexpected id shape still yields `None`,
    falling back to the explicit path rather than blocking on a guessed id.
    """
    if not branch:
        return None
    match = _BRANCH_BEAD_ID.search(branch)
    # Casefold so an upper/mixed-case branch maps to the canonical lowercase bead id.
    return match.group(1).casefold() if match else None


def evaluate(
    *,
    head_sha: str,
    base_sha: str | None,
    checks: list[dict],
    verdicts: list[Verdict],
    pr_number: str | None = None,
    trusted_authors: set[str] | None = None,
    required_checks: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS,
    bead_id: str | None = None,
    commit_messages: list[str] | None = None,
    merged_prs: set[str] | None = None,
    acknowledged_constraints: set[str] | None = None,
) -> list[str]:
    """The reasons a merge must be BLOCKED. Empty list means clear to merge.

    Pure and injectable: `main` fetches the live PR and passes it here, and the
    tests pass crafted state, so every rule is exercised without a network.

    A verdict counts only if it (1) names the live head by full oid, (2) names
    THIS pr (when `pr_number` is given), and (3) was posted by a trusted author
    (when `trusted_authors` is given -- the load-bearing authentication). With no
    trusted authors configured, no verdict is honored: the merge blocks rather
    than trust a self-issued one. When `bead_id` is known, a commit in the range
    must carry the exact `Completes-Bead: <bead_id>` trailer or the merge blocks
    (hy-jasw), so the bead cannot land open.
    """
    reasons: list[str] = []
    if not head_sha:
        return ["no head sha for the PR"]
    if base_sha and head_sha.lower() == base_sha.lower():
        reasons.append(
            "head == base: this pin merges the target into itself (a no-op that lands nothing)"
        )

    if bead_id and not has_completion_trailer(commit_messages or [], bead_id):
        reasons.append(
            f"no commit carries the exact 'Completes-Bead: {bead_id}' trailer -- "
            "the bead would land OPEN"
        )

    present = {(c.get("name") or "").strip() for c in checks}
    for required in required_checks:
        if required not in present:
            reasons.append(f"required check {required!r} did not run at the head")
    if not checks:
        reasons.append("no CI checks reported at the head -- green cannot be confirmed")
    for check in checks:
        name = check.get("name") or "check"
        status = (check.get("status") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()
        if status != "COMPLETED":
            reasons.append(f"check {name!r} has not completed (status {status or 'MISSING'})")
        elif conclusion != "SUCCESS":
            reasons.append(f"check {name!r} is {conclusion or 'PENDING'}, not SUCCESS")

    if trusted_authors is None:
        reasons.append(
            f"no trusted reviewer identity configured ({CRITIC_LOGINS_ENV}); "
            "refusing to honor any verdict"
        )
    counted = _counted_verdicts(
        verdicts, head_sha=head_sha, pr_number=pr_number, trusted_authors=trusted_authors
    )
    blocking = [v for v in counted if v.verdict in {"BOUNCE", "HOLD"}]
    if blocking:
        reasons.append(
            f"a {blocking[0].verdict} verdict names the live head -- fail safe, do not merge"
        )
    elif trusted_authors is not None and not any(v.verdict == "MERGE" for v in counted):
        reason = (
            "no trusted HYPERSET-VERDICT MERGE names the live head "
            "(a stale, cross-PR, or untrusted verdict does not carry)"
        )
        # A voided verdict leaves a RESIDUE so 'no live verdict' is distinguishable
        # from 'no verdict at all' (hy-bclr): a stale BOUNCE marks this head a
        # re-grade of a rejected change (fail open, not silent), a stale MERGE marks
        # it a re-gate. Folded into THIS block reason -- never emitted on the clear
        # path -- so a stale BOUNCE never blocks a fresh valid MERGE.
        residue = stale_residue(
            verdicts, head_sha=head_sha, pr_number=pr_number, trusted_authors=trusted_authors
        )
        if residue:
            reason += " -- " + residue
        reasons.append(reason)

    # Every constraint the clearing MERGE verdict(s) carry must be honored, or the
    # merge blocks (hy-sofx): a condition named in a structured field must not read
    # as clean the way one buried in prose did.
    acknowledged = acknowledged_constraints or set()
    for verdict in counted:
        if verdict.verdict != "MERGE":
            continue
        for constraint in verdict.constraints:
            reason = _constraint_block_reason(
                constraint, merged_prs=merged_prs, acknowledged=acknowledged
            )
            if reason:
                reasons.append(reason)
    return reasons


def _counted_verdicts(
    verdicts: list[Verdict],
    *,
    head_sha: str,
    pr_number: str | None,
    trusted_authors: set[str] | None,
) -> list[Verdict]:
    """The verdicts that COUNT for this head: named by full oid, naming this PR
    (when known), and posted by a trusted author. With no trusted author
    configured, none count -- the same authentication `evaluate` rests on, and the
    selection `merge_constraints` reuses so the two cannot read a different set of
    verdicts."""
    if trusted_authors is None:
        return []
    return [
        v
        for v in verdicts
        if _names_head(v, head_sha)
        and (pr_number is None or v.pr == str(pr_number))
        and v.author in trusted_authors
    ]


def stale_verdicts(
    verdicts: list[Verdict],
    *,
    head_sha: str,
    pr_number: str | None,
    trusted_authors: set[str] | None,
) -> list[Verdict]:
    """The trusted verdicts for THIS PR that named an EARLIER head, not the live
    one (hy-bclr): full-oid, this-PR, trusted-author -- every test `_counted_verdicts`
    applies EXCEPT the head-naming one, which they fail because the head has since
    moved. These are the verdicts the 40-hex rule VOIDED by a rewrite. They
    authorize and block nothing (only a verdict at the live head does that); they
    exist so a voided verdict leaves a RESIDUE (`stale_residue`) rather than
    evaporating into silence."""
    if trusted_authors is None:
        return []
    live = head_sha.lower()
    return [
        v
        for v in verdicts
        if v.sha
        and len(v.sha) == 40
        and v.sha != live
        and (pr_number is None or v.pr == str(pr_number))
        and v.author in trusted_authors
    ]


def stale_residue(
    verdicts: list[Verdict],
    *,
    head_sha: str,
    pr_number: str | None,
    trusted_authors: set[str] | None,
) -> str | None:
    """The residue a VOIDED verdict leaves once its head has moved (hy-bclr), or
    `None` when this PR carries no stale trusted verdict.

    The 40-hex rule voids a verdict SYMMETRICALLY when the head moves, but the two
    dispositions are ASYMMETRIC in consequence, so the residue distinguishes them:

      * a stale BOUNCE fails OPEN -- the rejection stops naming the live head, so
        nothing refuses and nobody is waiting; a bounced-then-rewritten head that
        is NOT re-graded reads exactly like a never-reviewed one, and the rejection
        "degrades into silence" rather than into a hold. The residue survives the
        head move and says so: this head is a RE-GRADE of a rejected change, not a
        first review -- the rewrite did not withdraw the objection. Re-review is
        still permitted (fail open), but it is no longer silent.
      * a stale MERGE fails SAFE on its own -- it authorizes nothing at the live
        head, someone is waiting on a merge that does not happen, so it surfaces
        without help. The residue just names the re-gate need.

    A rejection outweighs a stale approval here: if both exist, the BOUNCE residue
    is reported, because "this was rejected, re-review it" is the fact that must not
    go silent. Returned as a NON-blocking note the callers fold into the existing
    'no live MERGE' block -- it never blocks a fresh valid MERGE (that would make a
    stale BOUNCE fail CLOSED, the opposite of the fix)."""
    stale = stale_verdicts(
        verdicts, head_sha=head_sha, pr_number=pr_number, trusted_authors=trusted_authors
    )
    bounces = [v for v in stale if v.verdict in {"BOUNCE", "HOLD"}]
    if bounces:
        latest = bounces[-1]
        old = latest.sha[:12] if latest.sha else "?"
        # A BOUNCE is a rejection; a HOLD is a withheld verdict. Both void when the
        # head moves, and both must leave a residue, but the noun matches the
        # disposition so a stale HOLD is not miscalled a rejection.
        disposition = "rejection" if latest.verdict == "BOUNCE" else "hold"
        return (
            f"a prior {latest.verdict} named an earlier head ({old}), not the live one -- "
            f"this head is a RE-GRADE of a change a previous {disposition} named (the rewrite did "
            f"not withdraw the {disposition}), not a first review; re-review it (fail OPEN toward "
            "re-grade, but the earlier verdict is not silently gone)"
        )
    merges = [v for v in stale if v.verdict == "MERGE"]
    if merges:
        old = merges[-1].sha[:12] if merges[-1].sha else "?"
        return (
            f"a prior MERGE named an earlier head ({old}); an approval does not carry across a "
            "head move -- re-gate this head (fail SAFE)"
        )
    return None


def _constraint_block_reason(
    constraint: Constraint, *, merged_prs: set[str] | None, acknowledged: set[str]
) -> str | None:
    """Why one constraint blocks the merge, or `None` when it is satisfied.

    A `merge_after` clears only when the forge says the referenced PR is merged
    (an unresolved state is unmerged here -- fail toward blocking). A
    `do_not_close` clears only when the merger acknowledged it. An `unknown`
    constraint never clears: a condition the gate cannot read is one it cannot
    honor, so merging past it is refused."""
    if constraint.kind in MERGE_BLOCKING_KINDS:
        pr_ref = constraint.ref.lstrip("#")
        if merged_prs is not None and pr_ref in merged_prs:
            return None
        return (
            f"the verdict blocks this merge until {constraint.ref} lands "
            f"({constraint.raw!r}), and it has not"
        )
    if constraint.kind in CLOSE_BLOCKING_KINDS:
        if constraint.raw in acknowledged:
            return None
        return (
            f"the verdict names a post-merge constraint ({constraint.raw!r}) the merger has not "
            f"acknowledged; set {MERGE_ACK_ENV} to it, so the bead is not closed past its grader"
        )
    return (
        f"the verdict carries a constraint this gate cannot read ({constraint.raw!r}); "
        "refusing to merge past a condition it cannot honor"
    )


def merge_constraints(
    verdicts: list[Verdict],
    *,
    head_sha: str,
    pr_number: str | None,
    trusted_authors: set[str] | None,
) -> tuple[Constraint, ...]:
    """Every constraint the clearing MERGE verdict(s) carry, for the caller that
    resolves `merge_after` references and surfaces the `do_not_close` ones."""
    return tuple(
        constraint
        for verdict in _counted_verdicts(
            verdicts, head_sha=head_sha, pr_number=pr_number, trusted_authors=trusted_authors
        )
        if verdict.verdict == "MERGE"
        for constraint in verdict.constraints
    )


def merge_authorizers(
    verdicts: list[Verdict],
    *,
    head_sha: str,
    pr_number: str | None,
    trusted_authors: set[str] | None,
    base_sha: str | None,
    describes,
) -> set[str]:
    """The DISTINCT review ROLES whose counted MERGE verdict PINS a reviewed tree
    that STILL equals the current would-land tree (hy-irni).

    The autonomous merger (`scripts/refinery_poll.py`) authorizes a land only when
    at least two of these exist -- a dual-model MERGE, critic AND adversary, AT the
    exact tree that would land now. Distinctness is keyed off the self-identified
    ROLE, not the forge login, because this town posts every review comment as one
    account, so a distinct-login count is always 1 and nothing would ever land. It
    folds these guarantees into one selection:

      * the head / PR / trust binding of `_counted_verdicts`, so a self-issued,
        stale, or cross-PR verdict is not counted;
      * the reviewed-tree pin (`tree=`), so a MERGE verdict carrying NO pin
        authorizes nothing -- an unpinned approval cannot clear the unattended
        path (fail-closed);
      * the #300 tree identity, checked through the injected `describes`
        (`gate.gate_describes_would_land`): a base advance that CHANGES the
        would-land tree matches no pin, so a tree nobody dual-model-reviewed --
        stale-green under ADR 0014's no-branch-protection -- cannot auto-land;
        while a base advance the head ALREADY contains (tree unchanged) still
        authorizes, because that is the very tree CI ran and the reviewers named;
      * a KNOWN role (`MERGE_APPROVAL_ROLES`), so `role=critic` plus a
        `role=whatever-else` cannot reach two -- two means critic AND adversary.

    A single COMMENT contributes at most one role: its qualifying verdicts must
    name exactly ONE known role, or the comment is discarded, so one comment
    claiming two roles cannot satisfy both (the anti-gaming guard). `describes` is
    injected so this module keeps no import of `gate`; the caller wires them.
    """
    if not (base_sha and head_sha):
        return set()
    roles_by_comment: dict[str, set[str]] = {}
    for verdict in _counted_verdicts(
        verdicts, head_sha=head_sha, pr_number=pr_number, trusted_authors=trusted_authors
    ):
        if (
            verdict.verdict == "MERGE"
            and verdict.tree
            and verdict.role in MERGE_APPROVAL_ROLES
            and describes(tree_id=verdict.tree, base=base_sha, head=head_sha)
        ):
            roles_by_comment.setdefault(verdict.comment, set()).add(verdict.role)
    # A comment that names exactly one known role contributes it; a comment naming
    # two (or, grouped under a missing id, a mix) contributes nothing -- it cannot
    # stand in for two reviewers.
    authorized: set[str] = set()
    for comment_roles in roles_by_comment.values():
        if len(comment_roles) == 1:
            authorized |= comment_roles
    return authorized


def _gh_json(args: list[str]) -> dict:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def _fetch(pr: str) -> dict:
    data = _gh_json(
        [
            "pr",
            "view",
            pr,
            "--json",
            "commits,baseRefOid,baseRefName,headRefName,statusCheckRollup,comments,state,mergeable",
        ]
    )
    commits = data.get("commits") or []
    head_sha = (commits[-1] if commits else {}).get("oid", "")
    # `or ""` (not a default) so a null body becomes empty, never the literal
    # "None". ponytail: matches GitHub's LF-normalized message; a CR-bearing
    # commit body could in theory pass here yet miss the sync's raw `git log`
    # grep -- narrow enough (commit messages are LF) to leave.
    commit_messages = [
        f"{c.get('messageHeadline') or ''}\n\n{c.get('messageBody') or ''}" for c in commits
    ]
    verdicts: list[Verdict] = []
    for index, comment in enumerate(data.get("comments") or []):
        author = (comment.get("author") or {}).get("login")
        # A stable per-comment identity so `merge_authorizers` can count one role
        # per COMMENT: a single comment claiming two roles must not pass as two.
        # The forge url is unique per comment; the index is a fallback.
        cid = comment.get("url") or f"comment-{index}"
        verdicts.extend(parse_verdicts(comment.get("body") or "", author=author, comment=cid))
    return {
        "head_sha": head_sha,
        "base_sha": data.get("baseRefOid"),
        "state": data.get("state"),
        "mergeable": data.get("mergeable"),
        "checks": data.get("statusCheckRollup") or [],
        "verdicts": verdicts,
        "commit_messages": commit_messages,
        "branch": data.get("headRefName"),
    }


def _trusted_authors() -> set[str] | None:
    raw = os.environ.get(CRITIC_LOGINS_ENV, "").strip()
    return {login.strip() for login in raw.split(",") if login.strip()} or None


def _required_checks() -> tuple[str, ...]:
    raw = os.environ.get("HYPERSET_REQUIRED_CHECKS", "").strip()
    return tuple(c.strip() for c in raw.split(",") if c.strip()) or DEFAULT_REQUIRED_CHECKS


def _acknowledged_constraints() -> set[str]:
    # Either separator, so a merger who reuses the field's `;` in the ack env is
    # not silently unmatched (which would block, a footgun) -- both split alike.
    raw = os.environ.get(MERGE_ACK_ENV, "").strip()
    return {token.strip() for token in re.split(r"[;,]", raw) if token.strip()}


def _merged_prs(constraints: tuple[Constraint, ...]) -> set[str]:
    """Which `merge_after` PR references the forge reports as MERGED. Queried
    only for the refs a constraint names, and a ref whose state cannot be read --
    a network or auth failure, an unknown PR -- is simply left ABSENT, so
    `evaluate` reads it as unmerged and blocks rather than the precheck crashing
    (hy-sofx). Fail toward blocking, in band, with a reason."""
    merged: set[str] = set()
    for constraint in constraints:
        if constraint.kind not in MERGE_BLOCKING_KINDS:
            continue
        pr_ref = constraint.ref.lstrip("#")
        if not pr_ref.isdigit():
            continue
        try:
            data = _gh_json(["pr", "view", pr_ref, "--json", "state"])
        except SystemExit:
            continue
        if (data.get("state") or "").upper() == "MERGED":
            merged.add(pr_ref)
    return merged


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print("usage: merge_precheck.py <pr-number> [bead-id]", file=sys.stderr)
        return 2
    pr = argv[1]
    # The bead the MR completes, so its Completes-Bead trailer is required
    # (hy-jasw). Taken explicitly when refinery passes it (argv or env), else
    # INFERRED from the branch so the requirement fires without the merger having
    # to remember the id. A PR that names no bead either way skips the check.
    explicit_bead = (
        argv[2] if len(argv) == 3 else (os.environ.get("HYPERSET_MR_BEAD", "").strip() or None)
    )
    live = _fetch(pr)
    if live.get("state") and live["state"] != "OPEN":
        print(f"BLOCK PR #{pr}: state is {live['state']}, not OPEN")
        return 2
    bead_id = explicit_bead or bead_id_from_branch(live.get("branch"))
    trusted = _trusted_authors()
    # The constraints the clearing MERGE verdict carries (hy-sofx): resolve every
    # `merge_after` reference against the forge, and read the merger's
    # acknowledgements, so `evaluate` can honor each structured condition.
    constraints = merge_constraints(
        live["verdicts"], head_sha=live["head_sha"], pr_number=pr, trusted_authors=trusted
    )
    reasons = evaluate(
        head_sha=live["head_sha"],
        base_sha=live["base_sha"],
        checks=live["checks"],
        verdicts=live["verdicts"],
        pr_number=pr,
        trusted_authors=trusted,
        required_checks=_required_checks(),
        bead_id=bead_id,
        commit_messages=live["commit_messages"],
        merged_prs=_merged_prs(constraints),
        acknowledged_constraints=_acknowledged_constraints(),
    )
    head = (live["head_sha"] or "")[:12]
    if reasons:
        print(f"BLOCK PR #{pr} @ {head}: not clear to merge")
        for reason in reasons:
            print(f"  - {reason}")
        return 2
    print(f"CLEAR PR #{pr} @ {head}: CI green, head != base, MERGE verdict names the live head")
    # Surfaced, not silent: an acknowledged close constraint still binds the
    # bead-close step (refinery), which reads this structured field, not prose.
    for constraint in constraints:
        if constraint.kind in CLOSE_BLOCKING_KINDS:
            print(f"  ! post-merge constraint acknowledged: {constraint.raw} -- honor it at close")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main(sys.argv))
