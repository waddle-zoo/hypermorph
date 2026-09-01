#!/usr/bin/env python3
"""Comment on every bead a merge CLAIMS, and close none of them (hy-p4ji).

WHAT THIS EXISTS FOR. Merging a branch closes no bead. The only mechanism in
the town that closes one on a merge is `gt mq post-merge`, and it has a hard
precondition: an MR wisp must exist. A merge pressed with an empty queue --
which is every merge the Refinery performs directly -- satisfies nothing and
closes nothing. The bead stays open, `bd ready` keeps offering it, and the next
agent slung it reads a description the code has already outgrown.

WHY IT REPORTS AND NEVER CLOSES. Ruled by the Mayor on hy-p4ji, 2026-08-01, and
the reason is not a false-positive rate. A trailer is a CLAIM, not a completion:
a bead can legitimately span several commits with work still live, and no commit
can distinguish "fixed" from "attempted" -- a reverted or partial change reads
identically. So this step never closes, never reopens, and never changes a
status. It leaves a comment where the next agent is certain to look.

WHERE IT RUNS. In the Refinery, post-merge. The Refinery is the only seat that
knows the MERGED RANGE, and the range is what catches a bead whose fix landed
inside another bead's branch -- the range carries it, the merge subject does
not. It also fires at the instant the drift is created, so a report is one merge
wide and gets read, where a periodic digest of the same fifteen beads becomes
wallpaper.

TWO SIGNALS, AND NEITHER DOMINATES. Measured on origin/main at 58270709:

    commits carrying a `Completes-Bead:` body trailer     28 of 501

A body-trailer-only reader is blind on 94% of landings, so this also reads the
house convention -- the parenthesised group ending a commit subject. Open beads
claimed at that main, by signal:

    subject trailer only   hy-1jxy hy-c3dl hy-fc01 hy-so04 hy-ytp1
    body trailer only      hy-9xv1
    both                   hy-eowf hy-m78p hy-rt4v

The comment names which signal fired, because they are different strengths of
claim: `Completes-Bead:` is an explicit declaration, a subject parenthetical is
convention. Since nothing is ever closed, a weak claim costs one comment rather
than lost work, so the union is the right side to err on.

THE SUBJECT TRAILER IS A GROUP, NOT AN ID. Reading it as a single id is the
mistake that hides a third of the population:

    (hy-3187, hy-fc01)           (hy-a5ls, hy-so04)
    (hy-ytp1, hy-axg9, hy-sszk)  (hy-eowf, hy-m78p)

`\\((hy-[a-z0-9]{4,6})\\)$` matches none of these and reports nothing, in the
reassuring direction. The group is captured whole and split on commas.

A MID-SENTENCE ID IS A CITATION, NOT A CLAIM. 46586122 reads "...port the
hy-asip reference check... (hy-ou0o)": it claims hy-ou0o and merely cites
hy-asip, which is correctly open. Anchoring the group to the END of the subject
excludes it by construction. Measured against the whole-subject scan at
58270709, the two differ by exactly {hy-asip}.

BEAD IDS ARE NOT A FIXED WIDTH, SO THE SHAPE IS NOT GUESSED. Live ids include
`hy-bwo` (3), `hy-p4ji` (4), `hy-gh-206` and
`hy-md-0006-compat-strategy-recommendation`. Both `{4}` and `{4,6}` silently
drop real beads -- 19 open beads are three characters. Candidates are therefore
matched against the id set `bd` actually holds rather than against a pattern,
and a candidate that resolves to nothing is REPORTED, never dropped.

EXIT CODES
  0   nothing claimed, or every claimed bead is already closed
  3   REPORT: at least one open bead is claimed by this merge
  2   REFUSE: could not see -- unresolvable commit, unlanded commit, or no bd
  64  usage

2 and 3 are both non-zero and are not the same thing: 3 means the scan ran and
found something, 2 means it could not run. Collapsing them is how "I could not
look" comes to read as "I looked and it was fine".

USAGE
  scripts/report_claimed_beads.py <merge-sha> [--ref origin/main] [--apply]

Dry run is the default. The range is the merged side, `<sha>^1..<sha>`, so one
invocation speaks only for the branch that merge landed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import NoReturn

# The parenthesised group ending a commit subject, captured whole. Anchored to
# the end so a mid-sentence citation is not a claim; split on commas below.
SUBJECT_TRAILER = re.compile(r"\(([^()]*)\)\s*$")

# The explicit declaration, anchored both ends per line. This is the consumer's
# predicate at scripts/gh-to-gastown-sync.sh:556 read the other way round: that
# function answers "does this named bead have a completion commit", and this
# needs "which beads does this commit name". NOT git's `%(trailers)`: git parses
# only the LAST paragraph as trailers and the house convention ends every
# message with `Co-Authored-By: Claude`, so a `Completes-Bead:` line in its own
# paragraph is orphaned by construction -- invisible to the parser, plainly
# visible here. tests/unit/test_completes_bead_trailer_readers.py pins that
# divergence; measuring this population with the parser first found 18 beads and
# missed exactly the two known live failures.
COMPLETES_BEAD = re.compile(r"^Completes-Bead:[ \t]*(.+?)[ \t]*$", re.MULTILINE)

# Generous: hyphenated and any width. Never used to DECIDE what a bead is, only
# to propose candidates that are then resolved against bd's own id set.
CANDIDATE = re.compile(r"\bhy-[a-z0-9]+(?:-[a-z0-9]+)*\b")

SUBJECT_TRAILER_SIGNAL = "subject trailer"
COMPLETES_BEAD_SIGNAL = "Completes-Bead"

EXIT_OK = 0
EXIT_REFUSE = 2
EXIT_REPORT = 3
EXIT_USAGE = 64

# NUL separates fields and records, because `%B` (the full body) is author-
# controlled: a printable delimiter can be typed into a commit message, which
# injects a false field/record boundary and drops a real claim or attributes a
# bogus one. NUL cannot occur in a commit message, so no author content collides.
# The format uses git's `%x00` PLACEHOLDER, not a literal NUL -- a literal NUL in
# the argv would terminate the C string mid-argument. git expands %x00 to a real
# NUL in its OUTPUT, which is what the split below matches.
LOG_FORMAT = "%H%x00%s%x00%B%x00%x00"
FIELD_SEPARATOR = "\x00"
RECORD_SEPARATOR = "\x00\x00"

# Written into every comment so a re-run recognises its own work. Keyed to the
# claiming commit, not to the bead: a bead legitimately gets a second comment
# from a later merge that claims it again.
MARKER = "hy-p4ji-report"


class _UsageParser(argparse.ArgumentParser):
    """Exits EXIT_USAGE on a bad invocation, not argparse's default 2.

    2 is EXIT_REFUSE here -- "I could not look" -- so a plain usage error must
    not borrow it, or a typo reads as a blind scan. This is what makes the
    docstring's `64 usage` reachable.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


class Refusal(Exception):
    """Raised where the scan cannot see, never where it sees a problem."""


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _require_visible(sha: str, ref: str) -> str:
    """Resolve the merge commit and prove it is reachable from the ref.

    Both halves refuse rather than report. An absent object and a commit that
    never landed are the two ways this scan can be blind, and both fail toward
    "nothing claimed", which is the reassuring answer. `git cat-file -e` is the
    existence test rather than `rev-parse --verify`: the latter returns 0 and
    echoes the sha for a well-formed but absent 40-hex, because it validates
    spelling and not existence.
    """
    resolved = _git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise Refusal(f"cannot resolve {sha} to a commit")
    full = resolved.stdout.strip()
    if _git("cat-file", "-e", f"{full}^{{object}}").returncode != 0:
        raise Refusal(f"{full} does not resolve to an object in this repository")
    if _git("cat-file", "-e", f"{ref}^{{object}}").returncode != 0:
        raise Refusal(f"cannot resolve {ref}; fetch it before scanning")
    base = _git("merge-base", ref, full)
    if base.returncode != 0 or not base.stdout.strip():
        detail = base.stderr.strip() or "empty merge-base"
        raise Refusal(
            f"{full} and {ref} have no common ancestor ({detail}); "
            "a shallow checkout reads this way too, so this is not a finding"
        )
    if _git("merge-base", "--is-ancestor", full, ref).returncode != 0:
        raise Refusal(
            f"{full} is not reachable from {ref}; nothing is reported for work that has not landed"
        )
    return full


def merged_side(sha: str) -> str:
    """The range holding the commits this merge brought in.

    A merge commit's second-parent side is the branch; anything else is scanned
    as a single commit, which covers a fast-forward or a squash where there is
    no second parent to subtract.
    """
    parents = _git("rev-list", "--parents", "-n", "1", sha).stdout.split()
    return f"{sha}^1..{sha}" if len(parents) > 2 else f"{sha}^!"


def claims_in(range_spec: str) -> dict[str, dict[str, object]]:
    """Candidate bead id -> {commits: [(sha, subject)], signals: {...}}.

    Candidates, not beads. Resolution against bd happens in the caller, because
    a token that looks like an id and resolves to nothing must be reported
    rather than dropped.
    """
    log = _git("log", range_spec, f"--format={LOG_FORMAT}")
    if log.returncode != 0:
        raise Refusal(f"could not read {range_spec}: {log.stderr.strip()}")

    claims: dict[str, dict[str, object]] = {}

    def note(candidate: str, sha: str, subject: str, signal: str) -> None:
        entry = claims.setdefault(candidate, {"commits": [], "signals": set()})
        if (sha, subject) not in entry["commits"]:
            entry["commits"].append((sha, subject))
        entry["signals"].add(signal)

    for record in log.stdout.split(RECORD_SEPARATOR):
        if not record.strip():
            continue
        parts = record.lstrip("\n").split(FIELD_SEPARATOR)
        if len(parts) != 3:
            continue
        sha, subject, body = parts

        group = SUBJECT_TRAILER.search(subject)
        if group:
            for chunk in group.group(1).split(","):
                for candidate in CANDIDATE.findall(chunk.strip()):
                    note(candidate, sha, subject, SUBJECT_TRAILER_SIGNAL)

        for declared in COMPLETES_BEAD.findall(body):
            for candidate in CANDIDATE.findall(declared):
                note(candidate, sha, subject, COMPLETES_BEAD_SIGNAL)

    return claims


def known_beads() -> dict[str, str]:
    """Every bead id bd holds, mapped to its status.

    Taken from bd rather than assumed from a pattern: ids are 3 to 38
    characters and some carry internal hyphens, so any width-based regex drops
    real beads silently. `-n 0` because --limit defaults to 50.
    """
    listed = subprocess.run(
        ["bd", "list", "--all", "--json", "-n", "0", "--skip-labels"],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise Refusal(f"bd list failed: {listed.stderr.strip() or listed.stdout.strip()}")
    try:
        payload = json.loads(listed.stdout)
    except json.JSONDecodeError as broken:
        raise Refusal(f"bd list did not return JSON: {broken}") from broken
    # Two shapes from one command: `--status open --json` yields a bare list,
    # `--all --json` yields {"issues": [...], "meta": ..., "schema_version": ...}.
    # Accepting only one turns a flag change into an empty id set, and an empty
    # id set makes every claim read as "bd resolves no such bead".
    if isinstance(payload, dict):
        payload = payload.get("issues")
    if not isinstance(payload, list):
        raise Refusal("bd list returned neither a list nor an {'issues': [...]} object")
    beads = {b["id"]: b.get("status", "") for b in payload if isinstance(b, dict) and "id" in b}
    if not beads:
        raise Refusal("bd list returned no beads; refusing to call every claim unresolvable")
    return beads


def marker_line(sha: str) -> str:
    """The idempotency marker, in one place so the write and the check cannot drift.

    `comment_text` writes exactly this and `already_reported` matches exactly
    this, both from the claiming commit's sha. A second copy of the format, or a
    second source for the sha, is how a re-run either double-posts (checked sha
    != written sha) or silently skips.

    Keyed on the FULL 40-hex sha, never a prefix: two claiming commits that share
    a truncated prefix would collide, and the collision reads as "already
    reported" -- the same silent drop this tool exists to prevent, via the key.
    """
    return f"[{MARKER}:{sha}]"


def _comment_text(comment: object) -> str:
    """The body of a bd comment. Real `bd comments --json` names the field `text`."""
    return comment.get("text", "") if isinstance(comment, dict) else ""


def already_reported(bead: str, claiming_sha: str) -> bool:
    """True where a previous run left THIS commit's marker on this bead.

    Post-merge steps get re-run. Without this the same bead collects one
    identical comment per invocation, which is the fastest way to make the
    warning unreadable.

    The match is STRUCTURED, not a substring: a comment counts only if one of
    its lines EQUALS the bracketed marker. A substring scan over the serialized
    comment false-clears on any body that merely contains the token -- a quoted
    subject, a human paste, a related sha's marker -- and skips the report with
    no signal, which is the exact silent drop this whole step exists to prevent.
    """
    # `bd comments <id> --json`, NOT `bd comments list`: bd rejects the latter
    # verbatim ("there is no 'comments list'"), so the wrong subcommand exits
    # non-zero, this returns False, and every run re-posts. The argv shape is
    # pinned by test_the_idempotency_read_uses_bds_real_grammar.
    shown = subprocess.run(["bd", "comments", bead, "--json"], capture_output=True, text=True)
    if shown.returncode != 0:
        return False
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError:
        return False
    if isinstance(payload, dict):
        payload = payload.get("comments", [])
    if not isinstance(payload, list):
        return False
    wanted = marker_line(claiming_sha)
    return any(
        any(line.strip() == wanted for line in _comment_text(c).splitlines()) for c in payload
    )


def comment_text(bead: str, status: str, ref: str, entry: dict[str, object]) -> str:
    """The Mayor's wording, with the signal named.

    The last sentence is load-bearing and is not to be softened: without it the
    comment reads as an accusation of a missing close, and someone helpfully
    closes the bead.
    """
    commits = entry["commits"]
    signals = " + ".join(sorted(entry["signals"]))
    lines = []
    for sha, subject in commits:
        lines.append(f"Commit {sha} on {ref} names this bead in its {signals}:")
        lines.append(f'  "{subject}"')
    lines.append("")
    lines.append(
        f"This bead is still {status}. Nobody has verified whether the behaviour "
        f"is present at {ref} -- read the code before treating this description "
        f"as current. Not closed, on purpose: a trailer is a claim, not a "
        f"completion."
    )
    lines.append("")
    lines.append(marker_line(commits[0][0]))
    return "\n".join(lines)


def post(bead: str, text: str) -> tuple[bool, str]:
    """Add one comment. One call per bead, and never a status change."""
    added = subprocess.run(
        ["bd", "comment", bead, "--stdin"], input=text, capture_output=True, text=True
    )
    return added.returncode == 0, (added.stderr.strip() or added.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        prog="report_claimed_beads.py",
        description="Comment on every bead a merge claims. Closes nothing.",
    )
    parser.add_argument("merge_sha", help="the merge commit to speak for")
    parser.add_argument(
        "--ref",
        default="origin/main",
        help="the ref the merge must be reachable from (default: origin/main)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually comment; without it this prints what it would say",
    )
    args = parser.parse_args(argv)

    try:
        sha = _require_visible(args.merge_sha, args.ref)
        claims = claims_in(merged_side(sha))
        if not claims:
            print(f"no bead claimed in {merged_side(sha)}; nothing to report")
            return EXIT_OK
        # Only now is the id set needed: with no claim to resolve, an empty bd
        # list has nothing to misread as "no such bead", so it must not refuse.
        known = known_beads()
    except Refusal as refusal:
        print(f"REFUSE: {refusal}", file=sys.stderr)
        return EXIT_REFUSE

    reported = False
    for candidate in sorted(claims):
        entry = claims[candidate]
        signals = " + ".join(sorted(entry["signals"]))
        if candidate not in known:
            print(f"REPORT {candidate}: claimed via {signals} but bd resolves no such bead")
            reported = True
            continue
        status = known[candidate]
        if status == "closed":
            print(f"ok     {candidate}: already closed")
            continue
        if already_reported(candidate, entry["commits"][0][0]):
            print(f"ok     {candidate}: already carries this merge's report")
            continue
        text = comment_text(candidate, status, args.ref, entry)
        if not args.apply:
            print(f"would comment on {candidate} (status {status}, via {signals})")
            reported = True
            continue
        posted, detail = post(candidate, text)
        if posted:
            print(f"reported {candidate}: status {status}, via {signals}")
        else:
            print(f"REPORT {candidate}: bd comment failed: {detail}")
        reported = True

    return EXIT_REPORT if reported else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
