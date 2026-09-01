"""The deterministic embedding double -- CI's only embedding (ADR 0022 d.4).

This is to `EmbeddingProvider` what `ScriptedRuntime` is to the agent runtime:
the second implementation that proves the boundary is a boundary, and the one
CI runs because GitHub #25 forbids a hosted credential and a model download in
the required path.

WHAT IT IS. A signed feature-hashing bag of tokens (Weinberger et al.),
L2-normalized. Deterministic, dependency-free, and identical for document and
query text because a bag of tokens has no asymmetry to preserve -- the split
`embed_documents`/`embed_query` is the boundary's, not this double's.

WHAT IT IS NOT, said plainly so no one mistakes it for the thing ADR 0022
forbids. It is not "custom deterministic NLP" standing in for model reasoning
in the product path: it ranks by surface-token overlap, so a paraphrase that
shares no words with a domain's terms does not rank that domain, and proving
ordinary wording reaches the right governed slice is exactly what a real
embedding model does in the benchmark. `ScriptedRuntime` "cannot prove
semantic selection" (`planner/runtime.py`); neither can this. Its job is to
make the ranking machinery, the index versioning, and the governance floors
testable without a network.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from hyperset.embedding.provider import EmbeddingSpace, Vector

DETERMINISTIC_PROVIDER = "deterministic"
DETERMINISTIC_MODEL = "hashing-bag/1"
PROJECTION_VERSION = "lowercase-alnum-split/1"
DEFAULT_DIMENSIONS = 64

# The tokenizer. A regex here is not the regex ADR 0022 rejects: that one is a
# keyword router in the governed path, this one turns test text into buckets in
# a double CI runs instead of a model. Lowercase alphanumeric runs, nothing
# stemmed, nothing weighted.
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class DeterministicEmbeddingProvider:
    """A vector per text, from token hashes alone. No network, no model."""

    def __init__(self, *, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._space = EmbeddingSpace(
            provider=DETERMINISTIC_PROVIDER,
            model=DETERMINISTIC_MODEL,
            dimensions=dimensions,
            input_projection_version=PROJECTION_VERSION,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> Vector:
        return self._embed(text)

    def _embed(self, text: str) -> Vector:
        dims = self._space.dimensions
        acc = [0.0] * dims
        for token in _tokens(text):
            digest = int(hashlib.sha256(token.encode()).hexdigest(), 16)
            bucket = digest % dims
            # A separate bit for the sign, the signed hashing trick: it makes
            # colliding tokens as likely to cancel as to reinforce, so a
            # collision is noise rather than a systematic bias toward one
            # bucket.
            sign = 1.0 if (digest >> 8) & 1 else -1.0
            acc[bucket] += sign
        norm = math.sqrt(sum(x * x for x in acc))
        if norm == 0.0:
            return tuple(acc)
        return tuple(x / norm for x in acc)
