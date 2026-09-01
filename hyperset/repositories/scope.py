"""Explicit cross-tenant access sentinel for the repository layer (hq-t6nx).

Workspace scoping is the FAIL-CLOSED DEFAULT on the connection / observed-evidence
enumeration reads: a caller names the tenant it means, or names `ALL_WORKSPACES` to
read across every tenant on PURPOSE. There is no silent global default, so a new
consumer of connection or observed state is scoped by construction -- forgetting the
argument is a `TypeError`, never a cross-tenant leak. `ALL_WORKSPACES` is reserved for
SYSTEM callers whose job legitimately spans tenants (the CLI, ops health overviews,
the connector sync loop, processor scans), and each such use is written out in the
open rather than defaulted into.
"""

from __future__ import annotations

from typing import Final


class _AllWorkspaces:
    """The one explicit 'read across every tenant' marker. A distinct type (not a
    string or None) so it can never be produced by accident or by an ordinary
    workspace claim, and so a leak reads as a deliberate `ALL_WORKSPACES` at the call
    site."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "ALL_WORKSPACES"


ALL_WORKSPACES: Final = _AllWorkspaces()


def resolve_workspace_scope(workspace: str | None | _AllWorkspaces) -> str | _AllWorkspaces:
    """Map a `workspace: str | None` argument to a repository scope. A concrete
    tenant scopes; `None` -- the DEFERRED estate-wide meaning the public RESOLVE op
    still carries (ADR-0037) -- becomes the EXPLICIT `ALL_WORKSPACES` at the repo
    boundary, so the deferral is one named decision at the serving layer rather than a
    silent global default in the repository. Passing `ALL_WORKSPACES` is idempotent."""
    return ALL_WORKSPACES if workspace is None else workspace
