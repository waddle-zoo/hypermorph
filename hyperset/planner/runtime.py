"""The agent runtime boundary (hy-0gq, GitHub #70).

Hyperset ships an adapter and a tool contract, never an agent framework: #70
forbids duplicating what an SDK already does, so nothing here manages message
history, decides a retry policy, or runs its own dispatch loop. A runtime
translates our tool declarations into its SDK's shape, drives that SDK's own
loop, and reports each step back.

Two implementations ship together on purpose. `ScriptedRuntime` is not only
the test double -- it is the check that this is a protocol at all. A boundary
one SDK can satisfy is an interface drawn around that SDK; a boundary a second,
entirely unrelated implementation satisfies unchanged is a boundary. The
second real SDK is then a `tools()` translation and a `run()` that delegates.

That last sentence was a prediction until hy-2g31, and it is now a measured
fact: `claude_runtime.ClaudeAgentRuntime` drives the Claude Agent SDK -- a
different vendor, a different transport (a CLI subprocess rather than an HTTP
client), and a different tool mechanism (an in-process MCP server rather than
function tools) -- and it needed no change to these four members. It did find
the one place the Protocol is genuinely narrow, and the narrowness is correct
rather than accidental: that SDK can pin neither a seed nor a temperature, so
it refuses a `RuntimeConfig` carrying a seed instead of reporting a pinning
that did not happen. A runtime that cannot make a pin says so; it does not
quietly widen `provenance()`.

Reading the fake against the adapter is what that check is for, and it caught
one thing both had been made to reimplement: trace step emission (hy-ths). So
a runtime emits no trace step at all. It reports the one thing only it can see
-- what the model said -- as a string, and the planner builds the step
(hy-ths, hy-kz6). A kind every adapter implements for itself is a kind two
adapters can disagree about, on the surface GitHub #25 scores, and so is a
detail SHAPE two adapters build independently.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from hyperset.planner.trace import RUN_FAILURE_CODES, SCRIPTED_RUNTIME


@dataclass(frozen=True)
class ToolCall:
    """What the model asked to run.

    No SDK call id: the planner records the request and its outcome at one
    site, in order, so a result needs no identifier to be matched to its call
    (hy-ths). An id would be a field carried for a mechanism that does not
    exist.
    """

    operation: str
    params: dict


PINNED_MODEL = "qwen2.5:7b"
"""The model GitHub #25's claim is made WITH, pinned as a product decision.

A tag and not a family, because "small Ollama model beats raw metadata" is only
as strong as the model named. The digest, quantization and Ollama version are
pinned by the benchmark harness at run start rather than here: they are
per-host facts a constant cannot assert, and #25 requires a run whose pins do
not match to FAIL rather than warn.

Chosen once its cost stopped being a required gate's cost (hy-yx7y, ADR 0013).
One happy-path run measured CPU-only is 314.3 s for this model against 121.3 s
for llama3.2:3b, which at 12 questions across two local arms is 126 minutes
against 49 -- and a GPU-less cloud runner should be expected 2-4x slower again.
Under a required per-PR gate that difference decides the model; under a split
gate the live arms run on a schedule, and the larger model is affordable.

It is also the harder of the two candidates on this project's own payloads,
which is the right direction for a claim to be tested in: it tokenizes worse
(3.02 bytes per token against 3.32) and its one-resolve happy path is 8,810
tokens against 8,006.
"""

CONTEXT_WINDOW_TOKENS = 32768
"""The pinned context window, as a product decision rather than a test comment.

Every context-budget number in this repository used to descend from
`SMALL_MODEL_WINDOW_TOKENS = 8192`, declared in a test file in a comment
(hy-am2). 8k was never chosen and is measurably wrong: on a real tokenizer the
one-resolve happy path is 8,006 tokens for llama3.2:3b and 8,810 for
qwen2.5:7b, so it does not fit that window at all for one candidate and fits
the other with nothing left to generate into.

32k costs memory and nothing else, which was measured rather than assumed: the
same content prefills the same 2,353 tokens in 27.0s, 20.0s and 21.1s at
allocations of 8k, 16k and 32k, so prefill tracks tokens processed and not the
window allocated. Resident size on an M1 Pro at 32k, fully on GPU:
llama3.2:3b 5.9 GB, qwen2.5:7b 6.4 GB.

WHERE THIS NUMBER HAS TO BE APPLIED, because it cannot be sent from here:
Ollama's OpenAI-compatible endpoint ignores a context size in the request.
Verified -- posting `options.num_ctx` to `/v1/chat/completions` leaves the
loaded model at Ollama's 4,096 default, which is smaller than a single
resolve. The window belongs to the served model, so it is baked into the tag
the benchmark pins:

    FROM qwen2.5:7b
    PARAMETER num_ctx 32768
"""

CONSERVATIVE_BYTES_PER_TOKEN = 3.0
"""The worst measured bytes-per-token ratio among the candidate models.

Replaces a flat 4, which was a guess and was optimistic by 20-25% on exactly
the payload mix that matters: the biggest terms are punctuation-dense JSON
Schema and JSON bundles, which tokenize worse than prose. Measured over this
project's real preamble, catalog, bundle and validation, where 26,622 bytes
is 8,006 tokens for llama3.2:3b (3.32) and 8,810 for qwen2.5:7b-instruct
(3.02).

Fewer bytes per token means a window fills faster, so the arithmetic takes the
smaller ratio. That is now `PINNED_MODEL`'s own measured ratio rather than a
hedge across two candidates (hy-yx7y): qwen2.5:7b-instruct is the worse of the
pair, and it is the one pinned.

Deliberately one number rather than a per-model mapping. A mapping would be
applied per model nowhere -- the only consumer is this bound -- and verifying
a ratio needs a running Ollama, so CI could not check it. That is how the
first draft of this constant shipped with the two models transposed, caught in
review against the measurements two paragraphs up. The measurements stay here
as evidence; the mapping comes back when something can verify it.
"""


class PlannerRefusal(RuntimeError):
    """A refusal this project raised deliberately, carrying a stable code.

    A base class rather than a convention, because the trace reads the code by
    TYPE and never by attribute: `openai.APIError` also has `.code`, and it
    fills it from the response body, so duck-typing would let a remote
    endpoint choose the code a scorer branches on. Only exceptions in this
    hierarchy are trusted, and only codes in `RUN_FAILURE_CODES` at that --
    the same rule `warning()` applies to disclosure codes.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in RUN_FAILURE_CODES:
            raise ValueError(f"unknown run failure code {code!r}; add it to RUN_FAILURE_CODES")
        super().__init__(message)
        self.code = code


class UnusableContextWindow(PlannerRefusal):
    """A runtime that drives a real model refused to start (hy-3wat).

    Vendor-neutral by construction: the rule is that a runtime may not rely on
    a window nobody observed, and nothing here knows how an allocation is
    observed or which server serves it.

    Raised before the first token, which is what makes refusing legitimate
    rather than merely strict: the condition is knowable in advance, so this
    is the project's refuse-rather-than-warn rule and not a preference.
    """


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything the local-model requirement needs to be configurable.

    GitHub #25 must drive this loop in CI against a local OpenAI-compatible
    endpoint with no hosted credential, so every one of these is settable and
    `api_key` is optional: Ollama does not need one.
    """

    model: str
    base_url: str | None = None
    api_key: str | None = None
    seed: int | None = None
    """Passed to the endpoint and recorded on the trace. It was previously
    neither: the adapter read `model`, `base_url`, `api_key`, `temperature` and
    `max_turns` and nothing else, so a run pinned by a seed was not pinned by
    anything (hy-am2)."""

    temperature: float = 0.0
    allocated_context_window: int | None = None
    """What the server was OBSERVED to have allocated for this model, or
    `None` when nothing observed it.

    This is the number a run actually gets, and the only one a real-model
    runtime will start on (hy-c2tg). Filling it is vendor-specific:
    `hyperset.planner.ollama.observe_context_window` is the Ollama one -- one
    function on purpose, because reading an allocation without having caused
    the load answers about whatever the server loaded last -- and a second
    backend brings its own."""

    declared_context_window: int | None = None
    """What the served tag ASKS FOR, or `None` when it asks for nothing.

    Not evidence, and measured rather than assumed: a tag declaring 999999
    against a 131,072 architecture maximum loads without complaint and the
    server allocates 131,072. Ollama neither refuses nor fails -- it clamps
    silently. So this is a request, `allocated_context_window` is the answer,
    and the two are kept apart because the finding is that they disagree
    without saying so.

    Useful for exactly two things: refusing early when a tag asks for less
    than the pinned window, which one cheap call settles before a load that
    can take 30 seconds; and disclosing a disagreement when the server gives
    something else. Its absence is NOT a refusal reason -- a server started
    with `OLLAMA_CONTEXT_LENGTH` declares nothing on the tag and allocates
    correctly, which is the documented way to raise the default and the
    likeliest CI setup (hy-c2tg)."""

    max_turns: int = 8


@runtime_checkable
class Runtime(Protocol):
    """Four members. Anything larger would be the framework #70 forbids.

    The fourth is `provenance()`, and it is required rather than optional for
    one reason worth stating where the next reader will find it: an optional
    disclosure is declined precisely by the runtime that most needs to make it.
    A four-member Protocol invites being shaved back to three on minimality
    grounds, and that argument is right by every measure except the one that
    matters here -- the trace is what GitHub #25 scores, and a record that
    cannot say what produced it can be believed about a run that never
    happened.

    It survives the test that would otherwise kill a fourth member. A member is
    too much when the fake has to reimplement something to satisfy it; here
    `ScriptedRuntime` answers that it ran no model, which costs it nothing and
    is true. A member a fake can satisfy honestly is not a member the fake
    reimplements.

    Runtime-checkable so a test can assert every shipped implementation still
    satisfies it, the way `Connector` already is (hy-btty). `isinstance` sees a
    member deleted or renamed and NOT a signature changed underneath it, which
    is half of what this Protocol has done in one day, so
    `tests/unit/planner/test_runtime_conformance.py` checks the signatures too.
    A third party owes these four members and an honest `provenance()`; it does
    not owe running our suite.
    """

    def tools(self) -> Sequence[dict]: ...

    def run(
        self,
        question: str,
        *,
        on_message: Callable[[str], None],
        call_tool: Callable[[ToolCall], dict],
    ) -> None:
        """Drive the SDK's loop. `on_message` takes the model's TEXT, not a
        step: a runtime cannot emit a trace step at all (hy-kz6).

        hy-ths moved the three tool step kinds in front of this boundary
        because a kind every adapter implements for itself is a kind two
        adapters can disagree about. `PLANNER_MESSAGE` stayed, since only a
        runtime can see what the model said -- but its SHAPE did not have to,
        and two adapters were building `{"text": ...}` independently and
        agreeing by coincidence. Passing the string leaves the runtime with
        the part that is irreducibly its own and nothing else.
        """

    def close(self) -> None: ...

    def provenance(self) -> dict:
        """What this runtime ACTUALLY used, never what it was configured with.

        A runtime handed a configuration it ignored reports what it ignored it
        in favour of: the defect this exists for is a record describing a
        pinning that did not happen, and a configuration echo would reproduce
        it one constructor earlier.

        `runtime` and `model` are required; everything else is open. A dict
        rather than a dataclass because a second backend holds fields this one
        does not, and the trace already carries structured detail elsewhere --
        but the two required keys are load-bearing TODAY, not once a scorer
        exists: `plan_analytics_context` reads `model` to fill the trace's own
        model field, and `runtime` is what tells a fixture from a run.

        `model` may be `None`, which is the honest answer for a runtime that
        drove no model. Absent is different from `None` and is not allowed:
        one says "no model ran", the other says "this runtime declined to
        say", and only the first is a fact.
        """


@dataclass
class ScriptedRuntime:
    """A runtime that plays a fixed script instead of calling a model.

    Deterministic by construction, so planner behaviour can be asserted
    without a live model and without mocking inside the planner -- the seam
    under test is the same one a real SDK plugs into.

    A script entry is either a `ToolCall` to issue or a string to say. The
    script may also be a callable taking the results so far, for the tests
    that need a decision to depend on what came back.
    """

    script: Sequence[ToolCall | str] | Callable[[list[dict]], Iterable[ToolCall | str]]
    tool_specs: Sequence[dict] = field(default_factory=tuple)
    closed: bool = False

    def tools(self) -> Sequence[dict]:
        return self.tool_specs

    def run(
        self,
        question: str,
        *,
        on_message: Callable[[str], None],
        call_tool: Callable[[ToolCall], dict],
    ) -> None:
        results: list[dict] = []
        entries = self.script(results) if callable(self.script) else self.script
        for entry in entries:
            if isinstance(entry, str):
                on_message(entry)
                continue
            results.append(call_tool(entry))

    def close(self) -> None:
        self.closed = True

    def provenance(self) -> dict:
        """No model, and that is the whole answer.

        Not a gap to be filled in later and not nulls standing in for a real
        one: a trace from this runtime is a FIXTURE rather than a run, and
        `{"runtime": "scripted", "model": None}` is what lets a scorer tell the
        two apart. Nothing here fabricates a model name, a seed or a window,
        which is why the fourth member does not make this fake reimplement
        anything (hy-pqf3).
        """
        return {"runtime": SCRIPTED_RUNTIME, "model": None}
