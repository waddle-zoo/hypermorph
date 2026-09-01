"""HTTP and MCP adapters over the two agent-facing v0 operations (hy-oih).

Both transports call `run_operation` and serialize what it returns. Neither
owns a response shape: `ContextBundle` and `PlanValidation` are the contract.
"""

from hyperset.transport.http import ROUTES, build_server, serve_http
from hyperset.transport.mcp import serve_stdio, serve_streamable_http, tool_definitions
from hyperset.transport.operations import (
    OPERATION_SPECS,
    OPERATIONS,
    OperationError,
    run_operation,
    serialize,
)

__all__ = [
    "OPERATIONS",
    "OPERATION_SPECS",
    "ROUTES",
    "OperationError",
    "build_server",
    "serve_streamable_http",
    "run_operation",
    "serialize",
    "serve_http",
    "serve_stdio",
    "tool_definitions",
]
