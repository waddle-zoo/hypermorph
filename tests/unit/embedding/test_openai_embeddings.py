"""The OpenAI-compatible adapter: the pin, the opt-in, the width check (hy-gh-206).

No network. A fake client injected through the constructor seam returns
vectors of a chosen width, which is all this file needs to prove the
projection is applied, the live width is checked against the pin, the hosted
gate refuses without a secret, and no secret reaches disclosed metadata.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.embedding.openai_embeddings import (
    EmbeddingConfig,
    EmbeddingDimensionMismatch,
    EmbeddingProviderMisconfigured,
    OpenAIEmbeddingProvider,
)
from hyperset.embedding.provider import EmbeddingProvider, IndexVersion

_UNSET = object()


class _FakeClient:
    """Records the inputs it was asked to embed and answers with `width`-d
    vectors from the deterministic double, so a test can both inspect the
    projection and choose the returned width. Also records the `dimensions`
    kwarg (its sentinel `_UNSET` when the adapter did not send one) so a test
    can prove the hosted path asks for a width and the local path does not."""

    def __init__(self, width: int) -> None:
        self.inputs: list[str] = []
        self.dimensions_arg: object = _UNSET
        self._backing = DeterministicEmbeddingProvider(dimensions=width)
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, *, model, input, dimensions=_UNSET):  # noqa: A002 - the client's kwarg
        self.inputs.extend(input)
        self.dimensions_arg = dimensions
        data = [SimpleNamespace(embedding=list(v)) for v in self._backing.embed_documents(input)]
        return SimpleNamespace(data=data)


def _config(dimensions: int = 8, **overrides) -> EmbeddingConfig:
    values = {
        "model": "text-embedding-3-small",
        "dimensions": dimensions,
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "hosted": True,
        "api_key": "sk-test",
    }
    values.update(overrides)
    return EmbeddingConfig(**values)


def test_the_adapter_requires_an_explicit_deployment_configuration():
    with pytest.raises(TypeError):
        EmbeddingConfig()


def test_a_none_base_url_is_refused_not_defaulted_to_hosted():
    with pytest.raises(EmbeddingProviderMisconfigured):
        _config(base_url=None)


def test_a_hosted_provider_with_no_secret_is_refused_at_construction():
    with pytest.raises(EmbeddingProviderMisconfigured):
        _config(api_key=None)


def test_a_hosted_provider_with_a_secret_is_allowed():
    config = _config(api_key="sk-secret")
    assert config.hosted is True


def test_the_adapter_satisfies_the_boundary():
    provider = OpenAIEmbeddingProvider(_config(), client=_FakeClient(8))
    assert isinstance(provider, EmbeddingProvider)


def test_the_projection_prefixes_reach_the_client():
    fake = _FakeClient(8)
    config = _config(document_prefix="search_document: ", query_prefix="search_query: ")
    provider = OpenAIEmbeddingProvider(config, client=fake)
    provider.embed_documents(["revenue"])
    provider.embed_query("what is revenue")
    assert fake.inputs == ["search_document: revenue", "search_query: what is revenue"]


def test_a_wrong_width_from_the_model_is_refused_not_ranked():
    # Configured space is 768-d; the live model returns 8-d. A different width
    # is a different space and must not be mixed into this index.
    provider = OpenAIEmbeddingProvider(_config(dimensions=768), client=_FakeClient(8))
    with pytest.raises(EmbeddingDimensionMismatch):
        provider.embed_query("anything")


def test_the_openai_path_asks_the_provider_for_the_configured_width():
    # OpenAI's text-embedding-3-* return 1536-d by default but honor a `dimensions`
    # request, so the adapter must send the pinned width (768 matches an index Ollama
    # built at 768) rather than 500ing on the mismatch (hy-zakwj).
    fake = _FakeClient(768)
    config = _config(dimensions=768, api_key="sk-secret")
    provider = OpenAIEmbeddingProvider(config, client=fake)
    provider.embed_query("recognized revenue")
    assert fake.dimensions_arg == 768


def test_the_openai_path_always_sends_dimensions():
    fake = _FakeClient(768)
    provider = OpenAIEmbeddingProvider(_config(dimensions=768), client=fake)
    provider.embed_query("recognized revenue")
    assert fake.dimensions_arg == 768


def test_the_secret_never_reaches_disclosed_metadata():
    config = _config(api_key="sk-DO-NOT-LEAK")
    provider = OpenAIEmbeddingProvider(config, client=_FakeClient(8))
    index = IndexVersion(provider.space, "sha256:x", "commit", "cs-1")
    blob = repr(provider.space.as_metadata()) + repr(index.as_metadata())
    assert "sk-DO-NOT-LEAK" not in blob
    assert "api_key" not in blob
