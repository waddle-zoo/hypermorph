#!/usr/bin/env bash
# hy-fy7d clause 3, computed rather than quoted: does main's merge-base-to-tip
# diff intersect the files this head changed?
#
# Empty intersection and a clean trial merge means merge it -- UNLESS a diverged
# side touched a fixture/recording directory, where a test may couple through
# data the intersection cannot see and the merged-tree suite is required (hy-1ryd,
# exit 5); a non-empty intersection means back to critic with it named. The
# companion at merge time
# is scripts/refcheck.sh, which answers the reference-level half (hy-asip) and
# is HANDED a merge base -- this is the thing that produces one.
#
#   exit 0  computed; intersection EMPTY, no data dir touched (clause 3 satisfied)
#   exit 5  computed; intersection EMPTY but a fixture/recording dir was touched
#           on a diverged side -- the merged-tree SUITE is required (hy-1ryd)
#   exit 4  computed; intersection NON-EMPTY        (finding: back to critic)
#   exit 3  computed; the histories do not meet     (no common ancestor)
#   exit 2  REFUSE -- could not look                (never an answer)
#   exit 64 usage error
#
# 2 and 3 are the numbers hy-fy7d PUBLISHED for those two arms, and a script
# that renumbers a snippet still being copied by hand would make the copies and
# the calls disagree. 0 was published too. 4 and 5 are this file's additions --
# 4 because the ruling's snippet ends at `comm` and never says what a hit exits,
# 5 because a path intersection cannot see coupling through data read at runtime
# (hy-1ryd). 1 is deliberately unused: an exit 1 from this file is the file
# failing, not an answer it gave.
#
# WHY THE rc=1 ARM IS SPLIT, which is the whole reason this is a script and not
# four lines in a directive. `git merge-base A B` returns rc=1 with empty stdout
# for a genuine orphan AND for a seat whose copy of the connecting history was
# cut -- refinery's depth-1 probe put both tips in a clone, kept cat-file -e
# passing on both, and got byte-identical output to the orphan case. rc=128 does
# not cover it: 128 fires when a TIP is absent, and there the tips are present
# and the ANCESTOR is gone. So rc=1 alone is not a finding.
#
# AND NOT `--is-shallow-repository`, WHICH IS THE OTHER HALF (hy-f1vw). The flag
# and the hazard come apart in both directions, measured on this rig
# 2026-08-01: shallow=true on the bare, refinery/rig and all six polecats, which
# hold every open head; shallow=false on crew/critic and crew/hyperion, which do
# not. Keying a refusal to the flag refuses forever in the seat where merges
# happen and stays quiet in the seats that are actually blind.
set -u

TRUE_ROOT_DEFAULT=b9cc0901d2cf280d85a2db079fbffd14ae867b03

usage() {
  cat >&2 <<'USAGE'
usage: clause3-intersection.sh <head> [<main-ref>] [--true-root <sha>]

  head       the PR head to weigh (any rev this checkout can resolve)
  main-ref   what it is being merged into; default origin/main
  --true-root
             the repository's genuine root commit, used ONLY to decide whether
             a shallow boundary cuts anything. Default is this repository's:
             b9cc0901, established from OUTSIDE by three non-shallow clones
             (mayor seat, crew/critic, crew/atlas), which is the only place it
             can be established -- `rev-list --max-parents=0` inside a shallow
             clone returns the graft point by construction and would agree with
             itself. Pass it for any other repository.
USAGE
  exit 64
}

HEAD_REV=""; MAIN_REV="origin/main"; TRUE_ROOT="$TRUE_ROOT_DEFAULT"; positional=0
while [ $# -gt 0 ]; do
  case "$1" in
    --true-root) [ $# -ge 2 ] || usage; TRUE_ROOT="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) positional=$((positional + 1))
       case $positional in
         1) HEAD_REV="$1" ;;
         2) MAIN_REV="$1" ;;
         *) usage ;;
       esac
       shift ;;
  esac
done
[ -n "$HEAD_REV" ] || usage

refuse() { printf 'REFUSE: %s\n' "$1" >&2; exit 2; }

# What this checkout was configured to fetch, printed verbatim beside any
# absence. A seat with `+refs/heads/main:refs/remotes/origin/main` never fetches
# a PR branch however deep it is, and `--unshallow` there leaves it blind and
# holding a receipt (hy-4dci). The refspec is printed, not interpreted: reading
# a wildcard for coverage is its own defect (hy-m78p).
fetch_scope() {
  local refspecs count
  refspecs=$(git config --get-all remote.origin.fetch 2>/dev/null | tr '\n' ' ')
  count=$(git for-each-ref --format='%(refname)' refs/remotes 2>/dev/null | wc -l | tr -d ' ')
  printf 'remote.origin.fetch=%s; refs/remotes=%s' "${refspecs:-<unset>}" "${count:-0}"
}

# `git rev-parse --verify` validates SPELLING, not existence: measured in an
# empty repository, a 40-hex string nobody holds comes back rc=0 echoing itself.
# PEELING is what turns it into an existence test -- the same string with
# `^{commit}` exits 1 and prints nothing, because peeling has to read the object.
# So there is no `cat-file -e` beside this, deliberately: it was here, no arm
# could feel its removal, and a guard nothing can fail is a comment.
# (refcheck.sh does need `cat-file -e` -- it is handed a TREE, which cannot be
# peeled to a commit at all.)
resolve() {
  local rev sha
  rev="$1"
  sha=$(git rev-parse --verify --quiet "${rev}^{commit}" 2>/dev/null) || sha=""
  if [ -z "$sha" ]; then
    refuse "this checkout does not hold ${rev}. $(fetch_scope). Repair with \`git fetch origin <sha>\` or \`git fetch origin refs/pull/<n>/head\`, not \`git fetch --unshallow\`"
  fi
  printf '%s' "$sha"
}

H=$(resolve "$HEAD_REV") || exit $?
M=$(resolve "$MAIN_REV") || exit $?

STDERR=$(mktemp); trap 'rm -f "$STDERR"' EXIT
mb=$(git merge-base "$H" "$M" 2>"$STDERR"); rc=$?
case $rc in
  0) ;;
  1)
    # rc=1 AND EMPTY STDERR is the only shape entitled to the words "no common
    # ancestor", and it took a third population to learn that. A store missing an
    # ANCESTOR object -- tips fine, both peel, nothing shallow -- also exits 1
    # with empty stdout, and says so on stderr: `error: Could not read <sha>`.
    # Measured by deleting one loose commit object. hy-fy7d's published snippet
    # sends that stream to /dev/null and then decides on the shallow file alone,
    # so it answers NO-COMMON-ANCESTOR for a checkout that told it otherwise in
    # words. A genuine orphan writes ZERO bytes there, measured on the same box.
    if [ -s "$STDERR" ]; then
      refuse "git merge-base ${H} ${M} exited 1 and wrote to stderr, so this is not the empty answer it looks like: $(head -1 "$STDERR")"
    fi
    # Then the cut-history question: a shallow file naming anything other than
    # the true root means history was cut here, and the ancestor may be sitting
    # below the cut in the repository this was cloned from.
    sf="$(git rev-parse --path-format=absolute --git-common-dir)/shallow"
    if [ -s "$sf" ] && awk -v r="$TRUE_ROOT" '$0 != r {f=1} END {exit !f}' "$sf"; then
      refuse "merge-base ${H} ${M} came back empty in a checkout whose history is cut -- $(wc -l < "$sf" | tr -d ' ') shallow boundary/boundaries, and at least one is not the declared true root ${TRUE_ROOT}. A cut seat and a genuine orphan return the same rc=1 and the same empty stdout. Repair with \`git fetch --unshallow origin\`, then re-run"
    fi
    printf 'NO COMMON ANCESTOR: %s and %s do not meet in this checkout, whose history is not cut\n' "$H" "$M"
    exit 3
    ;;
  *)
    # No arm exercises this one and the suite says so rather than pretending:
    # every corruption reachable in a test -- deleted object, garbage object,
    # depth-1 graft -- came back rc=1, and 128 is what a bad ARGUMENT produces,
    # which `resolve` has already refused above. It stays because falling out of
    # this `case` with an unread rc would carry an empty merge base forward.
    refuse "git merge-base ${H} ${M} exited ${rc}; that is neither an answer nor a no. $(head -1 "$STDERR")"
    ;;
esac

# `--no-renames` ON BOTH SIDES, and it is the difference between a set and a
# lower bound (hy-8v1k). Rename detection is ON by default since git 2.9, and it
# reports a rename as ONE moved file: the NEW path enters the changed set and the
# OLD path never does. A stale reference cites the old path -- that is what makes
# it stale -- so the detecting form is blind to precisely the class clause 3 and
# refcheck exist to catch.
#
# THE FAILURE DIRECTION IS WHY THIS IS NOT A TIDY-UP. Fewer files, smaller
# intersection, and the missing case is reported as NO INTERSECTION: clause 3
# satisfied. It fails toward the reassuring answer, and it does it silently.
#
# Measured on a constructed pair: merge base holds a.py, main renames it to b.py,
# the head edits a.py. The detecting form gives main {b.py} against head {a.py},
# intersects to nothing, and this script exited 0 saying the trial merge was the
# only thing left. `--no-renames` gives main {a.py, b.py} and it exits 4 naming
# a.py.
head_files=$(git diff --no-renames --name-only "$mb" "$H") || refuse "git diff ${mb} ${H} failed"
main_files=$(git diff --no-renames --name-only "$mb" "$M") || refuse "git diff ${mb} ${M} failed"

hit=$(comm -12 <(printf '%s\n' "$head_files" | sort -u) \
                <(printf '%s\n' "$main_files" | sort -u) | sed '/^$/d')

printf 'merge base %s; head changed %s file(s); main changed %s file(s) since\n' \
  "$mb" "$(printf '%s\n' "$head_files" | sed '/^$/d' | wc -l | tr -d ' ')" \
  "$(printf '%s\n' "$main_files" | sed '/^$/d' | wc -l | tr -d ' ')"

if [ -n "$hit" ]; then
  printf 'INTERSECTION (clause 3): back to critic with these named\n'
  printf '%s\n' "$hit"
  exit 4
fi

# hy-1ryd DATA-READ ARM. The path intersection is EMPTY, but a path intersection
# is blind to coupling through data READ AT RUNTIME. A test that globs a
# fixture/recording directory reads files it shares no path with -- so main
# rewriting one of those files and the head editing the test that reads them
# intersect to nothing, and a conflict-only trial merge is blind to it too,
# because the two sides touch different paths and merge without a conflict. The
# measured case: #197 touched only test_committed_recordings_resolve.py, main had
# gained #194's rewrite of two recordings that test enumerates and reads; empty
# intersection, clean merge, and the merged-tree suite was the only instrument
# that could see it.
#
# Deriving the READ SET from a globbing test's own diff is not possible (the file
# names a directory, not the members), so this is the bead's sanctioned weaker
# fallback: if EITHER side's changed files touch a fixture/recording directory,
# do not report the trial merge as the remaining check -- require the SUITE at the
# merged tree. Like exit 4 this is a NOMINATION, never a bounce: it says a cheaper
# instrument cannot clear this pair, not that the pair is broken.
#
# Gated on main having diverged. When main changed nothing since the merge base
# the head CONTAINS main -- the strongest state, not a blind one -- the merged
# tree equals the head tree, and the head's own CI already ran the suite against
# it. There is no second tree to check, so the arm does not fire (refcheck's
# "empty main-side set" reasoning, applied here).
# The read set, by name -- every place a test reads data it does not share a path
# with, so main rewriting it and a head editing the reader intersect to nothing.
# Directories: recordings/cases/prompts (the eval read set -- globbed and
# fixed-path both couple invisibly, e.g. main rewriting cases/revenue.yaml vs a
# head editing the test that loads it); fixtures (every test fixture tree); and
# docs (whole -- adversarial review kept finding one more doc a tests/unit
# assertion checks against source: benchmark.md's numbers vs scored recordings,
# v0-foundation.md's stated version vs the served contract, docs/adr vs the
# ratchet. Covering the tree ends the per-doc whack-a-mole and over-fires only
# toward a suite run, the safe direction). Files outside docs a test asserts
# against source: expected_failures.yaml (the ratchet's data), ci.yml (a test
# reads the workflow steps), pyproject.toml/AGENTS.md/CLAUDE.md
# (test_gate_evidence_line checks the gate line and distributions against them).
#
# NOT everything non-.py: the foundational no-bounce case moves a root .txt the
# head never reads, and clause 3's whole point is that it merge clean -- so this
# is a CURATED list, not "any data file". The general read set cannot be read off
# a globbing test's own diff (hy-1ryd's deferred hard half), and a NEW read
# location a future test adds OUTSIDE docs must be named here by hand;
# test_the_data_arm_covers_the_known_read_sets pins the known ones so a regex edit
# that drops one reddens.
data_re='(^|/)(recordings|fixtures|cases|prompts)/|(^|/)docs/|(^|/)(expected_failures\.yaml|ci\.yml|pyproject\.toml|AGENTS\.md|CLAUDE\.md)$'
if [ -n "$(printf '%s\n' "$main_files" | sed '/^$/d')" ]; then
  head_data=$(printf '%s\n' "$head_files" | grep -E "$data_re" || true)
  main_data=$(printf '%s\n' "$main_files" | grep -E "$data_re" || true)
  if [ -n "${head_data}${main_data}" ]; then
    printf 'NO PATH INTERSECTION, but a fixture/recording directory was touched -- a path\n'
    printf 'intersection and a conflict-only trial merge are both blind to coupling through\n'
    printf 'data a test reads at runtime by globbing that directory (hy-1ryd). The merged-tree\n'
    printf 'SUITE is the remaining check, not the trial merge. Files under such a directory:\n'
    [ -n "$main_data" ] && printf '%s\n' "$main_data" | sed 's/^/  main: /'
    [ -n "$head_data" ] && printf '%s\n' "$head_data" | sed 's/^/  head: /'
    exit 5
  fi
fi

printf 'NO INTERSECTION: clause 3 satisfied; the trial merge is the remaining check\n'
exit 0
