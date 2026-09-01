#!/usr/bin/env bash
# hy-asip companion to clause 3, run by the MERGER at merge time.
#
#   Which files in main mention the changed module by name, and did main modify
#   any of them since the merge base?
#
# Deliberately a grep over the MERGED TREE, not an import graph: the dangerous
# references are the ones no import graph contains -- docstrings, comments, skip
# reasons, fixture paths, docs.
#
# exit 0 = no references          exit 2 = HOLD  (both halves hit)
# exit 3 = NOTE (first half only) exit 1 = REFUSE (could not see; never an answer)
#
# Ported from the refinery's scratchpad (hy-ou0o), where it was base-rated against
# five known merges before it moved here. The semantics are hy-asip's and are not
# mine to change; what this file adds is a home where it can be reviewed and a
# suite where each of its arms can be made to fail. The one behavioural change is
# `grep_names` below, which amendment 4 required after the same defect fired twice
# in one hour: an empty result read out of a field nobody checked exists.
set -u
usage() { echo "usage: refcheck.sh <merged-tree-or-commit> <main-sha> <merge-base>" >&2; exit 1; }
[ $# -eq 3 ] || usage
TREE=$1; MAIN=$2; MB=$3

for o in "$TREE" "$MAIN" "$MB"; do
  # `git rev-parse --verify <40-hex>` returns rc=0 and echoes the sha for an object that
  # does NOT exist -- it validates spelling, not existence, so a literal deadbeef... walks
  # straight through. `cat-file -e` is the existence test; it takes a PLAIN object name
  # (adding ^{object} makes it fatal at 128) and accepts commit, tree and blob alike.
  # Do not use ^{commit}: this script is passed a TREE and ^{commit} would refuse it.
  git cat-file -e "${o}" 2>/dev/null || { echo "REFUSE: cannot resolve ${o}" >&2; exit 1; }
done

# Names of the files in <tree> that contain any of the given -e patterns, with the
# "<tree>:" prefix `git grep` puts on every line stripped off.
#
# This exists because BOTH of this script's greps read a shape nobody asserted.
# `git grep -l <pattern> <tree>` prefixes each line with "<tree>:"; the strip is a
# sed. If that prefix is not what arrives -- a git that spells it differently, an
# argument that is not a tree, a wrapper on PATH -- the sed is a no-op, every name
# compares unequal to every path, half two intersects to nothing, and a HOLD is
# printed as NO REFERENCE INTERACTION. Nothing in the output would look wrong.
#
# So the prefix is asserted before an empty or non-matching result is believed, and
# the refusal prints the line it actually got rather than the lookup that failed.
# That is amendment 4 on hy-ou0o, generalised from two bead reads minutes apart: one
# probed a key that does not exist and reported a storage failure that never
# happened, the other named the body key wrong and reported 0 bytes for a 5634-byte
# comment. A query against a field that is not there returns absence, and absence
# reads as an answer -- in either direction.
#
# rc 1 from `git grep` is "no matches" and is an answer; anything above 1 is not.
grep_names() {
  tree="$1"; shift
  raw=$(git grep -l -F "$@" "${tree}" 2>/dev/null); grc=$?
  if [ ${grc} -gt 1 ]; then
    echo "REFUSE: git grep over ${tree} failed (rc=${grc}); no result to read" >&2
    return 1
  fi
  [ ${grc} -eq 0 ] || return 0
  first=${raw%%$'\n'*}
  case "${first}" in
    "${tree}:"*) ;;
    *)
      echo "REFUSE: git grep output does not carry the \"${tree}:\" prefix this script strips, so an empty match set would be unreadable rather than absent -- first line was: ${first}" >&2
      return 1
      ;;
  esac
  echo "${raw}" | sed "s|^${tree}:||"
}

# `--no-renames` ON BOTH DIFFS (hy-8v1k). Rename detection is on by default since
# git 2.9 and collapses a rename to its NEW path alone, so the OLD path never
# enters either set. This script's whole subject is references that cite a path by
# name, and a reference goes stale by continuing to cite the OLD one -- so the
# detecting form drops exactly the rows worth checking, and drops them toward
# NO REFERENCE INTERACTION.
#
# The sharp case is a PR that RENAMES a module. Detecting, CHANGED holds only the
# new path, nothing ever greps for the old name, and every doc still citing it
# reads clean. Undetected, the old path arrives as a deletion -- which this script
# already handles below, reading the probe from main's copy and tagging the row
# [DELETED by this PR]. That path existed before this flag and had no way to be
# reached for a rename.
#
# The PR's changed files = merged tree vs main. Empty means nothing to check, which
# is never a clean answer -- it means the caller passed the wrong pair.
# bash 3.2 on macOS has no mapfile; keep it newline-delimited.
CHANGED=$(git diff --no-renames --name-only "${MAIN}" "${TREE}")
[ -n "${CHANGED}" ] || { echo "REFUSE: no changed files between ${MAIN} and ${TREE}" >&2; exit 1; }

# main IS the merge base is the SAFEST input, not a suspect one (hy-ezwm). The
# PR is based on the current tip, so main has modified nothing there is anything
# to conflict with, and the empty MAINMOD below would REFUSE it vacuously --
# blocking exactly the merges most obviously safe to take, which is every PR on
# the current tip. Short-circuit to a clean answer. This is decided by identity,
# not by an empty diff: a merge base that is resolvable but genuinely WRONG is a
# different input -- MB != MAIN and yet still an empty diff, e.g. an empty commit
# on main -- and the REFUSE below still catches it.
if [ "$(git rev-parse "${MB}")" = "$(git rev-parse "${MAIN}")" ]; then
  echo "NO REFERENCE INTERACTION: main is at the merge base (${MB:0:8}); 0 main-side modifications to check"
  exit 0
fi

# Files main itself moved since the merge base -- the second half of the question.
MAINMOD=$(git diff --no-renames --name-only "${MB}" "${MAIN}" | sort)
[ -n "${MAINMOD}" ] || { echo "REFUSE: main modified nothing since ${MB}; suspect wrong merge-base" >&2; exit 1; }

rc=0
OLDIFS=$IFS; IFS=$'\n'
for f in ${CHANGED}; do
  IFS=$OLDIFS
  base=$(basename "${f}")
  dotted=$(echo "${f}" | sed 's|/|.|g; s|\.py$||')

  # PER-RUN CONTROL: the grep must be able to find something that is definitely
  # there, or it is blind and must not report. Probe with the longest substantial
  # line of the file itself and require the grep to locate that file. Works for ANY
  # text file -- the earlier def/class probe silently skipped every non-Python file,
  # which is precisely the docstring/doc/comment case this check exists for.
  # A file in the diff but ABSENT from the merged tree is a DELETION, not blindness.
  # Probe from main's copy instead and keep searching: a reference to a file this PR
  # deletes is the sharpest form of the hazard, so it must not be skipped.
  src="${TREE}"; deleted=""
  if ! git cat-file -e "${TREE}:${f}" 2>/dev/null; then src="${MAIN}"; deleted=" [DELETED by this PR]"; fi
  probe=$(git cat-file blob "${src}:${f}" 2>/dev/null \
          | grep -vE '^[[:space:]]*$' | awk '{ if (length($0)>=24 && length($0)<=200) print }' \
          | head -1 | sed 's/^[[:space:]]*//')
  if [ -n "${probe}" ]; then
    # The control runs on the deletion path too, against main's copy. Skipping it
    # there was the old shape: the one case where the file is not in the tree is
    # also the one case where the grep's output shape went unchecked.
    control=$(grep_names "${src}" -e "${probe}") || exit 1
    if ! printf '%s\n' "${control}" | grep -qx "${f}"; then
      echo "REFUSE: control failed for ${f} -- grep cannot find that file by its own content" >&2
      exit 1
    fi
  else
    echo "REFUSE: no usable control probe in ${f}; refusing rather than reporting blind" >&2
    exit 1
  fi

  # Half one: who mentions this module BY NAME anywhere in the merged tree?
  # NOT the bare stem. "benchmark", "provenance" are English words; matching them
  # made 1 of 5 merges a false HOLD (docs/v0-foundation.md uses "provenance" 10
  # times and never names provenance.py). "By name" = base name or dotted path.
  #
  # Two statements, not one pipeline: `hits=$(grep_names ... | grep -v ... )` would
  # report the last stage's status and swallow the refusal the first stage raised.
  matched=$(grep_names "${TREE}" -e "${base}" -e "${dotted}") || exit 1
  hits=$(printf '%s\n' "${matched}" | grep -v "^${f}$" | sort)
  [ -n "${hits}" ] || continue

  # Half two: of those, which did main modify since the merge base?
  both=$(comm -12 <(echo "${hits}") <(echo "${MAINMOD}"))
  if [ -n "${both}" ]; then
    echo "HOLD  ${f}${deleted}: mentioned by, AND main modified since ${MB:0:8}:"
    echo "${both}" | sed 's/^/        /'
    rc=2
  else
    echo "note  ${f}: mentioned by $(echo "${hits}" | wc -l | tr -d ' ') file(s), none modified by main"
    [ $rc -eq 0 ] && rc=3
  fi
  IFS=$'\n'
done
IFS=$OLDIFS
[ $rc -eq 0 ] && echo "NO REFERENCE INTERACTION"
exit $rc
