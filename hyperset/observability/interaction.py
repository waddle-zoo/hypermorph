"""Per-request MCP interaction-trace linkage (hy-oqevj, epic hy-01442 slice 2).

The trace store needs to tie one tool call to the session, turn, and search it
belongs to. Those linkage ids are CLIENT-supplied opaque tokens (a session id, a
turn id, a per-call id, a correlation id linking a search to the resolve that
follows) plus a declared intent. They are carried as TRANSPORT METADATA -- HTTP
headers bound to a contextvar here -- and NOT as tool arguments, so no served
`inputSchema` changes and `tools_hash` does not move (they are audit linkage,
never an operation parameter).

Set FRESH at the top of EVERY request and reset after, exactly like
`correlation.py`: a keep-alive socket must not bleed one request's session id
into the next. Outside any request the context is EMPTY, so an off-request or
direct call records null linkage rather than a stale neighbour's ids.

None of these are IDENTITY: the traced principal and workspace are derived
server-side from the verified bearer, never from these headers. A caller cannot
spoof who they are by setting a header here; they can only label their own trace.
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass

# The header names a client sets to label its trace. `mcp-session-id` is the
# Streamable-HTTP session header; the rest are Hyperset-specific opaque tokens.
SESSION_HEADERS = ("mcp-session-id", "x-hyperset-session-id")
TURN_HEADER = "x-hyperset-turn-id"
TOOL_CALL_HEADER = "x-hyperset-tool-call-id"
CORRELATION_HEADER = "x-correlation-id"
INTENT_HEADER = "x-hyperset-intent"

# The LINKAGE ids (session/turn/tool_call/correlation) are OPAQUE TOKENS, and they land in
# a permanent, queryable audit table -- so a caller header is accepted ONLY if it matches a
# strict opaque-token shape, else it is DROPPED (never persisted raw, never logged). The
# class excludes `/`, `@`, `?`, `#`, and whitespace, so a crafted credential URL
# (`https://user:secret@host/...`) can never masquerade as a linkage id and reach a row or a
# degraded log line (hy-oqevj dual-block fix 3). `intent` is DECLARED free text, so it is not
# token-validated here; it is canonically redacted at the persist boundary instead.
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~:-]{1,200}$")


def opaque_token(value: str | None) -> str | None:
    """`value` if it is a well-formed opaque linkage token, else None. Strict by design:
    a header that is not a clean token is dropped rather than sanitized, so nothing a
    caller crafted survives into the durable trace."""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if _OPAQUE_TOKEN_RE.match(stripped) else None


@dataclass(frozen=True)
class TraceContext:
    """The linkage a traced tool call records, or all-None outside a request."""

    session_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    correlation_id: str | None = None
    intent: str | None = None


_EMPTY = TraceContext()

_trace_context: contextvars.ContextVar[TraceContext] = contextvars.ContextVar(
    "hyperset_trace_context", default=_EMPTY
)


def set_trace_context(context: TraceContext | None) -> None:
    _trace_context.set(context or _EMPTY)


def current_trace_context() -> TraceContext:
    """The current request's trace linkage, or an all-None context off-request."""
    return _trace_context.get()


def _first(get, names) -> str | None:
    for name in names:
        value = get(name)
        if value:
            stripped = str(value).strip()
            if stripped:
                return stripped
    return None


def trace_context_from_headers(get) -> TraceContext:
    """Build a `TraceContext` from a case-insensitive header lookup `get(name)`
    that returns the header value or None. Every field is optional; a request
    that sets none yields an all-None context (still a valid, if unlinked,
    trace).

    Linkage ids are validated to the strict opaque-token shape and DROPPED if they
    do not match, so a caller cannot smuggle credential-bearing text into the durable
    trace via a linkage header (hy-oqevj dual-block fix 3). `intent` is free text,
    carried through here and canonically redacted where it persists."""
    return TraceContext(
        session_id=opaque_token(_first(get, SESSION_HEADERS)),
        turn_id=opaque_token(_first(get, (TURN_HEADER,))),
        tool_call_id=opaque_token(_first(get, (TOOL_CALL_HEADER,))),
        correlation_id=opaque_token(_first(get, (CORRELATION_HEADER,))),
        intent=_first(get, (INTENT_HEADER,)),
    )
