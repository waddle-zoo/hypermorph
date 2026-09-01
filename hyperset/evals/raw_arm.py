"""Arm 2: the same model and runtime, given raw source metadata only (#25).

The arms must differ in exactly ONE variable -- the governed substrate -- or
the comparison is worthless and the result is unpublishable. So this module is
deliberately not a weaker harness: it is the same SDK adapter, the same turn
and token limits, the same seed and temperature, the same trace, the same
scorers. What changes is what the tools return.

WHY THESE TOOLS ARE NOT SERVED OPERATIONS. The governed benchmark serves three
resolve-path tools; an additional benchmark tool needs evaluator evidence and an ADR
amendment. Nothing here
becomes a fourth. These declarations exist inside the benchmark, reach the
observation store read-only, and are never mounted on HTTP or MCP. A baseline
arm needs a raw-metadata surface to be a baseline at all, and giving the
baseline the product's surface would compare Hyperset to Hyperset.

The listing is bounded for the reason the catalog is: the baseline runs in the
same context window as the governed arm, so an unbounded dump would fail the
arm on window pressure rather than on substrate, and that is a difference in
the harness rather than in what is being measured.
"""

from __future__ import annotations

from hyperset.evals.raw_operations import GET_RAW_ASSET, LIST_RAW_ASSETS
from hyperset.planner.executor import ToolResult
from hyperset.repositories.postgres import PostgresObservedAssetRepository
from hyperset.repositories.scope import ALL_WORKSPACES

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

RAW_TOOL_SPECS = {
    LIST_RAW_ASSETS: {
        "name": LIST_RAW_ASSETS,
        "description": (
            "List the assets the connectors have observed, newest identity first. Returns each "
            "asset's external id and type. This is a page: `truncated` says whether more exist "
            "than were returned, and `counts` gives the live total per type. No approval, "
            "definition or lineage is attached to anything here -- this is what the source "
            "systems reported."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_type": {
                    "type": "string",
                    "description": "Restrict to one observed type, e.g. dataset or chart.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many to return, 1-{MAX_LIMIT}. Defaults to "
                    f"{DEFAULT_LIMIT}.",
                },
                "offset": {"type": "integer", "description": "How many to skip."},
            },
            "additionalProperties": False,
        },
    },
    GET_RAW_ASSET: {
        "name": GET_RAW_ASSET,
        "description": (
            "Return one observed asset's raw payload exactly as the source system reported it, "
            "with its type and the time it was last seen. Nothing is normalized away and nothing "
            "is added."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "external_id": {
                    "type": "string",
                    "description": "The asset's identifier in its source system.",
                },
                "asset_type": {"type": "string", "description": "The observed type."},
            },
            "required": ["external_id", "asset_type"],
            "additionalProperties": False,
        },
    },
}

RAW_TOOL_ERROR = "raw_tool_error"
"""One code for every refusal this surface can make."""


class RawToolError(Exception):
    """A refusal from a tool that is not a served operation.

    Its own type rather than `OperationError`, because that constructor gates
    its code against `ERROR_CODES` -- the served vocabulary a client is told to
    handle (hy-y633) -- and a benchmark-only surface must not add a code to it.
    The shape is the one the planner reads off a refusal: `code`, `recovery`,
    and `to_dict()` for what the model is shown.
    """

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.code = RAW_TOOL_ERROR
        self.message = message
        self.recovery = recovery

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "recovery": self.recovery}}


class RawMetadataExecutor:
    """The raw arm's tool surface, read-only over the observation store.

    Synchronous, like `InProcessExecutor`, because the planner pairs a call
    with its result by adjacency in the trace and an awaited executor would let
    two calls interleave (hy-kz6).
    """

    def __init__(self, *, session_factory) -> None:
        self._assets = PostgresObservedAssetRepository(session_factory)

    def call(self, operation: str, params: dict) -> ToolResult:
        try:
            if operation == LIST_RAW_ASSETS:
                return ToolResult(payload=self._list(params))
            if operation == GET_RAW_ASSET:
                return ToolResult(payload=self._get(params))
        except RawToolError as error:
            return ToolResult(error=error)
        return ToolResult(
            error=RawToolError(
                f"unknown tool {operation!r}",
                recovery=f"call {LIST_RAW_ASSETS} or {GET_RAW_ASSET}",
            )
        )

    def _list(self, params: dict) -> dict:
        asset_type = params.get("asset_type")
        limit = _bounded(params.get("limit"), default=DEFAULT_LIMIT)
        offset = max(int(params.get("offset") or 0), 0)
        live = [
            asset
            for asset in self._assets.list_all(
                asset_type=asset_type, include_deleted=False, workspace=ALL_WORKSPACES
            )
            if asset.deleted_at is None
        ]
        page = live[offset : offset + limit]
        counts: dict[str, int] = {}
        for asset in live:
            counts[asset.asset_type] = counts.get(asset.asset_type, 0) + 1
        return {
            "assets": [
                {"external_id": asset.external_id, "asset_type": asset.asset_type} for asset in page
            ],
            "counts": counts,
            "truncated": offset + len(page) < len(live),
        }

    def _get(self, params: dict) -> dict:
        external_id = params.get("external_id")
        asset_type = params.get("asset_type")
        if not external_id or not asset_type:
            raise RawToolError(
                "external_id and asset_type are both required",
                recovery=f"take both from a {LIST_RAW_ASSETS} entry",
            )
        for asset in self._assets.list_all(
            asset_type=asset_type, include_deleted=False, workspace=ALL_WORKSPACES
        ):
            if asset.external_id == external_id and asset.current_version is not None:
                return {
                    "external_id": asset.external_id,
                    "asset_type": asset.asset_type,
                    "last_seen_at": asset.last_seen_at.isoformat(),
                    "raw_payload": asset.current_version.raw_payload,
                }
        raise RawToolError(
            f"no observed {asset_type!r} with external_id {external_id!r}",
            recovery=f"list what exists with {LIST_RAW_ASSETS}",
        )


def _bounded(value, *, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, MAX_LIMIT))
