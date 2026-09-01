"""Errors for hyperset.context."""

from __future__ import annotations


class ContextError(Exception):
    """Base class for all hyperset.context errors."""


class GitReadError(ContextError):
    """The configured repository/ref/path could not be read."""


class ContextValidationError(ContextError):
    """The Git context at a commit is not a valid v0 context.

    Carries every reason at once (`reasons`) rather than failing on the
    first one: an operator fixing a customer-owned repository should see
    the whole list in one sync, not one bad ref per attempt.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))
