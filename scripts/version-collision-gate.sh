#!/usr/bin/env bash
#
# version-collision-gate.sh
#
# A merge precondition: no two open heads may ship two different meanings
# under one value of a served version constant.
#
# Why it exists: #164 and #165 each independently bumped SCHEMA_VERSION 4->5,
# for two unrelated schema changes. Both were open, both green, neither could
# merge as it stood, and three checks aimed at the problem returned clean. A
# touch-test asks WHETHER a head edited the constant; a collision is about
# WHICH VALUE it assigned. Nothing else in this repository compares a version
# constant across two trees, so this is the first such check, not a tightening
# of an existing one.
#
# Two rules, and neither implies the other:
#
#   RULE A  head vs head. Two CLAIMING heads with the same value, neither an
#           ancestor of the other.
#   RULE B  head vs main. A CLAIMING head whose value is the one main already
#           uses.
#
# Rule B is not redundant. The moment one of two colliding heads merges it
# leaves the open-head set, so rule A has nothing left to compare the other
# against -- and that is the highest-hazard moment in the sequence, not the
# safest.
#
# A head CLAIMS if its value differs from the value at ITS OWN MERGE BASE with
# main -- not from current main. The two definitions agree today and diverge
# exactly when it matters: after #165 lands, main is 5, and #164 at 5 reads as
# "inherits" against current main. That definition goes silent on #164 at the
# moment #164 becomes dangerous. Against its own merge base (4), #164 still
# claims, and rule B still fires. A merge-base claim is invariant under other
# merges, which is the property comparing to current main lacks.
#
# The "differs from base" clause in either form is load-bearing: without it
# every pair among the heads that merely INHERIT collides, and the one real
# collision is buried under dozens of false ones. So is the ancestor clause:
# when a bump sits low in a stack, every head above it legitimately claims the
# same value.
#
# Exit codes. 1 and 3 must never merge, and must never be collapsed: one says
# "I could not look", the other says "I looked and found a problem".
#
#   0  no collision, across a scope this run states
#   1  REFUSE -- could not see, or could see only part: a failed read, a
#      missing object, a value that is not a number, a file the list names
#      that main does not have, or a register that gained a member while the
#      number stood still
#   2  usage error, including a constant list that is empty, short a field, or
#      naming an extraction this gate cannot perform
#   3  collision found
#
# What exit 0 does and does not mean, both sentences:
#
#   Exit 0 means no collision among CLAIMING heads.
#   Exit 0 does not mean the version numbers are correct -- nothing tests that.
#
# The second sentence is the scope, not a caveat. A head that bumps to a
# number no other head claims passes this gate however wrong that number is;
# catching a wrong bump on a SINGLE branch is a different instrument. Since
# the gate being green will be the only thing anyone can point at, per-constant
# limits are configuration too -- see the `caveat` field of the constant list.
#
# Three things this script deliberately does not do, each because doing it was
# measured to fail silently:
#
#   It never takes a list of heads. A literal PR list in a release gate goes
#   stale the moment someone opens a PR, and it goes stale quietly. The heads
#   are enumerated from the forge on every run.
#
#   It never reads refs/pull/*. That namespace is fetched by some clones and
#   not others; a gate that refuses in the seat that runs it gets disabled
#   within a week. A refusal may still NAME that ref as a repair for a person
#   to run, which is not the gate depending on it. Values and merge bases are read through the forge at the
#   sha, which needs no local object at all. Git reads an OBJECT only for the
#   ancestry check, and only for the few heads that end up in a same-value
#   pair; it is also asked for `remote get-url origin` once, to learn which
#   repository this checkout is of.
#
#   It never holds a constant name in its own text. Which constants are served
#   is product knowledge that changes; it lives in version-constants.tsv --
#   which is itself a way to run a gate over nothing, so the scope it checked
#   is stated by every run that got as far as reading the list -- including
#   both enumeration refusals, which cover nothing and have to say so -- and an
#   empty list is refused. "Checked 0 constants" must not be a way to exit 0.
#   The exits that print no scope are the ones with no list to state: an
#   unreadable or empty version-constants.tsv, an unresolvable origin, --help.
#
#   It never treats a read it could not make as an answer. Reading a value has
#   three outcomes and they are never collapsed: a value; the constant is
#   ABSENT there, which at a head's merge base means the head claims the value
#   it introduced; or the read FAILED, which is a refusal. The tempting middle
#   reading -- "no value at base, so no claim, so quiet" -- is a clean
#   all-clear from an instrument that saw nothing.
#
# Requires: gh (authenticated), git
#
# Usage:
#   ./scripts/version-collision-gate.sh
#   ./scripts/version-collision-gate.sh --constants path/to/other.tsv

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO="${VERSION_GATE_REPO:-}"
BASE="main"
CONSTANTS_FILE="${SCRIPT_DIR}/version-constants.tsv"

# Where the forge's own complaint is kept, so "not found" and "could not ask"
# stay distinguishable instead of both arriving as a non-zero exit.
FORGE_ERRORS="$(mktemp)"
trap 'rm -f "$FORGE_ERRORS"' EXIT

usage() {
  cat <<'USAGE'
Usage: version-collision-gate.sh [options]

Refuse a queue in which two open heads ship two different meanings under one
value of a served version constant.

  RULE A  two CLAIMING heads with the same value, neither an ancestor of the
          other.
  RULE B  a CLAIMING head whose value is the one main already uses.

A head CLAIMS if its value differs from the value at its own merge base with
main. Against current main instead, the gate goes silent on the second of two
colliding heads the moment the first one lands.

Options:
  --repo <owner/name>   Forge repository (default: the origin remote's)
  --base <ref>          Branch the heads are aimed at (default: main)
  --constants <file>    Constant list (default: scripts/version-constants.tsv)
  -h, --help            This text, plus the configured per-constant caveats

Exit codes, four meanings the caller has to triage:
  0  no collision, across a scope the run states before it sweeps
  1  REFUSE. The gate could not see, or could see only part: a read that
     failed, a missing object, a value that is not a number, a listed file
     main does not have, or a register that gained a member while its number
     stood still. Nothing was established.
  2  usage error, including a constant list that is empty, short a field, or
     naming an extraction this gate cannot perform. Nothing was read.
  3  collision found. The gate looked and the queue has a problem.

  1 and 3 are never collapsed: one says "I could not look", the other "I
  looked and found a problem". 2 is the list itself being unusable, which is
  answerable without looking at anything.

Exit 0 means no collision among CLAIMING heads.
Exit 0 does not mean the version numbers are correct -- nothing tests that.
USAGE
  print_caveats
}

# Two ways to stop, kept apart on purpose. `refuse` is "I could not look" and
# is the answer to every unreadable input; `bad_usage` is "you asked wrong",
# which includes a constant list this script cannot act on.
refuse() {
  printf 'REFUSING TO REPORT: %s\n' "$1" >&2
  exit 1
}

bad_usage() {
  printf 'usage error: %s\n' "$1" >&2
  exit 2
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo) [[ $# -ge 2 ]] || bad_usage "--repo needs a value"; REPO="$2"; shift 2 ;;
      --base) [[ $# -ge 2 ]] || bad_usage "--base needs a value"; BASE="$2"; shift 2 ;;
      --constants)
        [[ $# -ge 2 ]] || bad_usage "--constants needs a value"
        CONSTANTS_FILE="$2"
        shift 2
        ;;
      -h|--help) usage; exit 0 ;;
      *) bad_usage "unknown argument: $1" ;;
    esac
  done
}

resolve_repo() {
  [[ -n "$REPO" ]] && return 0
  local remote
  remote=$(git remote get-url origin)
  if [[ $? -ne 0 || -z "$remote" ]]; then
    refuse "no --repo given and no origin remote to take one from"
  fi
  # git@host:owner/name.git and https://host/owner/name.git both reduce here.
  remote="${remote%.git}"
  remote="${remote#*://}"
  remote="${remote#*:}"
  REPO="${remote#*/}"
  [[ "$REPO" == */* ]] || refuse "could not read owner/name out of the origin url"
}

read_constants() {
  [[ -f "$CONSTANTS_FILE" ]] || bad_usage "no constant list at ${CONSTANTS_FILE}"
  local name path kind register caveat
  while IFS=$'\t' read -r name path kind register caveat; do
    [[ -z "$name" || "${name:0:1}" == "#" ]] && continue
    [[ -n "$path" && -n "$kind" && -n "$register" && -n "$caveat" ]] ||
      bad_usage "constant list row for '${name}' needs name, path, extraction, register and caveat (use '-' for no register and no caveat)"
    supported_extraction "$kind" ||
      bad_usage "constant '${name}' declares extraction '${kind}', which this gate cannot perform"
    CONSTANT_NAMES+=("$name")
    CONSTANT_PATHS+=("$path")
    CONSTANT_KINDS+=("$kind")
    CONSTANT_REGISTERS+=("$register")
    CONSTANT_CAVEATS+=("$caveat")
  done <"$CONSTANTS_FILE"
  # A list naming nothing is the cheapest way to make this gate report a clean
  # queue it never examined, and the report reads identically either way.
  [[ ${#CONSTANT_NAMES[@]} -gt 0 ]] ||
    bad_usage "constant list ${CONSTANTS_FILE} names no constants; a gate that checked nothing must not report clean"
}

print_caveats() {
  local i
  CONSTANT_NAMES=() CONSTANT_PATHS=() CONSTANT_KINDS=() CONSTANT_REGISTERS=() CONSTANT_CAVEATS=()
  read_constants
  printf '\nWhat the gate does not establish, per constant (%s):\n' "$CONSTANTS_FILE"
  for i in "${!CONSTANT_NAMES[@]}"; do
    if [[ "${CONSTANT_CAVEATS[$i]}" == "-" ]]; then
      printf '  %s: nothing beyond the sentence above.\n' "${CONSTANT_NAMES[$i]}"
    else
      printf '  %s: %s\n' "${CONSTANT_NAMES[$i]}" "${CONSTANT_CAVEATS[$i]}"
    fi
  done
}

# How a value is read is declared per constant, because the next constant on
# the list will not be a bare assignment and a gate that assumes it is would
# read an empty string and call it inherited.
#
# Which kinds exist is answered once, and answered at read time rather than at
# extraction time: an unreadable declaration is a usage error, and inside the
# command substitution that wraps every extraction an exit would only leave
# the subshell, turning "I cannot perform this" into an empty value and then
# into a refusal wearing the wrong exit code.
supported_extraction() {
  case "$1" in
    bare_int) return 0 ;;
    *) return 1 ;;
  esac
}

extract_value() {
  local kind="$1" name="$2"
  case "$kind" in
    bare_int)
      # Anchored on the exact name so `RULE_VERSIONS = {RULE_ID: RULE_VERSION}`
      # one line below `RULE_VERSION = 1` cannot be read as either of them.
      sed -n "s/^${name}[[:space:]]*=[[:space:]]*\([0-9][0-9]*\)[[:space:]]*\(#.*\)*$/\1/p" | head -1
      ;;
    *) return 1 ;;
  esac
}

# One file at one commit, from the forge, so no local object is needed. The
# three outcomes are kept apart by the caller's exit code, never by an empty
# string: 0 the file is served, 2 the forge says it is not there, 1 the read
# itself failed. Collapsing 1 into 2 turns a rate limit into "the constant
# does not exist here", which reads as good news.
fetch_blob() {
  local ref="$1" path="$2" raw rc
  raw=$(gh api -H "Accept: application/vnd.github.raw" \
    "repos/${REPO}/contents/${path}?ref=${ref}" 2>"$FORGE_ERRORS")
  rc=$?
  if [[ $rc -eq 0 ]]; then
    printf '%s' "$raw"
    return 0
  fi
  grep -qi "404\|not found" "$FORGE_ERRORS" && return 2
  return 1
}

# Sets READ_STATE and READ_VALUE rather than printing, because the state is
# the part callers get wrong. Four states, and each caller decides what its
# own position makes of them:
#
#   value      the constant is here and is a number
#   absent     the file or the constant is not here at all -- a FACT about
#              that commit, and at a head's merge base it means the head
#              introduced the constant and claims the value it introduced
#   malformed  the constant is here and its value is not a number
#   failed     the read did not happen
read_value() {
  local ref="$1" path="$2" kind="$3" name="$4" raw rc value
  raw=$(fetch_blob "$ref" "$path")
  rc=$?
  READ_VALUE=""
  case $rc in
    1) READ_STATE=failed; READ_FAILURE=$(head -1 "$FORGE_ERRORS"); return 0 ;;
    2) READ_STATE=absent; return 0 ;;
  esac
  if ! printf '%s\n' "$raw" | grep -q "^${name}[[:space:]]*="; then
    READ_STATE=absent
    return 0
  fi
  value=$(printf '%s\n' "$raw" | extract_value "$kind" "$name")
  # The gate validates its own OUTPUT is well formed, not merely that its
  # inputs resolved. Written because of a measured failure: a `git show
  # "$mb:path"` in zsh had `$mb:h` eaten as a dirname modifier, so every read
  # returned an empty string, every object resolved, every presence guard
  # passed, and the verdict column read "inherits" for all twelve heads.
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    READ_STATE=value
    READ_VALUE="$value"
  else
    READ_STATE=malformed
    READ_VALUE="$value"
  fi
  return 0
}

# Any read this gate could not make, refused in the caller's own words. The
# state is passed in rather than re-derived so that "absent" can mean
# different things in different positions and still be spelled out here.
refuse_read() {
  local state="$1" what="$2"
  case "$state" in
    failed) refuse "could not read ${what}: ${READ_FAILURE}" ;;
    absent) refuse "${what} is not there at all" ;;
    malformed) refuse "${what} value not an integer: '${READ_VALUE}'" ;;
    *) refuse "${what}: unhandled read state '${state}'" ;;
  esac
}

# A register's membership at one commit, for the constants whose meaning it is
# part of. Deliberately narrow: a single-line collection. A declaration this
# cannot read is refused rather than guessed at, because guessing here means
# reporting a membership nobody measured.
read_register() {
  local ref="$1" path="$2" register="$3" raw rc line inner
  raw=$(fetch_blob "$ref" "$path")
  rc=$?
  REGISTER_MEMBERS=""
  case $rc in
    1) REGISTER_STATE=failed; READ_FAILURE=$(head -1 "$FORGE_ERRORS"); return 0 ;;
    2) REGISTER_STATE=absent; return 0 ;;
  esac
  line=$(printf '%s\n' "$raw" | grep "^${register}[[:space:]]*=" | head -1)
  if [[ -z "$line" ]]; then
    REGISTER_STATE=absent
    return 0
  fi
  inner=$(printf '%s' "$line" | sed -n 's/^[^({[]*[({[]\(.*\)[)}]].*$/\1/p')
  if [[ -z "$inner" ]]; then
    inner=$(printf '%s' "$line" | sed -n 's/^[^({[]*[({[]\(.*\)[)}]]*$/\1/p')
  fi
  if [[ -z "$inner" ]]; then
    REGISTER_STATE=unreadable
    return 0
  fi
  REGISTER_MEMBERS=$(printf '%s' "$inner" |
    tr ',' '\n' |
    sed -e 's/:.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']//' -e 's/["'"'"']$//' |
    grep -v '^$' |
    sort |
    tr '\n' ' ')
  REGISTER_STATE=members
  return 0
}

# Ancestry needs real objects, so only the heads that end up in a same-value
# pair are ever fetched.
have_object() {
  git cat-file -e "${1}^{commit}" 2>/dev/null
}

# What this checkout was configured to fetch, and how much of the board it
# has. Crew seats on this rig clone with `+refs/heads/main:refs/remotes/origin/main`
# and never see a PR branch, while the ref count is a fossil of when the clone
# was made rather than of what it can reach -- two seats under one refspec held
# 166 and 52 (hy-4dci, measured 2026-07-31 with
# `git for-each-ref --format='%(refname)' refs/remotes | wc -l`). Without this
# beside an absence the reader cannot tell a commit that does not exist from
# one this seat was never configured to see.
fetch_scope() {
  local refspecs count
  refspecs=$(git config --get-all remote.origin.fetch 2>/dev/null | tr '\n' ' ')
  refspecs="${refspecs%"${refspecs##*[![:space:]]}"}"
  count=$(git for-each-ref --format='%(refname)' refs/remotes 2>/dev/null | wc -l | tr -d ' ')
  printf 'remote.origin.fetch=%s; refs/remotes=%s' "${refspecs:-<unset>}" "${count:-0}"
}

ensure_object() {
  local sha="$1" head_ref="$2" number="$3"
  have_object "$sha" && return 0
  git fetch --quiet origin "$head_ref"
  # Not "absent from the repository": this seat does not hold it, which is the
  # only one of those two a local check can measure. The repair names the
  # object, because a seat that cannot see the branch can still be handed the
  # sha; `--unshallow` is the repair for truncation and would leave this seat
  # blind and looking repaired.
  have_object "$sha" ||
    refuse "MISSING OBJECT #${number} ${sha} -- this checkout does not hold it (fetched origin/${head_ref} and it is still not here). $(fetch_scope). Repair with \`git fetch origin ${sha}\` or \`git fetch origin refs/pull/${number}/head\`, not \`git fetch --unshallow\`"
}

# A shallow or grafted seat stops an ancestry walk at a BOUNDARY it knows about,
# so `--is-ancestor` returns a CLEAN rc=1 (no error) for a pair that is related
# below the cut -- git says nothing, so only the repository's shape reveals it.
# This is the half of the truncation problem git's own stderr cannot flag (the
# other half, an ABSENT object, it flags loudly -- see `is_ancestor`). A narrow
# refspec is deliberately NOT here: a seat that never fetched the connecting
# branch is missing those OBJECTS, so the walk hits an absent object and git
# writes `could not read`, which the stderr arm catches -- and a narrow-refspec
# seat that DOES hold the link answers correctly and must not be refused (that
# over-refusal would disable the gate on every crew seat). Keyed on the
# repository's shape, never on uname -- the axis is the git version and the
# history, not the platform (hy-h08a). Mirrors the shallow/graft half of
# `hyperset/evals/provenance.py`'s truncation evidence, in shell.
history_incomplete() {
  [[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" == "true" ]] && return 0
  local grafts
  grafts=$(git rev-parse --git-path info/grafts 2>/dev/null)
  [[ -n "$grafts" && -s "$grafts" ]] && return 0
  [[ -n "$(git for-each-ref --count=1 --format='%(refname)' refs/replace 2>/dev/null)" ]] && return 0
  return 1
}

# `git merge-base --is-ancestor` exits non-zero on a MISSING OBJECT exactly as
# it does on "not an ancestor". Swallowing that with `|| true` or `2>/dev/null`
# reports "no prefix relation" -- in the reassuring direction -- for a repo that
# simply does not have the commits. The check never runs before `ensure_object`.
#
# A NON-ZERO EXIT IS NOT-AN-ANCESTOR ONLY WHEN GIT COULD WALK TO DECIDE (hy-nijz).
# Both heads can be present -- `cat-file -e` passes, `ensure_object` is satisfied --
# while the history that CONNECTS them is cut, and `--is-ancestor` then reports a
# pair that IS related on a whole clone as non-related. Two such cuts, announcing
# themselves differently:
#   - an ABSENT object on the path (a narrow-refspec seat that never fetched the
#     link, a raced gc, corruption): git writes `Could not read <sha>` to stderr.
#   - a shallow/graft BOUNDARY: git stops cleanly, empty stderr. No error to catch,
#     so `history_incomplete` reveals it from the repository's shape.
# Either is UNKNOWN, not NO. Key on the STDERR, never on the exit CODE, because the
# code for the absent-object case is git-version-dependent -- 1 on git 2.39.5, 128
# on 2.47.3, both with the same stderr (hy-t0ot, hy-h08a). Only a CLEAN non-zero
# (empty stderr) on a whole seat is a trustworthy answer, and only rc=1 among those
# is a real "no" -- a genuine pair of siblings; any other clean non-zero is git
# malfunctioning, not a verdict. No per-pair merge-base query is sound: a deeper
# shared root can survive while the connecting commits are cut, so the merge base
# is non-empty and the answer still lies (adversarial review, hy-nijz). Stdout is
# dropped and stderr captured on the same line so the rc read is `--is-ancestor`'s.
is_ancestor() {
  local ancestor="$1" descendant="$2" rc err
  err=$(git merge-base --is-ancestor "$ancestor" "$descendant" 2>&1 >/dev/null)
  rc=$?
  [[ $rc -eq 0 ]] && return 0
  if [[ -n "$err" ]] || history_incomplete; then
    refuse "git merge-base --is-ancestor ${ancestor} ${descendant} exited ${rc} but this seat could not walk the history that would decide it, so the answer is UNKNOWN here and not NOT-AN-ANCESTOR. ${err:+git said: ${err}. }$(fetch_scope). Repair with \`git fetch --unshallow origin\` on a shallow seat, or by fetching the missing history, then re-run"
  fi
  [[ $rc -eq 1 ]] && return 1
  refuse "git merge-base --is-ancestor ${ancestor} ${descendant} exited ${rc} with no error text; that is neither an answer nor a no"
}

prefix_related() {
  is_ancestor "$1" "$2" && return 0
  is_ancestor "$2" "$1" && return 0
  return 1
}

# The merge base is read from the forge too, so the claim test needs no local
# object either -- and it has the same three outcomes as a blob read, kept
# apart the same way (hy-so04). One `return 1` for all of them made a
# rate-limited compare report MISSING OBJECT: "the head is not there", about a
# forge that never answered. Its own output is checked as well, because a
# compare that answers without a merge base would otherwise become an empty ref
# and then a confidently empty value.
#
#   value   the merge base, in MERGE_BASE_SHA
#   absent  the forge says there is no such comparison
#   failed  the read did not happen, or came back without a merge base
merge_base_of() {
  local sha="$1" answer rc
  MERGE_BASE_SHA=""
  answer=$(gh api "repos/${REPO}/compare/${BASE}...${sha}" -q '.merge_base_commit.sha' \
    2>"$FORGE_ERRORS")
  rc=$?
  if [[ $rc -ne 0 ]]; then
    if grep -qi "404\|not found" "$FORGE_ERRORS"; then
      MERGE_BASE_STATE=absent
    else
      MERGE_BASE_STATE=failed
      READ_FAILURE=$(head -1 "$FORGE_ERRORS")
    fi
    return 0
  fi
  if [[ ! "$answer" =~ ^[0-9a-f]{7,40}$ ]]; then
    MERGE_BASE_STATE=failed
    READ_FAILURE="compare answered without a merge base sha: '${answer}'"
    return 0
  fi
  MERGE_BASE_STATE=value
  MERGE_BASE_SHA="$answer"
  return 0
}

enumerate_heads() {
  local rc
  # The head set is enumerated here, every run, and this script offers no way
  # to supply one.
  HEADS=$(gh api --paginate "repos/${REPO}/pulls?state=open&per_page=100" \
    -q '.[] | [.number, .head.sha, .head.ref] | @tsv')
  rc=$?
  # Both refusals below happen with the constant list already read and no head
  # set at all, so the scope is stated here rather than back in `main`: these
  # are the runs that cover NOTHING, and the reader still has to be told what
  # was on the list they are now missing.
  if [[ $rc -ne 0 ]]; then
    print_scope "no open head, because the enumeration failed"
    refuse "could not enumerate open pull requests for ${REPO}"
  fi
  if [[ -z "$HEADS" ]]; then
    print_scope "no open head at all"
    refuse "enumeration returned no open pull requests; a gate that saw nothing must not report clean"
  fi
}

# A register that gained a member while its number stood still. Under hq-6htz
# case 3 as amended, a persisted-only register gaining a member MOVES the
# number, and this gate compares values -- so the one thing it can honestly do
# is refuse to call the head clean, naming the member it cannot weigh.
check_register() {
  local number="$1" sha="$2" head_ref="$3" name="$4" path="$5" register="$6" claims="$7"
  local base_members head_members member

  [[ "$register" == "-" ]] && return 0

  read_register "$MERGE_BASE_SHA" "$path" "$register"
  case "$REGISTER_STATE" in
    members|absent) base_members="$REGISTER_MEMBERS" ;;
    failed) refuse "could not read ${register} at the merge base of #${number}: ${READ_FAILURE}" ;;
    *) refuse "${register} at the merge base of #${number} is not a membership this gate can read" ;;
  esac
  read_register "$sha" "$path" "$register"
  case "$REGISTER_STATE" in
    members|absent) head_members="$REGISTER_MEMBERS" ;;
    failed) refuse "could not read ${register} at #${number}: ${READ_FAILURE}" ;;
    *) refuse "${register} at #${number} is not a membership this gate can read" ;;
  esac

  for member in $head_members; do
    case " ${base_members} " in
      *" ${member} "*) continue ;;
    esac
    [[ "$claims" == "yes" ]] && return 0
    refuse "#${number} (${sha}) adds '${member}' to ${register} without moving ${name}; a persisted register gaining a member moves the number (hq-6htz case 3, amended 2026-07-30), and comparing values cannot see a member"
  done
  return 0
}

# One constant, across every open head. Returns 3 if it found a collision,
# 0 if it did not; it refuses on its own behalf when it could not look.
check_constant() {
  local name="$1" path="$2" kind="$3" register="$4" caveat="$5"
  local main_value number sha head_ref head_value base_value claims

  read_value "$BASE" "$path" "$kind" "$name"
  case "$READ_STATE" in
    value) main_value="$READ_VALUE" ;;
    absent) refuse "the constant list names ${name} in ${path}, and ${BASE} has no such value there" ;;
    *) refuse_read "$READ_STATE" "${name} in ${path} at ${BASE}" ;;
  esac

  printf '=== %s (%s, %s) ===\n' "$name" "$path" "$kind"
  printf '%s currently: %s\n' "$BASE" "$main_value"
  [[ "$caveat" == "-" ]] || printf 'caveat: %s\n' "$caveat"

  # Real arrays, and every expansion quoted: bash word-splits an unquoted
  # expansion and zsh does not, the seats run zsh, and the difference is
  # silent -- a loop that iterated once over a whole accumulated string
  # printed "none" with the collision on the screen.
  local -a numbers=() shas=() refs=() values=() inheriting=()

  while IFS=$'\t' read -r number sha head_ref; do
    [[ -n "$number" ]] || continue
    merge_base_of "$sha"
    case "$MERGE_BASE_STATE" in
      value) ;;
      absent) refuse "MISSING OBJECT #${number} ${sha} -- ${BASE} and it have no merge base the forge will serve" ;;
      *) refuse "could not read the merge base of #${number} (${sha}): ${READ_FAILURE}" ;;
    esac
    read_value "$sha" "$path" "$kind" "$name"
    case "$READ_STATE" in
      value) head_value="$READ_VALUE" ;;
      absent) refuse "${name} in ${path} is not there at #${number} (${sha}), and ${BASE} has it" ;;
      *) refuse_read "$READ_STATE" "${name} in ${path} at #${number} (${sha})" ;;
    esac
    read_value "$MERGE_BASE_SHA" "$path" "$kind" "$name"
    case "$READ_STATE" in
      value) base_value="$READ_VALUE" ;;
      # Not there at the merge base and there at the head is a head that
      # INTRODUCED the constant. It claims the value it introduced, and goes
      # through the same collision test as any other claim.
      absent) base_value="" ;;
      *) refuse_read "$READ_STATE" "${name} in ${path} at the merge base ${MERGE_BASE_SHA} of #${number}" ;;
    esac

    if [[ "$head_value" == "$base_value" ]]; then
      claims=no
      inheriting+=("#${number}")
    else
      claims=yes
      numbers+=("$number")
      shas+=("$sha")
      refs+=("$head_ref")
      values+=("$head_value")
    fi
    check_register "$number" "$sha" "$head_ref" "$name" "$path" "$register" "$claims"
  done <<<"$HEADS"

  printf -- '--- claims (value differs from its own merge base) ---\n'
  local i
  if [[ ${#numbers[@]} -eq 0 ]]; then
    printf '  none\n'
  else
    for i in "${!numbers[@]}"; do
      printf '  #%s %s %s\n' "${numbers[$i]}" "${shas[$i]}" "${values[$i]}"
    done
  fi
  printf -- '--- inheriting their merge base value (excluded) ---\n'
  if [[ ${#inheriting[@]} -eq 0 ]]; then
    printf '  none\n'
  else
    printf '  %s\n' "${inheriting[*]}"
  fi

  local -a rule_a=() rule_b=() excused=()
  local a b
  for a in "${!numbers[@]}"; do
    for b in "${!numbers[@]}"; do
      [[ $a -lt $b ]] || continue
      [[ "${values[$a]}" == "${values[$b]}" ]] || continue
      ensure_object "${shas[$a]}" "${refs[$a]}" "${numbers[$a]}"
      ensure_object "${shas[$b]}" "${refs[$b]}" "${numbers[$b]}"
      if prefix_related "${shas[$a]}" "${shas[$b]}"; then
        excused+=("#${numbers[$a]} and #${numbers[$b]} both claim ${name}=${values[$a]} but are prefix-related -- OK")
      else
        rule_a+=("COLLISION: #${numbers[$a]} (${shas[$a]}) and #${numbers[$b]} (${shas[$b]}) both claim ${name}=${values[$a]}, neither is an ancestor of the other")
      fi
    done
  done
  for a in "${!numbers[@]}"; do
    [[ "${values[$a]}" == "$main_value" ]] || continue
    rule_b+=("COLLISION: #${numbers[$a]} (${shas[$a]}) claims ${name}=${values[$a]}, which ${BASE} already uses")
  done

  printf -- '--- rule A: two claiming heads, one value, not prefix-related ---\n'
  if [[ ${#rule_a[@]} -eq 0 ]]; then
    printf '  none\n'
  else
    printf '  %s\n' "${rule_a[@]}"
  fi
  if [[ ${#excused[@]} -gt 0 ]]; then
    printf '  %s\n' "${excused[@]}"
  fi
  printf -- '--- rule B: a claiming head whose value %s already uses ---\n' "$BASE"
  if [[ ${#rule_b[@]} -eq 0 ]]; then
    printf '  none\n'
  else
    printf '  %s\n' "${rule_b[@]}"
  fi

  [[ ${#rule_a[@]} -eq 0 && ${#rule_b[@]} -eq 0 ]] && return 0
  return 3
}

# What the run set out to cover, printed BEFORE the first constant is checked.
# A caveat beside a PASS reads the same sentence whether the gate checked two
# constants or none, so the count is the part a reader can act on -- and a run
# that REFUSES partway needs it more than a clean one does, because the reader
# has to know how much of the sweep the refusal cost them. Printed last, it was
# exactly the runs that ended in `refuse` or `bad_usage` that lost it.
#
# The head set arrives as a PHRASE, not a count, because the two enumeration
# refusals have no count to state and "0 open head(s)" beside a failed
# enumeration is the same collapse this gate exists against: a head set nobody
# could read is not an empty one.
print_scope() {
  printf -- '--- scope: %s constant(s) across %s: %s ---\n' \
    "${#CONSTANT_NAMES[@]}" "$1" "${CONSTANT_NAMES[*]}"
}

main() {
  resolve_repo
  CONSTANT_NAMES=() CONSTANT_PATHS=() CONSTANT_KINDS=() CONSTANT_REGISTERS=() CONSTANT_CAVEATS=()
  READ_STATE="" READ_VALUE="" READ_FAILURE="" REGISTER_STATE="" REGISTER_MEMBERS=""
  MERGE_BASE_SHA="" MERGE_BASE_STATE=""
  read_constants
  enumerate_heads
  print_scope "$(printf '%s\n' "$HEADS" | grep -c .) open head(s)"

  local worst=0 rc i
  for i in "${!CONSTANT_NAMES[@]}"; do
    check_constant "${CONSTANT_NAMES[$i]}" "${CONSTANT_PATHS[$i]}" "${CONSTANT_KINDS[$i]}" \
      "${CONSTANT_REGISTERS[$i]}" "${CONSTANT_CAVEATS[$i]}"
    rc=$?
    [[ $rc -eq 0 ]] || worst=$rc
  done
  return $worst
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  parse_args "$@"
  main
  exit $?
fi
