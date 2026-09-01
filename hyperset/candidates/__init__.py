"""Assist-class candidate discovery over the configured context catalog.

Distinct from `hyperset.bundle.discovery`, which ranks observed SOURCES for an
already-selected domain the corpus does not cover. This package ranks the
catalog's DOMAINS and CONCEPTS by relevance to a question, ahead of exact
resolution, so a planner can reach the right governed slice without already
knowing an internal domain name (ADR 0022).

Nothing here is authoritative. A candidate names a Git-declared domain or
concept and the signal that ranked it; it holds no observed-asset ref, no
resolution, and no governed label (ADR 0019 floors). The planner chooses among
candidates and sends the exact names back through the unchanged resolver, which
remains the governance kernel.
"""
