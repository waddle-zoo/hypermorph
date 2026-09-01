"""The embedding boundary's identity and comparability rules (hy-gh-206).

The space is the unit of comparability, the index version is the full pin ADR
0022 requires, and a mix of spaces is refused rather than ranked. Pure values,
so tested here directly.
"""

from __future__ import annotations

import pytest

from hyperset.embedding.provider import (
    EmbeddingSpace,
    IncompatibleEmbeddingSpace,
    IndexVersion,
    cosine,
    require_same_space,
)

SPACE = EmbeddingSpace(
    provider="deterministic", model="hashing-bag/1", dimensions=64, input_projection_version="p/1"
)


def test_space_id_is_stable_and_field_sensitive():
    assert SPACE.id == EmbeddingSpace("deterministic", "hashing-bag/1", 64, "p/1").id
    assert SPACE.id != EmbeddingSpace("deterministic", "hashing-bag/2", 64, "p/1").id
    assert SPACE.id != EmbeddingSpace("deterministic", "hashing-bag/1", 128, "p/1").id
    assert SPACE.id != EmbeddingSpace("deterministic", "hashing-bag/1", 64, "p/2").id


def test_dimensions_must_be_positive():
    with pytest.raises(ValueError):
        EmbeddingSpace("d", "m", 0, "p/1")


def test_index_version_metadata_carries_every_pin_adr_0022_requires():
    index = IndexVersion(
        space=SPACE,
        source_text_hash="sha256:abc",
        commit_sha="c0ffee",
        context_snapshot_id="cs-1",
    )
    meta = index.as_metadata()
    # provider, exact model, dimensions, input-projection version, source-text
    # hash, and Git snapshot/commit -- the six ADR 0022 enumerates, plus the
    # two content addresses that make a ranking reproducible.
    assert meta["provider"] == "deterministic"
    assert meta["model"] == "hashing-bag/1"
    assert meta["dimensions"] == 64
    assert meta["input_projection_version"] == "p/1"
    assert meta["source_text_hash"] == "sha256:abc"
    assert meta["commit_sha"] == "c0ffee"
    assert meta["context_snapshot_id"] == "cs-1"
    assert meta["space_id"] == SPACE.id
    assert meta["index_version"] == index.id


def test_index_id_changes_with_the_snapshot_it_was_built_over():
    a = IndexVersion(SPACE, "sha256:x", "commitA", "cs-1")
    b = IndexVersion(SPACE, "sha256:x", "commitB", "cs-1")
    c = IndexVersion(SPACE, "sha256:y", "commitA", "cs-1")
    assert a.id != b.id
    assert a.id != c.id


def test_require_same_space_refuses_a_mix():
    other = EmbeddingSpace("openai", "text-embedding-3-small", 1536, "raw/1")
    with pytest.raises(IncompatibleEmbeddingSpace):
        require_same_space(SPACE, other)


def test_require_same_space_allows_equal():
    require_same_space(SPACE, EmbeddingSpace("deterministic", "hashing-bag/1", 64, "p/1"))


def test_cosine_refuses_length_mismatch():
    with pytest.raises(ValueError):
        cosine((1.0, 0.0), (1.0, 0.0, 0.0))


def test_cosine_identical_is_one_orthogonal_is_zero():
    assert cosine((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == pytest.approx(1.0)
    assert cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine((0.0, 0.0), (1.0, 1.0)) == 0.0
