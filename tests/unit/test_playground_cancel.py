"""A disconnected turn cancels the streamed run instead of draining it.

Provider-agnostic: the agent SDK's ``cancel()`` is what closes the upstream
streaming connection (Bedrock/OpenAI stop server-side on close). This asserts we
call it and stop consuming the moment the client is gone, rather than reading the
stream to the end.
"""

import asyncio
import inspect
from pathlib import Path

import agents
import pytest

from playground.ui import app

# playground/ui/app.py -> playground/ui -> playground -> repo root.
_REPO_ROOT = Path(app.__file__).resolve().parents[2]
CHAT_UI_INDEX = _REPO_ROOT / "packages" / "chat-ui" / "src" / "index.jsx"


def test_consumer_disconnect_cancels_run_and_stops_consuming(monkeypatch):
    state = {"cancelled": False, "client_closed": False, "consumed": 0}

    class Client:
        async def close(self):
            state["client_closed"] = True

    class Agent:
        class model:
            _client = Client()

    class FakeStreamed:
        async def stream_events(self):
            for index in range(1000):
                state["consumed"] += 1
                yield {"index": index}

        def cancel(self):
            state["cancelled"] = True

    monkeypatch.setattr(agents.Runner, "run_streamed", staticmethod(lambda *a, **k: FakeStreamed()))

    def on_event(_event):
        raise ConnectionError("client gone")  # the SSE emit fails on a dead socket

    with pytest.raises(ConnectionError):
        app._run_agent_with_budget(Agent(), "prompt", on_event=on_event)

    assert state["cancelled"] is True, "streamed run must be cancelled on disconnect"
    assert state["client_closed"] is True, "provider client must close before its loop"
    assert state["consumed"] == 1, "must stop consuming immediately, not drain the stream"


def test_nonstreaming_run_closes_provider_client_before_its_loop(monkeypatch):
    state = {"closed": False, "loop": None}

    class Client:
        async def close(self):
            assert asyncio.get_running_loop() is state["loop"]
            state["closed"] = True

    class Agent:
        class model:
            _client = Client()

    async def run(_agent, _prompt, *, max_turns):
        assert max_turns == 2
        state["loop"] = asyncio.get_running_loop()
        return "done"

    monkeypatch.setattr(agents.Runner, "run", run)

    assert app._run_agent_sync(Agent(), "prompt", max_turns=2) == "done"
    assert state["closed"] is True
    assert state["loop"].is_closed()


def test_stop_copy_matches_the_uncancellable_discovery_path():
    """The stop control aborts the browser fetch; it does not stop the server.

    The answer stream cancels server-side on disconnect (the test above proves
    it), but context DISCOVERY runs through a blocking ``_run_agent_sync`` wrapper with
    no cancellation hook, so a client stop cannot halt discovery already in
    flight -- the server finishes it. The stop-affordance copy must disclose
    that rather than claim to stop the whole turn (hy-csr9, #385).

    This binds the copy to the behaviour in both directions: reverting the copy
    to an unqualified "stop this turn" reddens the copy asserts, and making
    discovery cancellation-aware (adding a ``.cancel()`` path) reddens the
    behaviour assert so the disclosure gets revisited.
    """
    # Behaviour: discovery is a blocking run_sync wrapper with nothing to cancel.
    selector_src = inspect.getsource(app._run_context_selector)
    assert "_run_agent_sync" in selector_src, (
        "discovery is expected to use the blocking synchronous wrapper"
    )
    assert ".cancel(" not in selector_src, (
        "discovery gained a cancellation path -- revisit the stop copy, which currently "
        "discloses that in-flight server-side discovery keeps running"
    )
    # The answer stream, by contrast, cancels on disconnect.
    assert ".cancel(" in inspect.getsource(app._run_agent_with_budget)

    copy = CHAT_UI_INDEX.read_text(encoding="utf-8")
    # No bare whole-turn-stop claim remains on any affordance.
    assert "You can stop this turn below" not in copy
    assert ">Stopped by you.<" not in copy
    assert 'aria-label="Stop the running turn"' not in copy
    assert 'title="Stop the running turn"' not in copy
    # Every affordance discloses the server-side continuation of discovery.
    assert "keeps going on the server" in copy  # patience notice + button title
    assert "may still finish on the server" in copy  # the cancelled-message notice
