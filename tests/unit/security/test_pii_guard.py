"""The local PII guard: fail-closed, redact/block, model-input untouched (hy-hbtz).

Presidio may not be hostable on a seat (the extra, or its spaCy model, absent).
The fail-closed and config arms FORCE the unhostable state (so they run on any
seat, present or not); the redact/block arms SKIP WITH A REASON where Presidio
cannot be hosted and run red-able where it can. Deterministic, no external calls.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hyperset.security import pii
from hyperset.security.pii import (
    PiiBlocked,
    PiiGuardUnavailable,
    guard_text,
    presidio_available,
)

EMAIL_TEXT = "please contact analyst john.doe@example.com about the revenue drift"


def _force_unhostable(monkeypatch):
    """Pin the guard to the can't-host state so the fail-closed arm is exercised
    on any seat, including one where Presidio IS installed."""
    monkeypatch.setattr(pii, "_engines", False)


# --- config / fail-closed (run on any seat) ---


def test_the_guard_is_a_no_op_unless_engaged(monkeypatch):
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    # Default off: the boundary text passes through unchanged, so the library
    # default breaks no existing write path.
    assert guard_text(EMAIL_TEXT, boundary="miss_log") == EMAIL_TEXT


@pytest.mark.parametrize("boundary", ["miss_log", "git_proposal"])
def test_engaged_but_unhostable_fails_closed(monkeypatch, boundary):
    _force_unhostable(monkeypatch)
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    with pytest.raises(PiiGuardUnavailable):
        guard_text(EMAIL_TEXT, boundary=boundary)


def test_presidio_available_is_false_when_unhostable(monkeypatch):
    _force_unhostable(monkeypatch)
    assert presidio_available() is False


def test_find_spec_that_raises_is_tolerated(monkeypatch):
    """`presidio_available()` must not crash when `find_spec` RAISES
    ModuleNotFoundError (find-spec-raises-under-a-blocking-finder); it fails
    closed to False."""

    def _raise(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(importlib.util, "find_spec", _raise)
    monkeypatch.setattr(pii, "_engines", None)
    assert presidio_available() is False


def test_a_missing_model_makes_no_network_call(monkeypatch):
    """hy-wp8e: the capability probe is PURELY LOCAL. With the spaCy model
    absent, `presidio_available()` must fail closed WITHOUT reaching Presidio's
    auto-download (`spacy.cli.download`, a ~400MB network fetch) -- otherwise
    engaging the guard on an internet seat phones home, contradicting its own
    'runs locally, no external call' premise. Requires presidio (hence spaCy)
    installed so the absence is staged, not merely assumed."""
    pytest.importorskip("presidio_analyzer")
    import spacy.cli
    import spacy.util

    monkeypatch.setattr(spacy.util, "is_package", lambda _name: False)  # model absent

    # A RECORDER, not a raise: `_load_engines`'s broad except would SWALLOW a
    # raise and the final bool would pass against the very bug this guards. The
    # violation is that download is REACHED AT ALL, so record the attempt and
    # assert it never happened.
    downloads: list = []
    monkeypatch.setattr(spacy.cli, "download", lambda *args, **kwargs: downloads.append(args))
    monkeypatch.setattr(pii, "_engines", None)

    assert presidio_available() is False  # fails closed
    assert downloads == [], "the guard reached spaCy's model download (network egress)"


# --- redact / block: SKIP WITH REASON where Presidio is not hostable ---


def _require_presidio():
    pytest.importorskip("presidio_analyzer")
    if not presidio_available():
        pytest.skip("presidio is installed but its spaCy model is not hostable on this seat")


def test_redact_replaces_detected_pii(monkeypatch):
    _require_presidio()
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    monkeypatch.setenv("HYPERSET_PII_ACTION", "redact")
    redacted = guard_text(EMAIL_TEXT, boundary="miss_log")
    assert "john.doe@example.com" not in redacted
    assert "revenue drift" in redacted  # non-PII text preserved


def test_block_raises_on_pii(monkeypatch):
    _require_presidio()
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    monkeypatch.setenv("HYPERSET_PII_ACTION", "block")
    with pytest.raises(PiiBlocked):
        guard_text(EMAIL_TEXT, boundary="git_proposal")


# --- the model-input path is UNTOUCHED (ADR 0019 exclusion) ---


def test_no_model_input_module_imports_the_guard():
    """The guard sits on two WRITE-BACKS, never between the question and the
    assist model. The planner loop and the resolver -- the model-input path --
    must not import it."""
    for module in ("hyperset/planner/loop.py", "hyperset/bundle/resolver.py"):
        source = Path(module).read_text()
        assert "security.pii" not in source
        assert "guard_text" not in source


def test_the_assist_prompt_still_carries_the_original_question(monkeypatch):
    """Even with the guard engaged, the question reaches the model verbatim --
    the guard never redacts a model input (Overseer exclusion)."""
    from hyperset.planner.loop import plan_analytics_context
    from hyperset.planner.runtime import ScriptedRuntime

    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")

    class _NeverCalled:
        def call(self, operation, params):  # pragma: no cover - script issues no tool call
            raise AssertionError("the scripted run issues no tool call")

    trace = plan_analytics_context(
        EMAIL_TEXT,
        runtime=ScriptedRuntime(script=["I have drafted nothing."]),
        executor=_NeverCalled(),
    )
    assert trace.question == EMAIL_TEXT
    assert "john.doe@example.com" in trace.question


def test_the_module_imports_clean_without_a_collection_error():
    """Importing the guard costs no collection error: the SDK is imported lazily
    inside the probe, never at module scope."""
    assert importlib.util.find_spec("hyperset.security.pii") is not None
