"""The agent path over the three served operations (hy-0gq, GitHub #70).

An adapter and a tool contract, not an agent framework. Hyperset supplies the
deterministic substrate -- the catalog, the bundle, the plan check -- and the
model supplies the semantic reasoning that turns a question into a
`ContextDirective`. Nothing in this package reads the text of a question.

Named `planner`, not `agent`: `hyperset/agent` is one of the deleted pre-pivot
packages that `scripts/check_docs.py --section repo-shape` holds empty, and
untracked residue under that exact path still sits in some worktrees. Amending
the guard to admit this package would have retired the check that catches that
residue being committed, so the package moved instead of the guard.
"""

from hyperset.planner.executor import Executor, InProcessExecutor, ToolResult
from hyperset.planner.loop import (
    RESOLVE_PATH_OPERATIONS,
    catalog_bytes,
    fixable_warnings,
    plan_analytics_context,
    planner_prompt,
    refusals,
    tool_specs,
    tools_hash,
)
from hyperset.planner.runtime import (
    CONSERVATIVE_BYTES_PER_TOKEN,
    CONTEXT_WINDOW_TOKENS,
    PlannerRefusal,
    Runtime,
    RuntimeConfig,
    ScriptedRuntime,
    ToolCall,
    UnusableContextWindow,
)
from hyperset.planner.trace import (
    CLAUDE_AGENT_RUNTIME,
    CONTEXT_WINDOW_BELOW_PINNED,
    CONTEXT_WINDOW_UNOBSERVED,
    OPENAI_AGENTS_RUNTIME,
    RUN_FAILURE_CODES,
    RUNTIME_NAMES,
    SCRIPTED_RUNTIME,
    STEP_KINDS,
    Step,
    Trace,
    content_hash,
)

__all__ = [
    "CONSERVATIVE_BYTES_PER_TOKEN",
    "CONTEXT_WINDOW_TOKENS",
    "STEP_KINDS",
    "Executor",
    "InProcessExecutor",
    "Runtime",
    "RuntimeConfig",
    "ScriptedRuntime",
    "Step",
    "ToolCall",
    "ToolResult",
    "Trace",
    "CONTEXT_WINDOW_BELOW_PINNED",
    "CONTEXT_WINDOW_UNOBSERVED",
    "PlannerRefusal",
    "RUN_FAILURE_CODES",
    # The runtime NAMES are exported; the adapter classes are not, and neither
    # is `OpenAIAgentsRuntime`. Importing either one imports its SDK, and this
    # package stays importable with no optional extra installed.
    "CLAUDE_AGENT_RUNTIME",
    "OPENAI_AGENTS_RUNTIME",
    "RUNTIME_NAMES",
    "SCRIPTED_RUNTIME",
    "UnusableContextWindow",
    "catalog_bytes",
    "fixable_warnings",
    "content_hash",
    "plan_analytics_context",
    "planner_prompt",
    "refusals",
    "RESOLVE_PATH_OPERATIONS",
    "tool_specs",
    "tools_hash",
]
