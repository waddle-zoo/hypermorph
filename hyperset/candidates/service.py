"""The served discover operation: rank the catalog for a question (hy-gh-206).

The application service behind `discover_analytics_context`, the fourth v0
operation. It reads the full catalog corpus from the pinned snapshots, ranks
its domains and concepts against the question with the configured embedding
provider, and returns candidates that each disclose the signal that ranked them.

ASSIST-CLASS AND SERVED, NOT PLANNED. Discovery is exposed on HTTP and MCP so a
caller can use it, but it is NOT in the governed benchmark tool surface
(`hyperset.planner.loop.tool_specs`), which is an explicit resolve-path
allowlist. That keeps `tools_hash` and the committed #25 recordings untouched
by this operation. Wiring discovery into the planning flow, with a live
re-record, is deferred work (hy-z1mr), not this operation's to trigger.

NO AUTHORITY. The result carries candidates and a schema version, never
governed instructions, a resolution, or a provenance ref. `resolve_analytics_
context` is unchanged and remains the only path to governed meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from hyperset.bundle.schema import SCHEMA_VERSION
from hyperset.candidates.catalog import CANDIDATE_LIMIT, discover_candidates
from hyperset.candidates.corpus import read_catalog_corpus
from hyperset.config.provider_settings import (
    embedding_api_key,
    embedding_base_url,
    embedding_dimensions,
    embedding_model,
    embedding_provider,
)
from hyperset.db.base import utcnow
from hyperset.embedding.openai_embeddings import (
    EmbeddingConfig,
    EmbeddingProviderMisconfigured,
    OpenAIEmbeddingProvider,
)
from hyperset.embedding.provider import EmbeddingProvider

DISCOVER_OPERATION = "discover_analytics_context"

# How a deployment chooses the embedding provider the served operation uses.
# The product runtime is OpenAI-only. Deterministic embeddings remain available
# as an explicit provider injected by tests, never through served configuration.
PROVIDER_ENV = "HYPERSET_EMBEDDING_PROVIDER"
BASE_URL_ENV = "HYPERSET_EMBEDDING_BASE_URL"
API_KEY_ENV = "HYPERSET_EMBEDDING_API_KEY"
MODEL_ENV = "HYPERSET_EMBEDDING_MODEL"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
_OPENAI_API_HOST = "api.openai.com"


def _is_openai_api_base_url(base_url: str) -> bool:
    """Accept only the vendor OpenAI endpoint for the served embedding path.

    The provider label is not a security boundary: an Ollama or other local
    OpenAI-compatible server can present the same API shape. The MVP must not
    silently route embedding corpus data to one of those endpoints, so the
    served factory pins the origin to OpenAI's HTTPS host. Test doubles are
    injected explicitly and never pass through this check.
    """
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or host != _OPENAI_API_HOST:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return port is None or port == 443


def configured_embedding_provider() -> EmbeddingProvider:
    """Build the configured OpenAI embedding provider.

    Any non-OpenAI provider label, including the retired Ollama/local label and
    the deterministic test double, is refused before a passage can leave the
    process. Tests inject their double directly. Embeddings use their own
    explicit deployment key rather than inheriting the chat/model credential.
    """
    # Migrated to the settings object (hy-7m5yg): the loaded providers.embedding.*, else the
    # explicit embedding env values. The served path does not inherit chat/model credentials.
    choice = (embedding_provider() or "openai").strip().lower()
    if choice != "openai":
        raise EmbeddingProviderMisconfigured(
            f"embedding provider {choice!r} is not supported; the MVP uses OpenAI embeddings"
        )
    dims = embedding_dimensions()
    if dims is None:
        raise EmbeddingProviderMisconfigured(
            "OpenAI embeddings require HYPERSET_EMBEDDING_DIMENSIONS to be configured"
        )
    base_url = embedding_base_url() or DEFAULT_OPENAI_BASE_URL
    if not _is_openai_api_base_url(base_url):
        raise EmbeddingProviderMisconfigured(
            "served embeddings accept only https://api.openai.com; local or compatible "
            "embedding endpoints are not supported"
        )
    return OpenAIEmbeddingProvider(
        EmbeddingConfig(
            provider="openai",
            model=embedding_model() or DEFAULT_OPENAI_MODEL,
            base_url=base_url,
            api_key=embedding_api_key(),
            hosted=True,
            dimensions=dims,
        )
    )


@dataclass(frozen=True)
class DiscoveryResult:
    """What the discover operation returns: ranked candidates and nothing governed.

    Carries `schema_version` the way `ContextCatalog` does, so a caller reads
    the same contract version off every served shape. It has no `resolution`,
    no `instructions`, and no `provenance_refs`: a ranking is not an answer.
    """

    query: str
    candidates: list[dict]
    generated_at: datetime = field(default_factory=utcnow)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "query": self.query,
            "candidates": self.candidates,
        }


def discover_analytics_context(
    *,
    question: str,
    session_factory,
    provider: EmbeddingProvider | None = None,
    limit: int = CANDIDATE_LIMIT,
    workspace: str | None = None,
) -> DiscoveryResult:
    """Rank the catalog's domains and concepts for a question. Read-only.

    `provider` is injectable for tests; in the served path it defaults to the
    deployment's configured provider. The corpus is the FULL declared lists, so
    a concept past the catalog's positional cap is reachable by relevance.
    """
    corpus = read_catalog_corpus(session_factory=session_factory, workspace=workspace)
    # An empty catalog has nothing to send to an embedding provider. Read it before
    # constructing the configured adapter so an otherwise valid empty deployment does not
    # require a hosted credential merely to return the empty result promised by discovery.
    embedding_provider = (
        configured_embedding_provider() if provider is None and corpus else provider
    )
    if embedding_provider is None:
        return DiscoveryResult(query=question, candidates=[])
    candidates = discover_candidates(
        question=question, corpus=corpus, provider=embedding_provider, limit=limit
    )
    return DiscoveryResult(query=question, candidates=[c.to_dict() for c in candidates])
