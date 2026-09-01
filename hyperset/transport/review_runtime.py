"""The authoring model runtime the refine review op drives (hy-jis1).

Isolated in its own module ON PURPOSE. `refine_review_draft` needs an inference
runtime, but `hyperset.transport.operations` -- where the served handler lives --
is reachable from the reporting path (`hyperset.evals.stability`), which
`test_report_time_purity` keeps one import away from no inference runtime. So the
runtime is built HERE and INJECTED into `operations` by the server builders;
operations itself names no inference module. This module is not on the reporting
path, so holding the `openai_runtime`/`evals.run` imports here is safe.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def authoring_runtime():
    """Build the pinned authoring runtime for the refine re-run.

    The authoring prompt and the authoring tools, never the resolve-path tools,
    so `tools_hash` is untouched. Built against the configured hosted endpoint.
    """
    from hyperset.config.provider_settings import (
        openai_api_key,
        openai_base_url,
        openai_reasoning_effort,
    )
    from hyperset.embedding.openai_embeddings import is_safe_openai_endpoint
    from hyperset.flywheel.authoring import authoring_prompt, authoring_tool_specs
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
    from hyperset.planner.runtime import RuntimeConfig

    # Authoring is the deployed hosted-model path, distinct from the pinned local-model
    # benchmark. Compose supplies these same three settings to every serve service.
    base_url = openai_base_url() or "https://api.openai.com/v1"
    if not is_safe_openai_endpoint(base_url):
        raise RuntimeError(
            "the OpenAI authoring runtime refuses local or Ollama-compatible endpoints; "
            "use an HTTPS OpenAI-compatible endpoint"
        )
    config = RuntimeConfig(
        # The first write-back agent is intentionally one hosted Luna path. Do not let a
        # legacy local-model or arbitrary provider-model env override silently change it.
        model="gpt-5.6-luna",
        base_url=base_url,
        api_key=openai_api_key(),
    )
    return OpenAIAgentsRuntime(
        config,
        instructions=authoring_prompt(),
        declarations=authoring_tool_specs(),
        responses_api=urlsplit(base_url).hostname == "api.openai.com",
        reasoning_effort=openai_reasoning_effort() or "medium",
        # The bounded authoring prompt is not the local-model benchmark and the hosted API
        # exposes no allocation probe. Do not invent one in provenance.
        enforce_context_window=False,
    )


def propose_review(
    *, draft, domain, repository, base_ref, path, token, review=None, before_remote_write=None
):
    """Open the proposal-only PR for a review task and return the proposal dict.

    Holds the `git_pr` (and thus `subprocess`) reach that `operations` must not,
    and maps the writer's own failures to `OperationError` so `operations` names
    neither `git_pr` nor its error type. Proposal-only (ADR 0012): a local target
    has no PR to open, so the pushed branch IS the proposal; a URL target
    authenticates the clone/push with the token and opens a real PR (hy-eji4).
    """
    import tempfile
    from pathlib import Path

    from hyperset.context.errors import ContextValidationError
    from hyperset.flywheel.git_pr import GitProposalError, propose_context_change
    from hyperset.transport.operations import INVALID_REQUEST, OperationError

    workdir = Path(tempfile.mkdtemp(prefix="hyperset-propose-"))
    try:
        proposal = propose_context_change(
            draft=draft,
            domain=domain,
            repository=repository,
            base_ref=base_ref,
            path=path,
            workdir=workdir,
            token=token,
            opener=None if token else (lambda _proposal: None),
            review=review,
            before_remote_write=before_remote_write,
        )
    except ContextValidationError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"the draft is not a valid context change: {'; '.join(exc.reasons)}",
            recovery="the draft must satisfy the manifest rules before it can be proposed",
        ) from exc
    except GitProposalError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"could not open a proposal: {exc}",
            recovery="check the configured repository path, base ref, and manifest path",
        ) from exc
    return {
        "repository": proposal.repository,
        "base_ref": proposal.base_ref,
        "head_branch": proposal.head_branch,
        "commit_sha": proposal.commit_sha,
        "path": proposal.path,
        "pr_url": proposal.pr_url,
    }


def install_authoring_runtime() -> None:
    """Inject the authoring runtime factory into the operations module.

    Called by the server builders so `refine_review_draft` has a runtime on both
    transports, without `operations` importing this module (and thereby reaching
    an inference runtime from the reporting path). Idempotent; a test's patch of
    `operations._authoring_runtime` is left untouched.
    """
    from hyperset.transport import operations

    if operations._AUTHORING_RUNTIME_FACTORY is None:
        operations._AUTHORING_RUNTIME_FACTORY = authoring_runtime
    if operations._PROPOSE_WRITER is None:
        operations._PROPOSE_WRITER = propose_review
