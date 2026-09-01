#!/bin/sh
# hy-h08a closing artefact. Five reachability-failure states, measured and then
# ASSERTED OVER BEHAVIOUR rather than over platform or exit code.
#
# Why behaviour: the axis was measured to be the GIT VERSION, not the operating
# system (Darwin 2.39.5 and Debian 2.39.5 agree on every cell; Debian 2.47.3
# differs). Any guard keying on `uname` is keyed to the wrong variable, and any
# guard keying on a bare rc is naming a population rc cannot name.
#
# Exits 0 if every assertion holds at this seat, 1 otherwise. POSIX sh.
set -u
PLAT=$(uname -s); GITVER=$(git --version | sed 's/^git version //')
WORK=$(mktemp -d 2>/dev/null || mktemp -d -t rst)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_AUTHOR_DATE="2020-01-01T00:00:00Z" GIT_COMMITTER_DATE="2020-01-01T00:00:00Z"
FAILED=0

cap() { # cap <var-prefix> <cmd...>  -> sets ${p}_rc and ${p}_err_empty
  p="$1"; shift
  out=$("$@" 2>"$WORK/e"); rc=$?
  if [ -s "$WORK/e" ]; then ee=no; else ee=yes; fi
  eval "${p}_rc=\$rc"; eval "${p}_err_empty=\$ee"; eval "${p}_out=\$out"
}
assert() { # assert <name> <ok:0/1> <detail>
  if [ "$2" -eq 0 ]; then printf 'PASS  %-34s  %s\n' "$1" "$3"
  else printf 'FAIL  %-34s  %s\n' "$1" "$3"; FAILED=1; fi
}
mkrepo() { d="$1"; mkdir -p "$d"; cd "$d" || exit 1; git init -q .
  echo a > f; git add f; git commit -q -m base; }

printf '== seat: %s / git %s ==\n\n' "$PLAT" "$GITVER"

# ---------- shared upstream used by states 4 and 5 ----------
U="$WORK/up"; mkrepo "$U"
BASE=$(git rev-parse HEAD)
echo b > f; git commit -q -am l1; echo b2 > f; git commit -q -am l2; git branch -q left
git checkout -q "$BASE" 2>/dev/null; echo c > g; git add g; git commit -q -m r1; git branch -q right
git symbolic-ref HEAD refs/heads/left 2>/dev/null

# ---------- control: a real common ancestor, and a genuine orphan ----------
O="$WORK/orph"; mkrepo "$O"
OBASE=$(git rev-parse HEAD); echo b > f; git commit -q -am l1; OLEFT=$(git rev-parse HEAD)
git checkout -q --orphan other 2>/dev/null; git rm -rq --cached . 2>/dev/null; rm -f f
echo z > z; git add z; git commit -q -m orphanroot; OORPH=$(git rev-parse HEAD)
git checkout -q --detach "$OLEFT"
cap ctl  git merge-base "$OLEFT" "$OBASE"
cap orph git merge-base "$OLEFT" "$OORPH"
ORPH_SHALLOW=$(git rev-parse --is-shallow-repository)

# ---------- state 1: sha never known, passed as an argument ----------
cap s1 git merge-base "$OLEFT" 0123456789012345678901234567890123456789
# ---------- state 2: ref name that does not exist ----------
cap s2 git merge-base "$OLEFT" no-such-ref-xyz

# ---------- state 3: loose object DELETED but still referenced by refs ----------
D3="$WORK/del"; mkrepo "$D3"
DBASE=$(git rev-parse HEAD); echo b > f; git commit -q -am l; DLEFT=$(git rev-parse HEAD)
git checkout -q "$DBASE" 2>/dev/null; echo c > g; git add g; git commit -q -m r
DRIGHT=$(git rev-parse HEAD); git checkout -q --detach "$DLEFT"
rm -f ".git/objects/$(printf '%s' "$DBASE" | cut -c1-2)/$(printf '%s' "$DBASE" | cut -c3-)"
cap s3   git merge-base "$DLEFT" "$DRIGHT"
cap s3a  git merge-base --is-ancestor "$DLEFT" "$DRIGHT"
cap s3ce git cat-file -e "$DBASE"
cap s3rv git rev-parse --verify -q "$DBASE"

# ---------- state 4: shallow seat, ancestor object ABSENT ----------
C4="$WORK/sh4"
git clone -q --depth 1 --no-local --single-branch --branch left "file://$U" "$C4" 2>/dev/null
cd "$C4" || exit 1
git fetch -q --depth 1 origin right:refs/remotes/origin/right 2>/dev/null
S4_SHALLOW=$(git rev-parse --is-shallow-repository)
cap s4   git merge-base HEAD refs/remotes/origin/right
cap s4a  git merge-base --is-ancestor "$BASE" HEAD
cap s4ce git cat-file -e "$BASE"

# ---------- state 5: shallow seat, ancestor object PRESENT, graft blocks walk ----------
C5="$WORK/sh5"
git clone -q --depth 1 --no-local --single-branch --branch left "file://$U" "$C5" 2>/dev/null
cd "$C5" || exit 1
# deep fetch of the OTHER branch: drags BASE into the object store while the
# walk from `left` stays cut by the graft. Presence without reachability.
git fetch -q --depth 100 origin right:refs/remotes/origin/right 2>/dev/null
S5_SHALLOW=$(git rev-parse --is-shallow-repository)
cap s5   git merge-base refs/heads/left refs/remotes/origin/right
cap s5a  git merge-base --is-ancestor "$BASE" refs/heads/left
cap s5ce git cat-file -e "$BASE"

# ---------- ORACLE: the same question, asked of a COMPLETE clone ----------
# This is what makes state 5 a FALSE negative rather than a true one. Same
# upstream, same two object names, no graft. If this answers 0 and state 5
# answers 1, the disagreement is the defect -- no claim of mine is involved.
C0="$WORK/deep"
git clone -q --no-local "file://$U" "$C0" 2>/dev/null
cd "$C0" || exit 1
cap orc git merge-base --is-ancestor "$BASE" refs/remotes/origin/left
ORC_SHALLOW=$(git rev-parse --is-shallow-repository)

# ================= observations, recorded but NOT asserted =================
printf 'observed (recorded, deliberately not asserted -- these move with the git version):\n'
printf '  state3 merge-base rc=%s  is-ancestor rc=%s\n' "$s3_rc" "$s3a_rc"
printf '  state4 merge-base rc=%s  is-ancestor rc=%s\n' "$s4_rc" "$s4a_rc"
printf '  state5 merge-base rc=%s  is-ancestor rc=%s\n\n' "$s5_rc" "$s5a_rc"

# ================= assertions: behaviour, not platform =================
[ "$ctl_rc" -eq 0 ]; assert control_finds_ancestor $? "a real common ancestor still resolves"

# LOUD populations: they announce themselves on stderr, whatever the rc is.
[ "$s1_err_empty" = no ]; assert state1_argument_is_loud $? "unknown sha as argument writes stderr"
[ "$s2_err_empty" = no ]; assert state2_absent_ref_is_loud $? "absent ref name writes stderr"
[ "$s3_err_empty" = no ]; assert state3_midwalk_is_loud $? "deleted object writes stderr (rc varies by version)"

# SILENT populations: rc=1, nothing on stderr, and MUTUALLY INDISTINGUISHABLE.
sil=0
[ "$orph_rc" -eq 1 ] && [ "$orph_err_empty" = yes ] || sil=1
[ "$s4_rc" -eq 1 ]   && [ "$s4_err_empty" = yes ]   || sil=1
[ "$s5_rc" -eq 1 ]   && [ "$s5_err_empty" = yes ]   || sil=1
assert silent_trio_is_silent $sil "orphan, state4, state5 all rc=1 with empty stderr"

ind=1
[ "$orph_rc" = "$s4_rc" ] && [ "$orph_rc" = "$s5_rc" ] \
  && [ "$orph_err_empty" = "$s4_err_empty" ] && [ "$orph_err_empty" = "$s5_err_empty" ] && ind=0
assert rc_and_stderr_cannot_separate $ind "genuine orphan is byte-identical to both truncated states"

# The separator that DOES work.
sep=1
[ "$ORPH_SHALLOW" = false ] && [ "$S4_SHALLOW" = true ] && [ "$S5_SHALLOW" = true ] && sep=0
assert is_shallow_separates_them $sep "is-shallow-repository: orphan=$ORPH_SHALLOW s4=$S4_SHALLOW s5=$S5_SHALLOW"

# STATE 5 IS THE DEFECT: it does not refuse, it ANSWERS, and the answer is false.
# BASE genuinely IS an ancestor of left. The object is present. is-ancestor says no.
fn=1
[ "$s5ce_rc" -eq 0 ] && [ "$s5a_rc" -eq 1 ] && fn=0
assert state5_is_a_false_negative $fn "object PRESENT (cat-file -e=$s5ce_rc) yet is-ancestor=$s5a_rc on a TRUE ancestor"

orc=1
[ "$orc_rc" -eq 0 ] && [ "$ORC_SHALLOW" = false ] && orc=0
assert oracle_says_it_IS_an_ancestor $orc "complete clone, same two shas: is-ancestor rc=$orc_rc"

dis=1
[ "$orc_rc" -eq 0 ] && [ "$s5a_rc" -eq 1 ] && dis=0
assert truncated_seat_contradicts_oracle $dis "same question: complete=$orc_rc truncated=$s5a_rc"

# ...therefore an existence test is not the pairing to reach for.
[ "$s5ce_rc" -eq 0 ]; assert existence_test_is_defeated $? "cat-file -e returns 0 in the one state that lies"

# rev-parse --verify is a spelling check, never an existence test.
rv=1
[ "$s3rv_rc" -eq 0 ] && [ "$s3rv_out" = "$DBASE" ] && rv=0
assert revparse_verify_is_not_existence $rv "rc=$s3rv_rc and echoes the sha for an absent object"
[ "$s3ce_rc" -ne 0 ]; assert cat_file_e_answers_state3 $? "cat-file -e correctly refuses the deleted object"

# One population, two subcommands, two code paths, two exit codes.
[ "$s4_rc" -ne "$s4a_rc" ]; assert rc_cannot_name_a_population $? \
  "same state4: merge-base rc=$s4_rc vs is-ancestor rc=$s4a_rc"

cd /; rm -rf "$WORK"
printf '\n'
[ $FAILED -eq 0 ] && echo "ALL ASSERTIONS HOLD AT THIS SEAT" || echo "SOME ASSERTIONS FAILED AT THIS SEAT"
exit $FAILED
