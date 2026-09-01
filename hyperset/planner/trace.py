"""The trace an evaluator reads, not debug output (hy-0gq, GitHub #70).

GitHub #25 scores the chain step by step -- which tools the planner called,
what it retrieved, whether it expanded, whether the plan validated -- so every
step is a structured record with a stable `kind`, exactly as
`resolution.warnings` entries carry a stable code. A scorer that has to
substring-match English is the same defect one layer out, and hy-6ae spent a
PR removing it from the bundle.

The trace is also what makes a run reproducible. Both content-addressed inputs
are recorded on the run itself: the system prompt and the tool descriptions
the planner was given. A prompt edit and a contract edit both change planner
behaviour, and a benchmark has to be able to tell a scoring change from a
contract change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from hyperset.db.base import utcnow

# What a step is. A scorer branches on these and never on prose, so they are
# part of the contract with GitHub #25 rather than an implementation detail.
PLANNER_MESSAGE = "planner_message"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
TOOL_REFUSAL = "tool_refusal"
RUN_FAILED = "run_failed"

STEP_KINDS = (PLANNER_MESSAGE, TOOL_CALL, TOOL_RESULT, TOOL_REFUSAL, RUN_FAILED)

# Why a run this project refused to start could not start. Enumerable for the
# same reason `WARNING_CODES` is: a scorer must be able to list what it may
# have to handle, and a code invented at a call site is a code no client can
# handle. Each constant is named for its own string, so the two cannot drift.
#
# Note what this is NOT yet true of elsewhere: `TOOL_REFUSAL` carries
# `OperationError.code`, whose values are bare literals at their call sites
# with no registry and no document (hy-y633). This is the first coded path in
# the repository whose vocabulary is enforced where the code is raised.
CONTEXT_WINDOW_UNOBSERVED = "context_window_unobserved"
CONTEXT_WINDOW_BELOW_PINNED = "context_window_below_pinned"

RUN_FAILURE_CODES = (CONTEXT_WINDOW_UNOBSERVED, CONTEXT_WINDOW_BELOW_PINNED)

# What produced a run, as reported by `Runtime.provenance()`. Named constants
# rather than literals at the two call sites, for the reason hy-y633 exists:
# a vocabulary a client branches on is one a client must be able to enumerate,
# and one written as a bare string at its raise site is neither gated nor
# findable. A scorer telling a fixture from a run reads this.
SCRIPTED_RUNTIME = "scripted"
OPENAI_AGENTS_RUNTIME = "openai_agents_sdk"
# The second real vendor implementation (hy-2g31). Named here rather than only
# in its own module for the reason above, and because a scorer must be able to
# tell it from the OpenAI arm: it cannot carry a seed or a temperature, so a
# recording from it is not comparable to a benchmark run.
CLAUDE_AGENT_RUNTIME = "claude_agent_sdk"

RUNTIME_NAMES = (SCRIPTED_RUNTIME, OPENAI_AGENTS_RUNTIME, CLAUDE_AGENT_RUNTIME)


def content_hash(payload: str) -> str:
    """Content address for a pinned artifact -- the prompt, the tool specs."""
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


@dataclass(frozen=True)
class Step:
    """One planner decision or one tool interaction.

    `detail` carries the structured facts for this kind and never a rendered
    sentence: refs and versions for a result, the code and recovery for a
    refusal. `summary` is for a person reading the trace and may be reworded.
    """

    kind: str
    detail: dict
    summary: str = ""
    at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.kind not in STEP_KINDS:
            raise ValueError(f"unknown trace step kind {self.kind!r}; add it to STEP_KINDS first")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "at": self.at.isoformat(),
            "detail": self.detail,
            "summary": self.summary,
        }


@dataclass
class Trace:
    """One run: what the planner was given, what it did, and what came back."""

    question: str
    prompt_hash: str
    tools_hash: str
    model: str
    provenance: dict = field(default_factory=dict)
    """What the runtime reported it actually used (hy-pqf3).

    Not handed in beside the run: the planner asks the runtime, because only
    the runtime knows what it ran with. The field it replaces was filled from
    a config passed separately, so a trace could claim a seeded model run for
    an execution that never touched a model -- a record of a pinning that did
    not happen, which is the defect this package spent hy-am2 removing one
    layer down.

    A fixture says so. `ScriptedRuntime` reports `{"runtime": "scripted",
    "model": None}`, which is what lets a scorer refuse to count a fake as
    evidence.
    """

    steps: list[Step] = field(default_factory=list)

    def record(self, kind: str, detail: dict, summary: str = "") -> Step:
        step = Step(kind=kind, detail=detail, summary=summary)
        self.steps.append(step)
        return step

    def of_kind(self, kind: str) -> list[Step]:
        return [step for step in self.steps if step.kind == kind]

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            # Both pinned inputs, so a benchmark can tell a prompt change from
            # a contract change rather than seeing one moved number.
            "prompt_hash": self.prompt_hash,
            "tools_hash": self.tools_hash,
            "model": self.model,
            "provenance": self.provenance,
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=str)
