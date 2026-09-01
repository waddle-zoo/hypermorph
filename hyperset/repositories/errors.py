"""Errors shared by every `hyperset.repositories` implementation."""

from __future__ import annotations


class RepositoryError(Exception):
    """Base class for all hyperset.repositories errors."""


class NotFoundError(RepositoryError):
    """A requested row does not exist."""


class AmbiguousIdentityError(NotFoundError):
    """A workspace-LESS identity lookup matched more than one tenant's row (hq-t6nx).

    Since identity is `(workspace, repository, ref, path)`, an un-scoped
    `(repository, ref, path)` lookup is no longer unique the moment two tenants
    share a pointer. Rather than let the datastore raise an unhandled
    `MultipleResultsFound` (a 500), a workspace-less caller fails CLOSED with this
    explicit error. It subclasses `NotFoundError` so every existing
    `except NotFoundError` path degrades cleanly (no row served) -- a tenant-sensitive
    caller must pass an explicit `workspace` to resolve exactly one row."""


class OptimisticConcurrencyError(RepositoryError):
    """A caller's `expected_version` did not match the current version."""


class DuplicateReviewTaskError(RepositoryError):
    """A review task with this `idempotency_key` already exists."""
