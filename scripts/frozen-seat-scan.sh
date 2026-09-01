#!/usr/bin/env bash
#
# frozen-seat-scan.sh
#
# Reports Gas Town seats whose Claude pane shows context use at or above a
# threshold, so a seat that has stopped accepting input is found by a sweep
# rather than by an operator noticing the town went quiet.
#
# A frozen seat is strictly worse than a dead one. A dead seat gets reaped and
# its work re-slung; a frozen seat keeps its bead hooked, its convoy open and
# its branch unpushed, and every nudge sent to it queues into a prompt that will
# never submit -- so the sender's "Nudged, wait-idle" is itself misleading.
# Nothing detected this: `gt status` drew all seven of the seats found on
# 2026-07-30 as live, and `gt patrol scan` looks for `session-dead-active`,
# which they were not. The process is alive and the TUI is still drawing; it
# simply will never accept input again (hy-wqyl, and hy-gh-121 for the freeze
# itself, whose autoCompactWindow mitigation lives in gastown-agent.sh).
#
# THE PANE IS A RENDERING, NOT AN API. This reads pixels-as-text, because the
# number is not exposed anywhere else. Two consequences are load-bearing enough
# to be contract rather than caveat, and both are enforced below:
#
#   1. Absence of a reading is NOT health. The line sits in a rotating hint slot
#      directly above the prompt box -- the same slot that carries "Update
#      available! Run: brew upgrade claude-code". A healthy seat usually shows
#      nothing there. So this reports OVER-THRESHOLD and NO-READING as separate
#      outcomes and never reports a seat as healthy.
#   2. A seat that merely TALKS about context percentages reads as one that is
#      out of context. This was found by hitting it: the first sweep flagged the
#      seat running the sweep, because the grep pattern was echoed into its own
#      pane and matched itself. The calling session is excluded for that reason,
#      which fixes the self-match and nothing else -- a seat discussing another
#      seat's 100% can still be flagged. Over-reporting is the safe direction
#      here: a false alarm costs a glance, a miss costs the fleet.
#
# Requires: tmux, and gt only when the socket has to be discovered.
#
# Usage:
#   ./scripts/frozen-seat-scan.sh                  # sweep, default threshold
#   ./scripts/frozen-seat-scan.sh --threshold 70
#   ./scripts/frozen-seat-scan.sh --socket gt-229ac4
#
# Exit codes are three-way on purpose, because "found frozen seats" and "could
# not look" must not be the same signal to a scheduled caller: 0 nothing at or
# above the threshold, 3 at least one seat flagged, 1 the sweep could not run,
# 2 usage error.

set -uo pipefail

# Well below 100 on purpose. At 100 the seat is already unrecoverable by
# anything but a restart; the point of a threshold is to catch it while `gt
# handoff` can still be typed into a prompt that still submits.
THRESHOLD="${FROZEN_SEAT_THRESHOLD:-85}"
SOCKET="${GASTOWN_TMUX_SOCKET:-}"

usage() {
  cat <<'EOF'
Usage: ./scripts/frozen-seat-scan.sh [--threshold N] [--socket NAME] [--help]

  --threshold N   Flag seats at or above N% context used (default 85).
                  Also settable as FROZEN_SEAT_THRESHOLD.
  --socket NAME   tmux socket holding the Gas Town sessions. Defaults to
                  GASTOWN_TMUX_SOCKET, else asks `gt status --json`.

Prints one "<seat> <percent>" line per flagged seat on stdout and a summary on
stderr. Exit: 0 none flagged, 3 seats flagged, 1 sweep failed, 2 usage.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --threshold)
      [ "$#" -ge 2 ] || { echo "ERROR: --threshold needs a value." >&2; usage >&2; exit 2; }
      THRESHOLD="$2"
      shift 2
      ;;
    --socket)
      [ "$#" -ge 2 ] || { echo "ERROR: --socket needs a value." >&2; usage >&2; exit 2; }
      SOCKET="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Checked rather than assumed numeric: an unvalidated threshold reaches the
# integer comparison at the bottom of the loop, where `[ 100 -ge abc ]` fails
# per seat with a bash error and the sweep reports nothing flagged -- a silent
# all-clear from a broken invocation, which is the exact failure this script
# exists to end.
case "$THRESHOLD" in
  ''|*[!0-9]*) die "threshold must be a whole number, got: $THRESHOLD" ;;
esac

command -v tmux >/dev/null 2>&1 || die "tmux is not installed or not on PATH."

if [ -z "$SOCKET" ]; then
  # The Gas Town sessions do not live on tmux's default socket, so a scheduled
  # run with no TMUX in its environment finds zero sessions and reports a clean
  # town. `gt status --json` carries the socket name; .agents[] does NOT carry
  # the seats (it listed 2 town-level entries while 11 rig seats were running),
  # so it is used for the socket only and the seat list comes from tmux.
  command -v gt >/dev/null 2>&1 || die "no --socket given and gt is not on PATH to discover one."
  SOCKET="$(gt status --json 2>/dev/null | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tmux", {}).get("socket", ""))
except ValueError:
    pass')"
  [ -n "$SOCKET" ] || die "could not read the tmux socket from \`gt status --json\`; pass --socket."
fi

# $TMUX is "<socket path>,<pid>,<session id>", and its socket path may differ
# textually from the resolved name's while pointing at the same socket
# (/private/tmp vs /tmp under macOS), so compare basenames rather than paths.
# Only skip a seat when the sweep is genuinely running on the socket it is
# sweeping; a same-named session on some other socket is a real seat and must
# still be reported.
self_seat=""
if [ -n "${TMUX:-}" ] && [ "$(basename "${TMUX%%,*}")" = "$SOCKET" ]; then
  self_seat="$(tmux display-message -p '#{session_name}' 2>/dev/null)" || self_seat=""
fi

seats="$(tmux -L "$SOCKET" list-sessions -F '#{session_name}' 2>&1)" || \
  die "could not list sessions on tmux socket '$SOCKET': $seats"
[ -n "$seats" ] || die "tmux socket '$SOCKET' has no sessions."

flagged=0
unread=0
while IFS= read -r seat; do
  [ -n "$seat" ] || continue
  if [ "$seat" = "$self_seat" ]; then
    continue
  fi
  # -t <session> captures the active pane of the active window, which is where
  # a seat's Claude runs. A seat parked on some other window reads as no
  # reading, which outcome 1 above already refuses to call healthy.
  pane="$(tmux -L "$SOCKET" capture-pane -p -t "$seat" 2>/dev/null)" || {
    # One unreadable pane must not abort the sweep: the remaining seats are
    # exactly what the caller asked about.
    echo "WARN: could not capture pane for seat '$seat'." >&2
    unread=$((unread + 1))
    continue
  }
  # "Context limit reached" is the terminal state's own wording and carries no
  # number, so it counts as 100 rather than as no reading.
  if printf '%s\n' "$pane" | grep -qF 'Context limit reached'; then
    percent=100
  else
    # Highest reading in the pane, not the last: the pane holds transcript as
    # well as the status slot, and over-reporting is the safe direction.
    percent="$(printf '%s\n' "$pane" \
      | grep -oE '[0-9]{1,3}% context used' \
      | grep -oE '^[0-9]{1,3}' \
      | sort -n \
      | tail -1)"
  fi
  if [ -z "$percent" ]; then
    unread=$((unread + 1))
    continue
  fi
  if [ "$percent" -ge "$THRESHOLD" ]; then
    printf '%s %s\n' "$seat" "$percent"
    flagged=$((flagged + 1))
  fi
done <<< "$seats"

if [ "$flagged" -gt 0 ]; then
  echo "$flagged seat(s) at or above ${THRESHOLD}% context used; $unread with no reading." >&2
  exit 3
fi

echo "No seat at or above ${THRESHOLD}% context used; $unread with no reading (not a health claim)." >&2
exit 0
