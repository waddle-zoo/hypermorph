#!/usr/bin/env bash
# A DETACHED, persistent runner for the refinery poll (hy-wpqa).
#
# The autonomous poll-merger is only autonomous if something keeps calling it. A
# Claude crew seat cannot: it reports "polling" but does not reliably re-invoke
# the loop across context-cycling, so PR #305 -- every gate clear -- sat OPEN for
# >2 intervals and never landed. This wrapper runs `refinery_poll.py --interval`
# as a background process detached from the seat's terminal (nohup ignores the
# SIGHUP a closing seat sends), with a pidfile so it is single-instance and a
# logfile so every pass is inspectable after the fact.
#
#   HYPERSET_CRITIC_LOGINS=bsovs scripts/refinery_poll_daemon.sh start
#   scripts/refinery_poll_daemon.sh status      # running? + last log lines
#   scripts/refinery_poll_daemon.sh once        # one foreground pass (diagnostic)
#   scripts/refinery_poll_daemon.sh dry-run     # one pass, decisions logged, NO merge
#   scripts/refinery_poll_daemon.sh stop
#   scripts/refinery_poll_daemon.sh tail        # follow the log
#
# It REQUIRES HYPERSET_CRITIC_LOGINS to be exported before `start`: the #305 stall
# was that this was unset, so every verdict was untrusted and every PR skipped.
# The daemon refuses to start blind rather than poll uselessly. For reboot-level
# persistence, wrap this in launchd/systemd; nohup survives a seat, not a reboot.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${HYPERSET_REFINERY_STATE:-$HOME/.hyperset}"
PIDFILE="$STATE_DIR/refinery_poll.pid"
LOGFILE="$STATE_DIR/refinery_poll.log"
INTERVAL="${HYPERSET_POLL_INTERVAL:-300}"

mkdir -p "$STATE_DIR"

_running_pid() {
  # Echo the live daemon PID, or nothing. A stale pidfile (process gone) is not
  # "running" -- checked with kill -0, so a crash does not wedge `start` forever.
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { echo "$pid"; return 0; }
  return 1
}

_require_logins() {
  if [ -z "${HYPERSET_CRITIC_LOGINS:-}" ]; then
    echo "REFUSING to start: HYPERSET_CRITIC_LOGINS is unset." >&2
    echo "Every verdict would be untrusted and every PR would skip (the #305 stall)." >&2
    echo "Export it first, e.g.  HYPERSET_CRITIC_LOGINS=bsovs $0 start" >&2
    exit 2
  fi
}

cmd_start() {
  _require_logins
  if pid="$(_running_pid)"; then
    echo "already running (pid $pid); log: $LOGFILE"
    return 0
  fi
  cd "$REPO"
  # nohup detaches from the seat's controlling terminal; the SIGHUP a closing seat
  # sends is ignored, so the loop outlives the seat. Env (incl. the required
  # HYPERSET_CRITIC_LOGINS) is inherited by the child.
  nohup uv run python scripts/refinery_poll.py --interval "$INTERVAL" >>"$LOGFILE" 2>&1 &
  local pid=$!
  echo "$pid" >"$PIDFILE"
  disown "$pid" 2>/dev/null || true
  echo "started refinery poll (pid $pid, interval ${INTERVAL}s); log: $LOGFILE"
}

cmd_stop() {
  if pid="$(_running_pid)"; then
    kill "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped (pid $pid)"
  else
    rm -f "$PIDFILE"
    echo "not running"
  fi
}

cmd_status() {
  if pid="$(_running_pid)"; then
    echo "RUNNING (pid $pid, interval ${INTERVAL}s)"
  else
    echo "NOT RUNNING"
  fi
  echo "log: $LOGFILE"
  if [ -f "$LOGFILE" ]; then
    echo "--- last 15 log lines ---"
    tail -n 15 "$LOGFILE"
  fi
  return 0
}

cmd_once() {
  _require_logins
  cd "$REPO"
  exec uv run python scripts/refinery_poll.py
}

cmd_dry_run() {
  cd "$REPO"
  exec uv run python scripts/refinery_poll.py --dry-run
}

cmd_tail() {
  [ -f "$LOGFILE" ] || { echo "no log yet: $LOGFILE"; exit 0; }
  exec tail -f "$LOGFILE"
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  once)    cmd_once ;;
  dry-run) cmd_dry_run ;;
  tail)    cmd_tail ;;
  *)
    echo "usage: $0 {start|stop|restart|status|once|dry-run|tail}" >&2
    exit 2
    ;;
esac
