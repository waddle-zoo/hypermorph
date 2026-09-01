"""The embedding-provider boundary for assist-class candidate discovery.

ADR 0022 decision 4: semantic discovery uses a provider-neutral
``EmbeddingProvider``. V0 ships a deterministic test double and a pinned
local-safe adapter; a deployment may configure a hosted provider instead
without changing the discovery or resolver contracts. Nothing here confers
authority -- an embedding orders candidates and never governs a field.
"""
