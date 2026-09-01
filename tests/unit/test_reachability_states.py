"""The reachability-states measurement artefact, landed in-tree and RUN (hy-nijz).

`scripts/reachability_states.sh` is critic's closing artefact for hy-h08a: it
builds the five git reachability-failure states plus two controls in one run and
asserts over BEHAVIOUR, never over `uname` or a git-version literal. It exists in
the tree rather than as a doc because every claim in it is git-version- and
topology-dependent, and a document cannot detect the day one stops being true --
this script reddens.

Running it here is what gives it teeth: the day a cell it asserts changes (an
upstream git fix to the shallow/oracle contradiction, say), the gate goes red and
the arm is the finding, not the seat. It is version-safe to gate: the one cell
that moves across the git-version flip (state3's rc) is recorded and asserted
nowhere, and `rc_cannot_name_a_population` asserts that state4's merge-base rc
DIFFERS from its is-ancestor rc rather than pinning 1 vs 128 -- so a newer git
does not redden it. critic verified 14 PASS on Darwin 2.39.5, Linux 2.39.5, and
Linux 2.47.3.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reachability_states.sh"

# The pinned identity of the artefact (hy-nijz). The mutation control that
# validates the harness asserts `sha1 unchanged`, and the bead is explicit that
# state3 must stay recorded-not-asserted and `rc_cannot_name_a_population` must
# assert a difference rather than the literals -- properties a silent "tidy" edit
# would erase. This pin makes such an edit red rather than quiet; a deliberate
# revision updates it together with a re-verification of the 14 arms.
_PINNED_SHA1 = "732a4d830de95c2b3098f92ce7d4993f071045c5"


def test_reachability_states_holds_on_this_seat():
    """The 14 arms pass here, whatever git version this seat runs."""
    result = subprocess.run(["sh", str(_SCRIPT)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL ASSERTIONS HOLD AT THIS SEAT" in result.stdout
    # No arm reddened; the FAIL prefix is what a broken arm prints.
    assert "FAIL" not in result.stdout


def test_reachability_states_is_the_pinned_artefact():
    """Byte-identical to critic's measured artefact (sha1 732a4d8). A change that
    moves this hash is either a tidy that erased a property the bead protects, or
    a deliberate revision that must re-verify the 14 arms and move the pin with
    intent -- never silently."""
    digest = hashlib.sha1(_SCRIPT.read_bytes()).hexdigest()
    assert digest == _PINNED_SHA1, f"artefact changed: {digest}"
