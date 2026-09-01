"""How a tool call reaches the three served operations (hy-0gq).

A seam rather than a call, for the reason the transports exist: a customer
does not run the planner in-process against their own database, they run it
against a deployed Hyperset over HTTP or MCP. Hardcoding the in-process path
would leave the path a customer actually uses as the one nothing exercises,
and this project has now twice found a transport disagreeing with the service
it fronts in a way only a direct assertion caught.

`InProcessExecutor` is what ships. It calls `run_operation`, which is the same
entry point both transports call, so the planner reaches exactly the served
surface and nothing beside it -- there is no privileged path into the resolver
for the agent. A transport-backed executor is a second implementation of this
protocol, not a rewrite of the planner.
"""

from __future__ import annotations

from typing import Protocol

from hyperset.security.authz import SYSTEM_PRINCIPAL
from hyperset.transport.operations import OperationError, run_operation


class ToolResult:
    """What one tool call produced: a payload, or a refusal carrying its code.

    A refusal is a result, not an exception, because the planner has to be
    able to act on it -- and the code is what it acts on. Raising here would
    make the loop's control flow the place refusals are handled, which is how
    a retry-until-something-works loop gets written by accident.
    """

    __slots__ = ("payload", "error")

    def __init__(self, payload: dict | None = None, error: OperationError | None = None) -> None:
        self.payload = payload
        self.error = error

    @property
    def refused(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict:
        return self.error.to_dict() if self.error is not None else (self.payload or {})


class Executor(Protocol):
    """One member, so a second implementation is small by construction.

    IT MUST STAY SYNCHRONOUS, and that is load-bearing rather than incidental
    (hy-kz6). `ToolCall` carries no SDK call id because the planner records a
    request and its outcome at one site, in order, so a result is matched to
    its call by ADJACENCY in the trace. The SDK invokes tools from an async
    callback: an executor that awaited would let two parallel tool calls
    interleave, and the pairing -- and the deletion of `call_id` that rests on
    it -- would silently stop holding. A transport-backed second
    implementation may block; it may not return a coroutine.
    """

    def call(self, operation: str, params: dict) -> ToolResult: ...


class InProcessExecutor:
    """The shipped executor: the served surface, called directly."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory

    def call(self, operation: str, params: dict) -> ToolResult:
        try:
            # The trusted in-process identity: this executor is not a network
            # boundary and carries no bearer token, so with the gate enabled it
            # authorizes as a reader rather than being stranded. With the gate off
            # (the default) the principal is ignored and the call is unchanged.
            return ToolResult(
                payload=run_operation(
                    operation,
                    params,
                    session_factory=self._session_factory,
                    principal=SYSTEM_PRINCIPAL,
                )
            )
        except OperationError as error:
            return ToolResult(error=error)
