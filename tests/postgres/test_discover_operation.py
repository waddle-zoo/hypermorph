"""The served discover operation, end to end over a real slice (hy-gh-206).

Injects a deterministic double explicitly so no model runs. The product-served
embedding factory remains OpenAI-only. Two things this file exists to prove that
the pure unit tests cannot: discovery ranks the REAL pinned corpus, and running
it changes nothing about the exact resolver's answer.
"""

from __future__ import annotations

import json

import pytest

from hyperset.candidates import service as candidate_service
from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.transport.operations import run_operation, serialize

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
DIRECTIVE = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}


@pytest.fixture(autouse=True)
def _deterministic_embeddings(monkeypatch):
    # The product-served factory is OpenAI-only. This is an explicit test
    # injection so the DB contract test never makes a network request.
    monkeypatch.setattr(
        candidate_service,
        "configured_embedding_provider",
        lambda: DeterministicEmbeddingProvider(),
    )


def _without_clock(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in ("resolved_at", "generated_at")}


@pytest.mark.postgres
def test_discover_serves_ranked_non_authoritative_candidates(session_factory, revenue_slice):
    result = run_operation(
        "discover_analytics_context", {"query": QUESTION}, session_factory=session_factory
    )
    assert result["query"] == QUESTION
    assert "schema_version" in result
    assert result["candidates"], "expected candidates over the revenue slice"
    domains = {c["domain"] for c in result["candidates"]}
    assert "revenue" in domains
    for candidate in result["candidates"]:
        # Non-authoritative by shape: a Git-declared name, a disclosed signal,
        # and nothing governed. No evidence ref, no resolution.
        assert set(candidate) == {"kind", "domain", "term", "provenance", "signal"}
        assert candidate["provenance"] == "assist_ranking"
        signal = candidate["signal"]
        assert "score" in signal and "index_version" in signal and "model" in signal
    blob = json.dumps(result)
    for forbidden in ("instructions", "resolution", "provenance_refs", "context_authority"):
        assert forbidden not in blob


@pytest.mark.postgres
def test_resolve_is_byte_identical_whether_or_not_discovery_ran(session_factory, revenue_slice):
    """Discovery is not in the resolve path: the exact resolver's answer is the
    same bytes whether or not a ranking was computed for the same question. If
    discovery ever reached the bundle, this reds."""
    params = {"query": QUESTION, "directive": DIRECTIVE}
    before = run_operation("resolve_analytics_context", params, session_factory=session_factory)
    run_operation(
        "discover_analytics_context", {"query": QUESTION}, session_factory=session_factory
    )
    after = run_operation("resolve_analytics_context", params, session_factory=session_factory)

    assert before["bundle_id"] == after["bundle_id"]
    assert serialize(_without_clock(before)) == serialize(_without_clock(after))
