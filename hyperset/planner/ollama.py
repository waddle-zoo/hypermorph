"""Reading what an Ollama server declares and what it actually allocated.

TWO NUMBERS, AND ONLY ONE OF THEM IS EVIDENCE (hy-3wat, hy-c2tg). A tag's
`num_ctx` is a REQUEST the server may clamp, measured on a real server rather
than assumed: a tag declaring 999999 against llama3.2's 131,072 architecture
maximum loads without complaint and `/api/ps` reports 131072. Ollama neither
refuses nor fails -- it clamps silently, which is the worst of the three
possible behaviours, because the other two would have been visible.

Clamping is not free either. That 131,072-token allocation wanted 17.8 GB on a
16 GB host, so 43% of the model spilled to CPU and the load took 30 seconds.
Trusting a declaration does not merely misreport a window; it can convert a
GPU run into a mostly-CPU one.

So `/api/ps` is the only source that answers "what will this run get", and the
declaration survives here as a cheap pre-flight signal and as something to
disclose when the two disagree.

This module is the one place in the package that knows what Ollama is. The
adapter's rule -- refuse to run on a window nobody observed -- is
vendor-neutral, and it stays that way by taking a number rather than learning
how to obtain one. A second backend adds its own module beside this one and
the adapter is untouched.

WHY A PROBE IS NEEDED AT ALL. Ollama's OpenAI-compatible endpoint ignores a
context size in the request: `options.num_ctx` and a bare `num_ctx` both leave
the model loaded at the server's 4,096 default, and what follows is invisible
at every layer a caller can see. The request returns HTTP 200, and
`usage.prompt_tokens` counts what survived truncation rather than what was
sent -- 602 reported for a 5,498-token prompt. A code planted at the head of a
12,659-token prompt is recalled verbatim at 32k and reported absent at 4k,
with usage claiming 2,050 tokens. A harness that logs usage cannot see the
loss.

THIS CODE CANNOT BE TESTED IN CI, and saying so is the point: verifying it
needs a running Ollama, which CI does not have. What CI tests is the
adapter's refusal, which is where the product rule lives. The parsing below is
covered by tests against captured response text; that the captured text is
what Ollama really returns is evidence from this host, recorded in the bead.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

SHOW_PATH = "/api/show"
PS_PATH = "/api/ps"
GENERATE_PATH = "/api/generate"
TAGS_PATH = "/api/tags"
VERSION_PATH = "/api/version"


def ollama_root(base_url: str) -> str:
    """The server root, from the OpenAI-compatible base URL a runtime uses.

    `/api/show` is served beside `/v1`, not under it.
    """
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def parse_num_ctx(parameters: str) -> int | None:
    """The `num_ctx` a served tag declares, or `None` when it declares none.

    Only the `parameters` block answers this question. `model_info` carries the
    architecture's maximum -- `llama.context_length` is 131072 for a tag this
    server will happily run at 4,096 -- so reading that number would confirm a
    window nobody is serving, which is the failure this module exists to
    prevent.
    """
    for line in parameters.splitlines():
        name, _, value = line.strip().partition(" ")
        if name == "num_ctx":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def declared_context_window(model: str, *, base_url: str, timeout: float = 5.0) -> int | None:
    """What `model`'s tag DECLARES, or `None` when it declares nothing.

    Named for the weaker claim it makes. This reads a declaration, not an
    allocation: a tag can declare `num_ctx 999999` above its own architecture
    maximum and this returns 999999 without anything having loaded it. It also
    cannot see a window set on the server rather than the tag --
    `OLLAMA_CONTEXT_LENGTH` raises the default and leaves `parameters` empty,
    so a correctly configured server reads as undeclared here. Both gaps are
    hy-c2tg, which reconciles this against `/api/ps` once a model is loaded.

    `None` is a refusal upstream rather than a default here: a tag without
    `PARAMETER num_ctx` runs at the server's default, and that default is
    smaller than one resolved bundle.
    """
    request = urllib.request.Request(
        ollama_root(base_url) + SHOW_PATH,
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return parse_num_ctx(payload.get("parameters") or "")


class UnreadableAllocation(RuntimeError):
    """`/api/ps` answered in a shape this module does not understand.

    Distinct from "not loaded" on purpose: not-loaded is fixed by warming the
    model, and no amount of warming fixes an answer we cannot read. Collapsing
    the two would make the second look like the first and produce a caller
    that warms forever.
    """


def observe_context_window(model: str, *, base_url: str, timeout: float = 600.0) -> int | None:
    """Load `model` and report the window the server allocated FOR THAT LOAD.

    The warm and the read are one function because `/api/ps` reports whoever
    loaded last, so reading without loading answers about somebody else's
    model. A foreign client loading the same tag at 4,096 would have this
    return 4,096 while our own run would have been given 32,768 -- a false
    refusal of exactly the class this module was written to remove, living in
    the seam between two functions rather than in either one.

    So the halves are private. If a caller ever genuinely needs the read
    without the load, that is a case to argue rather than a convenience to
    leave lying about.

    `None` means the model is not loaded even after warming, which is a
    refusal upstream rather than a default here.
    """
    _warm_up(model, base_url=base_url, timeout=timeout)
    return _allocated_context_window(model, base_url=base_url)


def _allocated_context_window(model: str, *, base_url: str, timeout: float = 5.0) -> int | None:
    """What the server ALLOCATED for `model`, or `None` when it is not loaded.

    This is the observation. `/api/ps` reports `context_length` per loaded
    model -- what the server resolved after applying the tag's `num_ctx`, the
    `OLLAMA_CONTEXT_LENGTH` default, and the architecture's own maximum -- so
    it answers for a deployment that set its window on the SERVER rather than
    on the tag, which `/api/show` cannot see at all. That configuration is the
    documented way to raise the default and the likeliest CI setup, and
    reading only the declaration refuses it.

    `None` means "not loaded", not "no window": a model nothing has asked for
    is absent from `/api/ps` entirely. `observe_context_window` is how a
    caller turns the first into the second, and is the only exported way in --
    reading this without having loaded the model answers about whatever the
    server loaded last.
    """
    request = urllib.request.Request(ollama_root(base_url) + PS_PATH)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _allocated_from(json.loads(response.read()), model)


def _allocated_from(payload: dict, model: str) -> int | None:
    """The reading half of `allocated_context_window`, split out because it is
    the half CI can test: the call needs a live server with the model loaded,
    the parsing needs only what that server returned."""
    for entry in payload.get("models") or []:
        if model in (entry.get("model"), entry.get("name")):
            window = entry.get("context_length")
            if isinstance(window, int):
                return window
            # Loaded, and the field is missing or not a number. Raised rather
            # than folded into `None`, which means "not loaded": that one is
            # answered by warming the model, and this one never is.
            raise UnreadableAllocation(
                f"{model!r} is loaded but /api/ps reported context_length={window!r}, "
                "which is not a window this reader understands"
            )
    return None


def _warm_up(model: str, *, base_url: str, timeout: float = 600.0) -> None:
    """Force `model` into `/api/ps` with the smallest request that loads it.

    One token, because the point is the load and not the answer. Without this
    the allocation cannot be observed before the run, and the check would have
    to happen mid-run -- which is the whole reason the reconciliation looked
    like a redesign rather than a sequencing detail.

    The timeout is generous on purpose: a large window on a small host takes
    tens of seconds to load, and 30 seconds was measured on this project's own
    hardware for a clamped 131,072-token allocation.
    """
    body = json.dumps(
        {"model": model, "prompt": "", "stream": False, "options": {"num_predict": 1}}
    ).encode()
    request = urllib.request.Request(
        ollama_root(base_url) + GENERATE_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout):
        return None


@dataclass(frozen=True)
class ModelFingerprint:
    """Which bytes a tag resolved to, and how they were quantized.

    GitHub #25 pins the model tag AND its digest, because a tag is mutable:
    `qwen2.5:7b` is a pointer the registry can republish, and a benchmark
    pinned only by name would silently score a different model. Measured on
    this host, `qwen2.5:7b` and `qwen2.5:7b-instruct` resolve to the same
    digest -- two names, one set of weights -- which is exactly why the digest
    rather than the name is the identity.
    """

    digest: str
    quantization: str


class UnknownModelTag(RuntimeError):
    """The server serves no such tag.

    Its own error rather than a `None` a caller could default past: an
    unpullable tag is the one condition where continuing produces a run pinned
    to nothing at all.
    """


def server_version(base_url: str, *, timeout: float = 5.0) -> str:
    """The Ollama version serving this run.

    Pinned because the server, not the model, owns everything the model's
    behaviour rests on that a digest does not cover: the prompt template it
    applies, how it clamps a window, and what `/api/ps` reports.
    """
    with urllib.request.urlopen(ollama_root(base_url) + VERSION_PATH, timeout=timeout) as response:
        return str(json.loads(response.read()).get("version") or "")


def model_fingerprint(model: str, *, base_url: str, timeout: float = 5.0) -> ModelFingerprint:
    """The digest and quantization the server holds for `model`.

    `/api/tags` and not `/api/show`, measured rather than assumed: `show`
    returns `details.quantization_level` but no digest at all, so the field
    that makes a tag identifiable is only on the listing.
    """
    request = urllib.request.Request(ollama_root(base_url) + TAGS_PATH)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _fingerprint_from(json.loads(response.read()), model)


def _fingerprint_from(payload: dict, model: str) -> ModelFingerprint:
    """The reading half of `model_fingerprint`, split out for the reason
    `_allocated_from` is: the call needs a live server, the parsing needs only
    what that server returned."""
    for entry in payload.get("models") or []:
        if model in (entry.get("model"), entry.get("name")):
            digest = entry.get("digest")
            quantization = (entry.get("details") or {}).get("quantization_level")
            if not isinstance(digest, str) or not isinstance(quantization, str):
                raise UnknownModelTag(
                    f"{model!r} is served but /api/tags reported digest={digest!r} "
                    f"quantization_level={quantization!r}, which pins nothing"
                )
            return ModelFingerprint(digest=digest, quantization=quantization)
    raise UnknownModelTag(
        f"{model!r} is not served by this Ollama; pull or build the tag before a run, "
        "because a benchmark cannot pin a model the server does not have"
    )
