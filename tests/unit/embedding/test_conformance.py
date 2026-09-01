"""One suite, two implementations: the boundary is a boundary (hy-gh-206, ADR 0022).

The proof a discovery-facing caller depends on the Protocol and not on a
vendor: the same conformance body passes for the deterministic double and for
the real OpenAI adapter, switched by configuration alone. This is `EmbeddingProvider` what
`ScriptedRuntime` next to `OpenAIAgentsRuntime` is to the agent runtime.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.embedding.openai_embeddings import EmbeddingConfig, OpenAIEmbeddingProvider
from hyperset.embedding.provider import EmbeddingProvider, cosine

WIDTH = 128


def _fake_client(width: int):
    backing = DeterministicEmbeddingProvider(dimensions=width)

    def create(*, model, input, dimensions=None):  # noqa: A002 - the client's own kwarg name
        # A hosted provider now forwards the pinned `dimensions` (hy-zakwj); accept and ignore it
        # (the double answers at its constructed width, which the adapter checks against the pin).
        data = [SimpleNamespace(embedding=list(v)) for v in backing.embed_documents(input)]
        return SimpleNamespace(data=data)

    return SimpleNamespace(embeddings=SimpleNamespace(create=create))


def _deterministic():
    return DeterministicEmbeddingProvider(dimensions=WIDTH)


def _hosted():
    config = EmbeddingConfig(
        provider="openai",
        model="text-embedding-3-small",
        dimensions=WIDTH,
        base_url="https://api.openai.com/v1",
        hosted=True,
        api_key="sk-secret",
    )
    return OpenAIEmbeddingProvider(config, client=_fake_client(WIDTH))


PROVIDERS = [
    pytest.param(_deterministic, id="deterministic"),
    pytest.param(_hosted, id="openai"),
]


@pytest.mark.parametrize("build", PROVIDERS)
def test_satisfies_the_protocol(build):
    assert isinstance(build(), EmbeddingProvider)


@pytest.mark.parametrize("build", PROVIDERS)
def test_width_is_the_declared_width(build):
    provider = build()
    assert len(provider.embed_query("revenue")) == provider.space.dimensions == WIDTH


@pytest.mark.parametrize("build", PROVIDERS)
def test_documents_preserve_count(build):
    assert len(build().embed_documents(["a", "b", "c"])) == 3


@pytest.mark.parametrize("build", PROVIDERS)
def test_deterministic_per_provider(build):
    provider = build()
    assert provider.embed_query("monthly revenue") == provider.embed_query("monthly revenue")


@pytest.mark.parametrize("build", PROVIDERS)
def test_identical_text_is_maximally_similar(build):
    provider = build()
    v = provider.embed_query("gross margin")
    assert cosine(v, v) == pytest.approx(1.0)
