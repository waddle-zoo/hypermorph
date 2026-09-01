"""The provider-neutral embedding boundary (ADR 0022 decision 4).

Two implementations ship together on purpose, the same discipline the planner
runtime keeps (`hyperset/planner/runtime.py`): a boundary one vendor can
satisfy is an interface drawn around that vendor; a boundary an unrelated
deterministic double satisfies unchanged is a boundary. `Deterministic
EmbeddingProvider` is that double and also CI's only embedding, because
GitHub #25 must rank in CI with no hosted credential and no model download.

Nothing here confers authority. An embedding produces a vector; a vector
orders candidates; ordering is evidence about where to look and never a
governed fact (ADR 0019 decision 3). So this module has no notion of a
declared ref, a snapshot's instructions, or a resolution status -- it cannot,
by shape, put an assist value in a governed field.

The vector SPACE is the unit of comparability. Vectors from two different
(provider, model, dimensions, input-projection) tuples are not one index and
are never compared as if they were (ADR 0022: "vectors produced by different
model spaces are never mixed"). `EmbeddingSpace` carries that identity and
`require_same_space` is where a mix is refused rather than silently ranked.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hyperset.bundle.schema import canonical_json

# An embedding vector. A tuple rather than a list so a vector is immutable and
# hashable, the same reason `ToolCall` and the bundle sections are frozen: a
# value that ranking depends on should not be editable after it is produced.
Vector = tuple[float, ...]


def _content_address(payload: dict) -> str:
    """A short content hash, spelled once and the same way as everywhere else.

    Reuses `canonical_json` -- "a second spelling of canonical is how two
    identities stop agreeing about what a change is" (`bundle.schema`).
    """
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()[:16]


class IncompatibleEmbeddingSpace(ValueError):
    """Two vectors from different embedding spaces were about to be compared.

    Refused, not coerced: a cosine between a 384-d OpenAI vector and a 768-d
    local one is a number, and it is meaningless. `left` and `right` carry the
    two spaces so a caller can report which index it tried to query with the
    wrong query vector, or which two indices it tried to merge.
    """

    def __init__(self, left: EmbeddingSpace, right: EmbeddingSpace) -> None:
        self.left = left
        self.right = right
        super().__init__(
            f"embedding spaces differ and cannot be one index: {left.id} != {right.id}"
        )


@dataclass(frozen=True)
class EmbeddingSpace:
    """The identity of a vector space: what makes two vectors comparable.

    Four fields, because all four change the geometry. `provider` and `model`
    name the weights; `dimensions` is the vector length a mismatch is caught
    on cheaply; `input_projection_version` is how THIS boundary turned a
    document or a query into the string the model saw -- the prefix a model
    wants on a passage versus a query, the truncation, the normalization.
    Change any one and the numbers are from a different space, so any one
    changing is a new index version.
    """

    provider: str
    model: str
    dimensions: int
    input_projection_version: str

    def __post_init__(self) -> None:
        if self.dimensions < 1:
            raise ValueError(f"dimensions must be >= 1, got {self.dimensions!r}")

    @property
    def id(self) -> str:
        return _content_address(
            {
                "provider": self.provider,
                "model": self.model,
                "dimensions": self.dimensions,
                "input_projection_version": self.input_projection_version,
            }
        )

    def as_metadata(self) -> dict:
        """What a candidate or a trace discloses about the space that ranked it.

        ADR 0022: candidate and trace metadata disclose the embedding model so
        rankings are reproducible. This is the model half; `IndexVersion.
        as_metadata` adds the source snapshot the vectors were built over.
        """
        return {
            "space_id": self.id,
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
            "input_projection_version": self.input_projection_version,
        }


def require_same_space(left: EmbeddingSpace, right: EmbeddingSpace) -> None:
    """Refuse to treat two different spaces as one index."""
    if left != right:
        raise IncompatibleEmbeddingSpace(left, right)


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity of two vectors in the same space.

    The length check is not the space check: equal length is necessary and not
    sufficient, so callers that hold both vectors' spaces use `require_same_
    space` first. This guards the one thing a raw pair of vectors can prove on
    its own -- that the arithmetic is defined.
    """
    if len(a) != len(b):
        raise ValueError(f"vector lengths differ: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class IndexVersion:
    """The full identity of a derived index over a pinned corpus.

    ADR 0022: every derived index version records the provider, exact model
    identifier, dimensions, input-projection version, source-text hash, and
    Git snapshot/commit that produced it. The first four are the `space`; the
    last two are here. Changing any incompatible value creates a new
    `IndexVersion`, and an index built under one is never queried with vectors
    built under another.
    """

    space: EmbeddingSpace
    source_text_hash: str
    commit_sha: str
    context_snapshot_id: str

    @property
    def id(self) -> str:
        return _content_address(
            {
                "space": self.space.id,
                "source_text_hash": self.source_text_hash,
                "commit_sha": self.commit_sha,
                "context_snapshot_id": self.context_snapshot_id,
            }
        )

    def as_metadata(self) -> dict:
        """Disclosed on every candidate and trace so a ranking is reproducible."""
        return self.space.as_metadata() | {
            "index_version": self.id,
            "source_text_hash": self.source_text_hash,
            "commit_sha": self.commit_sha,
            "context_snapshot_id": self.context_snapshot_id,
        }


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A source of vectors, and nothing more.

    Document and query embedding are separate operations because a real
    provider treats them differently -- an asymmetric model prefixes a passage
    and a question differently, and folding both into one call would hide the
    difference the `input_projection_version` exists to record. A provider that
    treats them the same implements both and returns the same vector; a
    provider that does not keeps the distinction the boundary preserves.
    """

    @property
    def space(self) -> EmbeddingSpace: ...

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Embed corpus texts, in order. `len(result) == len(texts)`."""
        ...

    def embed_query(self, text: str) -> Vector:
        """Embed one question."""
        ...
