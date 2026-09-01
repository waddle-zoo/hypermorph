"""The served discover operation's service: provider choice + result shape (hy-gh-206).

No database: the corpus reader is monkeypatched to a fixed corpus so the
service logic -- provider selection, ranking, and the non-authoritative result
shape -- is tested without Postgres. The end-to-end served path over a real DB
lives in tests/postgres.
"""

from __future__ import annotations

import pytest

from hyperset.candidates import service
from hyperset.candidates.catalog import DomainFacts
from hyperset.candidates.service import (
    DISCOVER_OPERATION,
    DiscoveryResult,
    configured_embedding_provider,
    discover_analytics_context,
)
from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.embedding.openai_embeddings import (
    EmbeddingProviderMisconfigured,
    OpenAIEmbeddingProvider,
)

CORPUS = (
    DomainFacts("revenue", "Revenue", ("recognized revenue", "churn"), "c0ffee", "cs-rev"),
    DomainFacts("habitat", "Penguin Habitat", ("ice shelf",), "deadbee", "cs-hab"),
)


@pytest.fixture(autouse=True)
def _fixed_corpus(monkeypatch):
    monkeypatch.setattr(
        service, "read_catalog_corpus", lambda *, session_factory, workspace=None: list(CORPUS)
    )


def test_the_name_is_the_fourth_operation():
    assert DISCOVER_OPERATION == "discover_analytics_context"


def test_deterministic_env_is_refused_by_the_served_factory(monkeypatch):
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "deterministic")
    with pytest.raises(EmbeddingProviderMisconfigured, match="OpenAI embeddings"):
        configured_embedding_provider()


def test_the_default_is_openai_and_requires_a_key(monkeypatch):
    for var in (
        "HYPERSET_EMBEDDING_PROVIDER",
        "HYPERSET_EMBEDDING_BASE_URL",
        "HYPERSET_EMBEDDING_MODEL",
        "HYPERSET_EMBEDDING_API_KEY",
        "HYPERSET_EMBEDDING_DIMENSIONS",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(EmbeddingProviderMisconfigured):
        configured_embedding_provider()

    monkeypatch.setenv("HYPERSET_EMBEDDING_API_KEY", "sk-test-embed")
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "768")
    provider = configured_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert "api.openai.com" in str(provider._client.base_url)
    assert provider.space.model == "text-embedding-3-small"


def test_a_retired_local_provider_is_refused(monkeypatch):
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "ollama")
    with pytest.raises(EmbeddingProviderMisconfigured, match="OpenAI embeddings"):
        configured_embedding_provider()


def test_an_openai_label_cannot_route_embeddings_to_an_ollama_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("HYPERSET_EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("HYPERSET_EMBEDDING_API_KEY", "sk-test-embed")
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "768")
    with pytest.raises(EmbeddingProviderMisconfigured, match="api.openai.com"):
        configured_embedding_provider()


def test_a_hosted_provider_without_a_secret_is_refused(monkeypatch):
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("HYPERSET_EMBEDDING_API_KEY", raising=False)
    with pytest.raises(EmbeddingProviderMisconfigured):
        configured_embedding_provider()


def test_openai_provider_with_the_wired_key_builds_a_hosted_provider(monkeypatch):
    # hy-8vm34: provider=openai + HYPERSET_EMBEDDING_API_KEY (the var compose now passes to
    # BOTH api and mcp-http) builds a HOSTED OpenAI embedding provider pointing at the OpenAI
    # base and model -- the value that was refused above when the key was absent. This is what
    # makes discover/search rank on OpenAI embeddings instead of the ollama default.
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("HYPERSET_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("HYPERSET_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("HYPERSET_EMBEDDING_API_KEY", "sk-test-embed")
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "768")
    provider = configured_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert "api.openai.com" in str(provider._client.base_url)
    assert "localhost" not in str(provider._client.base_url)
    assert provider.space.model == "text-embedding-3-small"


def test_the_configured_dimensions_reach_the_pinned_space(monkeypatch):
    # hy-zakwj: HYPERSET_EMBEDDING_DIMENSIONS pins the space width the hosted adapter asks
    # for, so an OpenAI model returning 1536-d by default is requested at 768 (matching an
    # index Ollama built at 768) instead of 500ing. A distinct 512 proves it is wired, not
    # the coincidental 768 default.
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("HYPERSET_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("HYPERSET_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("HYPERSET_EMBEDDING_API_KEY", "sk-test-embed")
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "512")
    provider = configured_embedding_provider()
    assert provider.space.dimensions == 512


def test_unset_dimensions_is_refused(monkeypatch):
    # The embedding width is part of the index identity and must be explicit.
    for var in ("HYPERSET_EMBEDDING_PROVIDER", "HYPERSET_EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_EMBEDDING_API_KEY", "sk-test-embed")
    with pytest.raises(EmbeddingProviderMisconfigured, match="DIMENSIONS"):
        configured_embedding_provider()


def test_discover_returns_ranked_candidates_and_nothing_governed():
    result = discover_analytics_context(
        question="recognized revenue",
        session_factory=object(),
        provider=DeterministicEmbeddingProvider(dimensions=128),
    )
    assert isinstance(result, DiscoveryResult)
    payload = result.to_dict()
    assert payload["query"] == "recognized revenue"
    assert set(payload) == {"schema_version", "generated_at", "query", "candidates"}
    assert payload["candidates"], "expected candidates"
    for candidate in payload["candidates"]:
        assert set(candidate) == {"kind", "domain", "term", "provenance", "signal"}
        assert candidate["provenance"] == "assist_ranking"
    # A ranking is not an answer: no governed section reaches the result.
    blob = repr(payload)
    for forbidden in ("instructions", "resolution", "provenance_refs", "context_authority"):
        assert forbidden not in blob


def test_an_empty_catalog_does_not_construct_an_embedding_provider(monkeypatch):
    monkeypatch.setattr(
        service, "read_catalog_corpus", lambda *, session_factory, workspace=None: []
    )
    monkeypatch.setattr(
        service,
        "configured_embedding_provider",
        lambda: pytest.fail("empty discovery must not construct a provider"),
    )

    result = discover_analytics_context(question="anything", session_factory=object())

    assert result.candidates == []


def test_the_relevant_domain_ranks_above_the_irrelevant_one():
    result = discover_analytics_context(
        question="recognized revenue",
        session_factory=object(),
        provider=DeterministicEmbeddingProvider(dimensions=128),
    )
    domains = [c for c in result.candidates if c["kind"] == "domain"]
    ranks = {c["domain"]: i for i, c in enumerate(domains)}
    assert ranks["revenue"] < ranks["habitat"]
