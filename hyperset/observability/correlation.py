"""Per-request correlation id (hy-w9ntg, V1 gap Admin/8).

A server-generated id for ONE admin request, carried in a contextvar so the audit repository
can stamp it on every row a request writes WITHOUT threading it through ~18 `record(...)` call
sites, and returned to the operator as an `X-Correlation-Id` response header so they can tie a
response to its audit rows. Pure (contextvars + uuid), so it imports cleanly into the repo and
the transport without a layer cycle.

Set FRESH at the top of EVERY request, not once per connection: `ThreadingHTTPServer` keeps a
thread per keep-alive CONNECTION, so without a per-request reset a stale id would bleed into
the next request served on the same socket. `current_correlation_id()` is None outside any
request, so a row written off-request (a test, a background write) records no id rather than a
stale one.
"""

from __future__ import annotations

import contextvars
import uuid

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hyperset_correlation_id", default=None
)


def new_correlation_id() -> str:
    """A fresh opaque request id (uuid4 hex). No embedded time or host, so it discloses
    nothing about the deployment."""
    return uuid.uuid4().hex


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def current_correlation_id() -> str | None:
    """The current request's correlation id, or None outside a request."""
    return _correlation_id.get()
