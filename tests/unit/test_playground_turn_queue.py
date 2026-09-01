"""Server-side FIFO turn queue: ordering, disconnect drop, and running cancel."""

import threading
import time

from playground.ui.app import _TurnQueue


def _spawn(queue, name, order, *, hold=0.05, fail_beat=None):
    beats = {"n": 0}

    def heartbeat(_position):
        beats["n"] += 1
        if fail_beat is not None and beats["n"] >= fail_beat:
            raise ConnectionError("client gone")

    def run():
        try:
            queue.acquire(heartbeat=heartbeat)
        except ConnectionError:
            order.append(f"{name}:dropped")
            return
        order.append(f"{name}:start")
        time.sleep(hold)
        order.append(f"{name}:end")
        queue.release()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_turns_run_one_at_a_time_in_arrival_order():
    queue = _TurnQueue(poll_interval=0.01)
    order: list[str] = []
    threads = []
    for name in ("A", "B", "C"):
        threads.append(_spawn(queue, name, order))
        time.sleep(0.02)  # establish arrival order
    for thread in threads:
        thread.join()

    # No overlap: every start is immediately followed by its own end.
    for name in ("A", "B", "C"):
        assert order.index(f"{name}:end") == order.index(f"{name}:start") + 1
    assert order.index("A:end") < order.index("B:start") < order.index("C:start")


def test_waiter_dropped_on_disconnect_does_not_block_the_queue():
    queue = _TurnQueue(poll_interval=0.01)
    order: list[str] = []
    a = _spawn(queue, "A", order, hold=0.3)  # holds the slot a while
    time.sleep(0.02)
    b = _spawn(queue, "B", order, fail_beat=1)  # disconnects while waiting
    time.sleep(0.02)
    c = _spawn(queue, "C", order)  # must still run after A
    for thread in (a, b, c):
        thread.join()

    assert "B:dropped" in order
    assert "B:start" not in order
    assert order.index("A:end") < order.index("C:start")
    assert order[-1] == "C:end"


def test_release_frees_the_slot_for_the_next_turn():
    queue = _TurnQueue(poll_interval=0.01)
    order: list[str] = []
    a = _spawn(queue, "A", order)
    a.join()  # A done and released
    b = _spawn(queue, "B", order)  # should acquire immediately, not hang
    b.join(timeout=2)
    assert not b.is_alive()
    assert order == ["A:start", "A:end", "B:start", "B:end"]
