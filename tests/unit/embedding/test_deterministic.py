"""The deterministic double: determinism, shape, and overlap ranking (hy-gh-206).

It is CI's only embedding, so its guarantees are the ones the discovery tests
rest on -- a text always embeds to the same vector, the width matches the
declared space, and more shared tokens means a higher cosine. It does NOT
promise paraphrase understanding; that is the real model's job in the
benchmark.
"""

from __future__ import annotations

from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.embedding.provider import EmbeddingProvider, cosine


def test_satisfies_the_boundary():
    assert isinstance(DeterministicEmbeddingProvider(), EmbeddingProvider)


def test_deterministic_same_text_same_vector():
    p = DeterministicEmbeddingProvider()
    assert p.embed_query("monthly recurring revenue") == p.embed_query("monthly recurring revenue")


def test_width_matches_the_declared_space():
    p = DeterministicEmbeddingProvider(dimensions=32)
    assert p.space.dimensions == 32
    assert len(p.embed_query("anything")) == 32


def test_embed_documents_preserves_order_and_count():
    p = DeterministicEmbeddingProvider()
    out = p.embed_documents(["a b", "c d", "e f"])
    assert len(out) == 3
    assert out[0] == p.embed_query("a b")


def test_document_and_query_agree_for_a_bag_of_tokens():
    p = DeterministicEmbeddingProvider()
    assert p.embed_documents(["churn rate"])[0] == p.embed_query("churn rate")


def test_shared_tokens_rank_above_disjoint():
    p = DeterministicEmbeddingProvider(dimensions=256)
    query = p.embed_query("revenue by month")
    overlap = p.embed_query("monthly revenue report")
    disjoint = p.embed_query("penguin habitat temperature")
    assert cosine(query, overlap) > cosine(query, disjoint)


def test_empty_text_is_a_zero_vector_of_the_right_width():
    p = DeterministicEmbeddingProvider(dimensions=16)
    v = p.embed_query("")
    assert len(v) == 16
    assert all(x == 0.0 for x in v)
