"""Git-owned context (hy-gh-43, ADR 0012).

One configured repository/ref/path is the authoritative source of v0
revenue-domain meaning. This package reads that Git content at an exact
commit, validates it, and hands it to `hyperset.repositories` as an
immutable snapshot. It never authors, edits, approves, or writes back
context: every semantic field stays traceable to the Git commit it came
from.
"""

from hyperset.context.errors import ContextError, ContextValidationError, GitReadError
from hyperset.context.git import GitContextRead, GitContextReader
from hyperset.context.schema import ContextDocument, normalize, parse_context, to_manifest_document
from hyperset.context.sync import ContextSyncResult, sync_git_context

__all__ = [
    "ContextError",
    "ContextValidationError",
    "GitReadError",
    "GitContextReader",
    "GitContextRead",
    "ContextDocument",
    "normalize",
    "parse_context",
    "to_manifest_document",
    "ContextSyncResult",
    "sync_git_context",
]
