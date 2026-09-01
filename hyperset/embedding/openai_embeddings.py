"""The explicit OpenAI embedding adapter (ADR 0022 decision 4).

The served product path is OpenAI-only. Deployment configuration supplies the
endpoint, model, API key, and vector width; the deterministic provider is the
offline test double. There is no implicit provider or endpoint, so a library
caller cannot silently send context to a local or hosted destination.

The hosted boundary is fail-closed: no request is constructed without a
credential, and ``base_url=None`` is refused rather than silently falling back
to the SDK's hosted default. The secret is held for the life of the process and
appears in no space, index, candidate, or trace metadata.

THE LIVE MODEL IS CHECKED AGAINST THE PIN. `dimensions` is part of the space's
identity, so a model that returns a different width is a different space and is
refused rather than silently ranked in the configured one.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from hyperset.embedding.provider import EmbeddingSpace, Vector


class EmbeddingProviderMisconfigured(ValueError):
    """A provider config that could send a passage somewhere it should not (ADR 0022).

    Two ways to earn it, both refused at construction so the failure is before
    any send rather than after it: a hosted provider with no secret to reach
    it, and a `base_url` of `None` -- which is not "local", it is the SDK's
    hosted default, and defaulting to it would silently choose a destination
    the deployment did not configure.
    """


class EmbeddingDimensionMismatch(ValueError):
    """The live model returned a width other than the configured one.

    The configured `dimensions` is part of the space identity a caller pins an
    index under. A response of a different width is not that space, so it is
    refused rather than L2-normalized into looking like one.
    """

    def __init__(self, model: str, expected: int, got: int) -> None:
        self.model = model
        self.expected = expected
        self.got = got
        super().__init__(
            f"{model!r} returned {got}-d vectors, configured space is {expected}-d: "
            "a different width is a different space and is not mixed into this index"
        )


def is_safe_openai_endpoint(base_url: str) -> bool:
    """Reject local/private endpoints before a served OpenAI request is built.

    OpenAI-compatible HTTPS gateways remain supported for enterprise deployments, but
    a provider label alone must never allow an accidental Ollama or private-network
    destination. This is a routing guard, not a claim that an arbitrary public gateway
    is trustworthy; deployment policy still owns the configured endpoint.
    """
    try:
        parts = urlsplit(str(base_url).strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except (TypeError, ValueError):
        return False
    if parts.scheme.lower() != "https" or not host:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if port not in (None, 443):
        return False
    if (
        host in {"localhost", "ollama", "host.docker.internal"}
        or host.endswith((".local", ".localhost", ".internal"))
        or host.split(".", 1)[0] == "ollama"
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_reserved,
        )
    )


@dataclass(frozen=True)
class EmbeddingConfig:
    """The deployment-supplied OpenAI endpoint, model, key, and vector width.

    These fields are required deliberately. ``hosted`` plus ``api_key`` is the
    served path's explicit boundary; prefixes and ``input_projection_version``
    are recorded so a changed projection is a new space rather than a silent
    one.
    """

    # No implicit endpoint or provider: the served path must receive its
    # OpenAI settings from the deployment configuration. Keeping these fields
    # required prevents a library caller from silently falling back to a local
    # or hosted destination.
    model: str
    dimensions: int
    provider: str
    base_url: str | None
    api_key: str | None = None
    hosted: bool = False
    document_prefix: str = ""
    query_prefix: str = ""
    input_projection_version: str = "raw/1"

    def __post_init__(self) -> None:
        if self.provider != "openai":
            raise EmbeddingProviderMisconfigured(
                f"provider {self.provider!r} is not supported; served embeddings are OpenAI-only"
            )
        if not isinstance(self.dimensions, int) or self.dimensions < 1:
            raise EmbeddingProviderMisconfigured(
                "OpenAI embeddings require a positive integer dimensions setting"
            )
        if self.base_url is None:
            raise EmbeddingProviderMisconfigured(
                f"provider {self.provider!r} has no base_url; None is not local, it is the "
                "OpenAI SDK's hosted default, so it is refused rather than defaulted -- set "
                "the configured OpenAI-compatible endpoint explicitly"
            )
        if self.hosted and not self.api_key:
            raise EmbeddingProviderMisconfigured(
                f"provider {self.provider!r} is hosted but no secret reference was "
                "configured; a hosted embedding provider is opt-in and sends nothing "
                "off-host until an administrator supplies its secret"
            )


class OpenAIEmbeddingProvider:
    """Embeddings over an OpenAI-compatible endpoint, local or hosted."""

    def __init__(self, config: EmbeddingConfig, *, client: object | None = None) -> None:
        self._config = config
        self._space = EmbeddingSpace(
            provider=config.provider,
            model=config.model,
            dimensions=config.dimensions,
            input_projection_version=config.input_projection_version,
        )
        if client is not None:
            # An injected client: a customer that already holds a configured
            # SDK object, and the seam a test uses to exercise projection and
            # the width check without a network. The same shape as the agent
            # runtime's injectable `Agent` factory.
            self._client = client
            return
        # Imported here, not at module scope, so the embedding package imports
        # without the client installed -- the deterministic double, the
        # boundary types, and their tests do not need it. Same reason the
        # agent SDK import lives inside `OpenAIAgentsRuntime.__init__`.
        from openai import OpenAI

        self._client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        prepared = [f"{self._config.document_prefix}{text}" for text in texts]
        return self._embed(prepared)

    def embed_query(self, text: str) -> Vector:
        return self._embed([f"{self._config.query_prefix}{text}"])[0]

    def _embed(self, inputs: Sequence[str]) -> list[Vector]:
        if not inputs:
            return []
        # OpenAI's text-embedding-3-* models honor a dimensions request. Send
        # it on every real adapter call so the remote index and configured
        # space cannot silently diverge.
        kwargs: dict = {
            "model": self._config.model,
            "input": list(inputs),
            "dimensions": self._config.dimensions,
        }
        response = self._client.embeddings.create(**kwargs)
        vectors: list[Vector] = []
        for item in response.data:
            vector = tuple(float(x) for x in item.embedding)
            if len(vector) != self._space.dimensions:
                raise EmbeddingDimensionMismatch(
                    self._config.model, self._space.dimensions, len(vector)
                )
            vectors.append(vector)
        return vectors
