"""The one deterministic "last finished sync" choice, shared by the CLI operator
view and the served status (hy-9vji #404).

Pure, over `SyncRunRecord`s, so both permutations of a tie can be fed in without a
database. The property that matters: the choice does not depend on input order,
so `latest_finished_run` (SQL) and `_get_playground_status` (Python) -- which both
call this -- cannot disagree, even on a double tie.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from hyperset.repositories.dto import SyncRunRecord
from hyperset.repositories.postgres import select_latest_finished

T0 = datetime(2026, 8, 17, 12, 0, 0)


def _run(run_id, *, started, finished, status="succeeded", counters=None):
    return SyncRunRecord(
        id=run_id,
        connection_id="c1",
        mode="full",
        transport=None,
        status=status,
        started_at=started,
        finished_at=finished,
        counters=counters or {},
        checkpoint=None,
        warnings=[],
        errors=[],
    )


def _pick_id(runs):
    chosen = select_latest_finished(list(runs))
    return chosen.id if chosen else None


def test_none_when_no_finished_run():
    running = _run("a", started=T0, finished=None)
    assert select_latest_finished([running]) is None
    assert select_latest_finished([]) is None


def test_latest_finished_at_wins_regardless_of_order():
    old = _run("old", started=T0, finished=T0 + timedelta(minutes=1))
    new = _run("new", started=T0, finished=T0 + timedelta(minutes=2))
    for order in itertools.permutations([old, new]):
        assert _pick_id(order) == "new"


def test_finished_at_tie_keeps_the_earlier_started_run_regardless_of_order():
    # The incumbent served behaviour: on a finished_at tie the earlier-started
    # run wins. Must hold for every input order.
    tie = T0 + timedelta(minutes=5)
    earlier = _run("z-earlier", started=T0, finished=tie)  # id sorts LAST on purpose
    later = _run("a-later", started=T0 + timedelta(minutes=1), finished=tie)
    for order in itertools.permutations([earlier, later]):
        # started_at distinguishes them, so id must NOT decide it -- earlier wins.
        assert _pick_id(order) == "z-earlier"


def test_a_double_tie_is_broken_deterministically_by_id():
    # finished_at AND started_at both equal: the ONLY case the tertiary decides.
    # Whatever the input order, the smallest id wins, so CLI and served agree.
    tie_finished = T0 + timedelta(minutes=5)
    a = _run("aaa", started=T0, finished=tie_finished, counters={"n": 1})
    b = _run("bbb", started=T0, finished=tie_finished, counters={"n": 2})
    picks = {_pick_id(order) for order in itertools.permutations([a, b])}
    assert picks == {"aaa"}, f"double-tie pick is order-dependent: {picks}"
