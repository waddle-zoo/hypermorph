"""Read-only, non-authoritative knowledge SEARCH over governed/configured sources.

The grep-MVP discovery path (epic hy-01442, slice 1 hy-r0szz): an agent greps the
messy Git/context content of the sources it is CONFIGURED to see, BEFORE resolving the
authoritative governed ContextBundle. It never writes, proposes, approves, or runs SQL,
and it is fail-closed: a caller without ACL access to a source gets zero hits from it.
"""

from hyperset.knowledge.search import (
    KnowledgeHit,
    SearchKnowledgeResult,
    SourceAdapter,
    SourceDocument,
    search_knowledge,
    search_over_adapters,
)

__all__ = [
    "KnowledgeHit",
    "SearchKnowledgeResult",
    "SourceAdapter",
    "SourceDocument",
    "search_knowledge",
    "search_over_adapters",
]
