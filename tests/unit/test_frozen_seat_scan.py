"""The frozen-seat sweep, driven through a stubbed `tmux`.

The script reads a terminal rendering, so the contract under test is what it
does with pane text: which seats it flags, which it refuses to call healthy,
and which it must not read at all. Each test therefore supplies real pane
transcripts to a `tmux` stub on PATH and asserts on the script's own stdout,
stderr and exit code, which is the whole interface a scheduled caller sees.

Pane text here is copied from real seats captured on 2026-07-30, including the
right-aligned column the indicator renders in.
"""

from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "./scripts/frozen-seat-scan.sh"

# Exit codes are three-way on purpose: "found frozen seats" and "could not
# look" must not read the same to a scheduled caller.
NONE_FLAGGED = 0
SWEEP_FAILED = 1
USAGE_ERROR = 2
SEATS_FLAGGED = 3

# A seat that has stopped accepting input. The indicator sits in the hint slot
# directly above the prompt box.
FROZEN_PANE = """\
* Accomplishing... (1m 30s * 4.6k tokens * thinking with high effort)
  Tip: Use /btw to ask a quick side question without interrupting Claude's
                                                              100% context used
--------------------------------------------------------------------------------
>
--------------------------------------------------------------------------------
  bypass permissions on (shift+tab to cycle) * esc to interrupt
"""

# A working seat. The same slot carries something else entirely, which is why
# a missing reading cannot be read as health.
HEALTHY_PANE = """\
  tell me the mechanism. How do I recycle a frozen polecat here?

* Sauteed for 41s
                                Update available! Run: brew upgrade claude-code
--------------------------------------------------------------------------------
>
--------------------------------------------------------------------------------
  manual mode on * ? for shortcuts
"""

TMUX_STUB = r"""#!/usr/bin/env bash
# Stands in for tmux. Seats and their panes come from files in $STUB_DIR so a
# test states only the town it needs.
args=("$@")
if [ "${args[0]}" = "-L" ]; then
  printf '%s\n' "${args[1]}" > "$STUB_DIR/socket-seen"
  args=("${args[@]:2}")
fi
case "${args[0]}" in
  list-sessions)
    if [ -f "$STUB_DIR/list-fails" ]; then
      echo "no server running on /tmp/tmux-501/nope" >&2
      exit 1
    fi
    cat "$STUB_DIR/seats"
    ;;
  capture-pane)
    seat=""
    for i in "${!args[@]}"; do
      if [ "${args[$i]}" = "-t" ]; then seat="${args[$((i + 1))]}"; fi
    done
    if [ -f "$STUB_DIR/panes/$seat" ]; then
      cat "$STUB_DIR/panes/$seat"
    else
      echo "can't find pane: $seat" >&2
      exit 1
    fi
    ;;
  display-message)
    cat "$STUB_DIR/self-seat" 2>/dev/null || exit 1
    ;;
esac
"""

GT_STUB = r"""#!/usr/bin/env bash
cat "$STUB_DIR/gt-status.json"
"""


def _town(tmp_path, seats, *, self_seat=None, gt_status=None):
    """Build a stubbed town. `seats` maps seat name to pane text, or to None
    for a seat whose pane cannot be captured."""
    stub_dir = tmp_path / "stubs"
    bin_dir = tmp_path / "bin"
    (stub_dir / "panes").mkdir(parents=True)
    bin_dir.mkdir()
    for name, body in (("tmux", TMUX_STUB), ("gt", GT_STUB)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    (stub_dir / "seats").write_text("".join(f"{seat}\n" for seat in seats))
    for seat, pane in seats.items():
        if pane is not None:
            (stub_dir / "panes" / seat).write_text(pane)
    if self_seat is not None:
        (stub_dir / "self-seat").write_text(f"{self_seat}\n")
    (stub_dir / "gt-status.json").write_text(
        gt_status if gt_status is not None else '{"tmux": {"socket": "gt-discovered"}}'
    )
    return stub_dir, bin_dir


def _run(stub_dir, bin_dir, tmp_path, *args, tmux_env=None):
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "STUB_DIR": str(stub_dir),
    }
    if tmux_env is not None:
        env["TMUX"] = tmux_env
    return run(
        [SCRIPT, *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )


def test_the_sweep_finds_a_seat_stopped_at_full_context(tmp_path):
    """The case the bead was filed for: seven seats reported healthy while
    frozen. One sweep has to name them."""
    stub_dir, bin_dir = _town(
        tmp_path,
        {"hy-capable": FROZEN_PANE, "hy-witness": HEALTHY_PANE, "hy-slit": FROZEN_PANE},
    )
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout.splitlines() == ["hy-capable 100", "hy-slit 100"]


def test_a_seat_with_no_reading_is_not_reported_as_healthy(tmp_path):
    """The indicator shares a rotating hint slot with 'Update available!', so a
    healthy-looking pane usually carries no number at all. Reporting that as
    health would recreate the gap this script closes."""
    stub_dir, bin_dir = _town(tmp_path, {"hy-witness": HEALTHY_PANE})
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == NONE_FLAGGED
    assert result.stdout == ""
    assert "1 with no reading" in result.stderr
    assert "not a health claim" in result.stderr


def test_the_terminal_wording_counts_as_full_even_though_it_has_no_number(tmp_path):
    """'Context limit reached' is the frozen state's own wording and carries no
    percentage. Matching only digits would miss the seats furthest gone."""
    pane = "Context limit reached - /compact or /clear to continue\n>\n"
    stub_dir, bin_dir = _town(tmp_path, {"hy-toast": pane})
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout == "hy-toast 100\n"


def test_the_threshold_leaves_room_to_hand_off_before_the_seat_is_lost(tmp_path):
    """A seat at 88% still submits a prompt, so it can still `gt handoff`. That
    is the whole point of a threshold below 100."""
    pane = "                                                     88% context used\n"
    stub_dir, bin_dir = _town(tmp_path, {"hy-atlas": pane})
    flagged = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert flagged.returncode == SEATS_FLAGGED
    assert flagged.stdout == "hy-atlas 88\n"
    quiet = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test", "--threshold", "90")
    assert quiet.returncode == NONE_FLAGGED
    assert quiet.stdout == ""


def test_the_highest_reading_in_the_pane_wins(tmp_path):
    """The pane holds transcript as well as the status slot, so a lower number
    can sit BELOW the one that matters. Ordered that way on purpose: a pane
    with the high reading last cannot tell "highest" from "last" and passes
    either way. Over-reporting is the safe direction here -- a false alarm
    costs a glance, a miss costs the fleet."""
    pane = "                          100% context used\n  and later, 40% context used\n"
    stub_dir, bin_dir = _town(tmp_path, {"hy-dementus": pane})
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout == "hy-dementus 100\n"


def test_the_sweep_does_not_flag_the_seat_running_it(tmp_path):
    """Found by hitting it: the first live sweep flagged its own seat, because
    the grep pattern was echoed into that pane and matched itself."""
    stub_dir, bin_dir = _town(
        tmp_path,
        {"hy-valkyrie": FROZEN_PANE, "hy-slit": FROZEN_PANE},
        self_seat="hy-valkyrie",
    )
    result = _run(
        stub_dir,
        bin_dir,
        tmp_path,
        "--socket",
        "gt-test",
        tmux_env="/private/tmp/tmux-501/gt-test,11300,1895",
    )
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout == "hy-slit 100\n"


def test_a_same_named_seat_on_another_socket_is_still_swept(tmp_path):
    """Self-exclusion is scoped to the socket being swept. A seat that merely
    shares a name with the caller's own is a real seat and must be reported."""
    stub_dir, bin_dir = _town(
        tmp_path,
        {"hy-valkyrie": FROZEN_PANE},
        self_seat="hy-valkyrie",
    )
    result = _run(
        stub_dir,
        bin_dir,
        tmp_path,
        "--socket",
        "gt-test",
        tmux_env="/private/tmp/tmux-501/some-other-socket,11300,1895",
    )
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout == "hy-valkyrie 100\n"


def test_one_unreadable_pane_does_not_abort_the_sweep(tmp_path):
    """The remaining seats are exactly what the caller asked about."""
    stub_dir, bin_dir = _town(
        tmp_path,
        {"hy-gone": None, "hy-slit": FROZEN_PANE},
    )
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == SEATS_FLAGGED
    assert result.stdout == "hy-slit 100\n"
    assert "could not capture pane for seat 'hy-gone'" in result.stderr


def test_a_socket_that_cannot_be_listed_is_not_reported_as_a_quiet_town(tmp_path):
    """Exit 1, not 0. A sweep that could not look must not read as all-clear."""
    stub_dir, bin_dir = _town(tmp_path, {"hy-slit": FROZEN_PANE})
    (stub_dir / "list-fails").write_text("")
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test")
    assert result.returncode == SWEEP_FAILED
    assert "could not list sessions" in result.stderr


def test_a_non_numeric_threshold_is_refused_rather_than_swept_with(tmp_path):
    """Unvalidated, it reaches the integer comparison, fails per seat with a
    bash error, and the run reports nothing flagged -- a silent all-clear from
    a broken invocation, which is the failure this script exists to end."""
    stub_dir, bin_dir = _town(tmp_path, {"hy-slit": FROZEN_PANE})
    result = _run(stub_dir, bin_dir, tmp_path, "--socket", "gt-test", "--threshold", "high")
    assert result.returncode == SWEEP_FAILED
    assert "threshold must be a whole number" in result.stderr
    assert result.stdout == ""


def test_a_flag_missing_its_value_is_a_usage_error(tmp_path):
    stub_dir, bin_dir = _town(tmp_path, {"hy-slit": FROZEN_PANE})
    result = _run(stub_dir, bin_dir, tmp_path, "--threshold")
    assert result.returncode == USAGE_ERROR
    assert result.stdout == ""


def test_the_socket_is_discovered_when_none_is_given(tmp_path):
    """Gas Town sessions do not live on tmux's default socket, so a scheduled
    run with no TMUX in its environment would otherwise find no sessions and
    report a clean town."""
    stub_dir, bin_dir = _town(tmp_path, {"hy-slit": FROZEN_PANE})
    result = _run(stub_dir, bin_dir, tmp_path)
    assert result.returncode == SEATS_FLAGGED
    assert (stub_dir / "socket-seen").read_text().strip() == "gt-discovered"


def test_an_unreadable_gt_status_does_not_become_a_default_socket(tmp_path):
    """Falling back to tmux's default socket here would sweep an empty server
    and call the town quiet."""
    stub_dir, bin_dir = _town(tmp_path, {"hy-slit": FROZEN_PANE}, gt_status="not json")
    result = _run(stub_dir, bin_dir, tmp_path)
    assert result.returncode == SWEEP_FAILED
    assert "could not read the tmux socket" in result.stderr
