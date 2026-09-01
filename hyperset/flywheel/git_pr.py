"""Propose a context change as a Git PR, and STOP (hy-8b5h, ADR 0012).

The flywheel's write-back curator. It takes a step-4 UNAPPROVED draft and opens
a pull request into the customer's context repository carrying that draft as a
manifest change -- then stops. A human reviews and merges. The Overseer affirmed
this boundary against ADR 0012 (2026-08-07): the writer PROPOSES only.

This is a DISTINCT, permissioned surface, separate from the fetch-only
`hyperset/context/git.py`: that module reads and this one writes, and neither
does the other's job. The boundary here is structural, not careful:

- It only ever pushes the ephemeral, content-hash-named proposal branch. It
  runs no `git merge` and never pushes -- force or otherwise -- to the base ref
  or any governed ref; there is no code path that advances the customer's
  authoritative ref. The proposal branch push is force (`+`) so a re-proposal of
  the same content overwrites its own stale branch (e.g. one left by an earlier
  failed run) instead of failing on a non-fast-forward -- idempotent, and scoped
  strictly to hyperset's own proposal ref.
- It imports no `ReviewRepository`, no `GovernedContext` writer, and holds no
  approval call. It cannot advance a `GovernedContext`, cannot call `approve`,
  and creates no Hyperset-side approvable object (ADR 0005, ADR 0012, ADR 0019
  floor 4). The only path from proposal to authority is a human Git merge.

Opening the PR itself is the injected last mile (`opener`): the default posts to
the GitHub REST API (no `gh` binary), and a test passes a recorder so the branch
push is exercised against a local repository with no network.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

from hyperset.context.schema import validate_definition_draft
from hyperset.security.pii import PiiError, guard_text
from hyperset.security.redaction import redact_free_text_userinfo

# The manifest file the v0 context layout pins (hy-gh-43).
MANIFEST_FILE = "manifest.yaml"
# The list fields a proposal may extend, and the key each entry is deduplicated
# by, so proposing a draft twice does not append a duplicate.
_MERGE_KEYS = {
    "definitions": lambda entry: entry.get("term"),
    "approved_sources": lambda entry: entry.get("ref"),
    "prohibited_sources": lambda entry: entry.get("ref"),
    "fields": lambda entry: entry.get("name"),
    "joins": lambda entry: (entry.get("from"), entry.get("to")),
    "filters": lambda entry: entry,
    "checks": lambda entry: entry,
    "caveats": lambda entry: entry,
}

PROPOSAL_AUTHOR_NAME = "Hyperset (proposal)"
PROPOSAL_AUTHOR_EMAIL = "noreply@hyperset.invalid"


class GitProposalError(Exception):
    """A proposal could not be prepared or pushed. Never raised to mean an
    approval or a merge -- those are not operations this surface has."""


@dataclass(frozen=True)
class ContextProposal:
    """One proposed context change, opened as a PR and left for a human.

    Carries what the PR is, never a merged or approved state: there is no field
    here for an approval, because this surface produces none.
    """

    repository: str
    base_ref: str
    head_branch: str
    path: str
    title: str
    body: str
    commit_sha: str
    manifest: str
    pr_url: str | None = None


def _auth_header(token: str) -> str:
    """The value for an http.extraheader that authenticates a private HTTPS
    clone/push with a GitHub token (hy-eji4). Basic auth over `x-access-token`
    is what GitHub accepts for a token, and putting it in a HEADER rather than
    the remote URL keeps the token out of the URL git would echo on an error."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {basic}"


def _git(
    args: list[str], *, cwd: Path | None = None, timeout: int = 120, auth: str | None = None
) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    if auth:
        # The auth header is passed through git's ENV-based config (git 2.31+),
        # NEVER a `-c` argv flag: the base64 is reversible, so on the command
        # line the token would be exposed in /proc/<pid>/cmdline and durably
        # captured by process-exec auditing (auditd/EDR). The environment of a
        # child is not on the process list, mirroring the GH_TOKEN pattern the
        # PR opener uses (hy-6haz).
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = auth
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment guard
        raise GitProposalError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitProposalError(f"git {args[0]} timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitProposalError(f"git {args[0]} failed: {detail[-1] if detail else 'no output'}")
    return result.stdout.decode("utf-8", "replace")


@dataclass(frozen=True)
class TargetProbe:
    """The result of a read-only write-back-target reachability probe (hq-095h).

    Carries only reachability facts and a short non-secret detail -- there is no
    field here for a branch, a commit, or a PR, because the probe creates none.
    """

    reachable: bool
    base_ref_exists: bool
    detail: str


def probe_target(
    *,
    repository: str,
    base_ref: str,
    token: str | None = None,
    timeout: int = 30,
) -> TargetProbe:
    """Probe a write-back target WITHOUT writing anything (hq-095h).

    `git ls-remote` lists the target's heads over the network (or a local path)
    but clones nothing, fetches no objects, and creates NO branch, commit, or PR.
    It proves three things at once: the repository is reachable, the credential
    (when a URL target carries one) authenticates, and whether `base_ref` exists.
    The token, when present, is handed to git through the same ENV-based
    extraheader the writer uses (never argv, never the URL, hy-6haz), so it cannot
    leak into a process listing or the returned detail.

    NEVER raises: a git failure (unreachable host, auth rejected, bad ref spec) is
    reported as `reachable=False` with a bounded, credential-free detail, so the
    caller maps it to a blocked status rather than crashing. The stored repository
    pointer carries no embedded userinfo (the admin write rejects a
    credential-bearing URL), so the git error text holds no secret.
    """
    auth = _auth_header(token) if token else None
    try:
        out = _git(
            ["ls-remote", "--heads", repository, base_ref.strip()],
            auth=auth,
            timeout=timeout,
        )
    except GitProposalError as exc:
        detail = str(exc).splitlines()[0][:200] if str(exc) else "unreachable"
        return TargetProbe(reachable=False, base_ref_exists=False, detail=detail)
    return TargetProbe(reachable=True, base_ref_exists=bool(out.strip()), detail="")


def _merge_into_manifest(manifest_text: str, draft: dict, domain: str | None = None) -> str:
    """The draft, merged into the existing manifest as an ADD-ONLY suggestion.

    Every list field is extended with the draft's entries that are not already
    present, keyed so a re-proposal is idempotent; `grain` is set only if the
    manifest has none. Nothing existing is removed or rewritten -- a proposal
    suggests, it does not overwrite the customer's governed meaning. The YAML is
    re-serialized (PyYAML does not preserve comments), which a human sees and
    edits in the PR.

    `manifest_text` may be empty: the flywheel starts with little or no governed
    context, so the FIRST proposal for a domain creates its manifest from scratch
    (hy-gh-43). `domain` names that new manifest; an existing `domain` is never
    overwritten (add-only, like `grain`).
    """
    manifest = yaml.safe_load(manifest_text) if manifest_text.strip() else {}
    if not isinstance(manifest, dict):
        raise GitProposalError("the target manifest is not a mapping")
    if domain and not manifest.get("domain"):
        manifest["domain"] = domain
    for key, key_of in _MERGE_KEYS.items():
        additions = draft.get(key) or []
        if not additions:
            continue
        existing = manifest.setdefault(key, [])
        seen = {_hashable(key_of(entry)) for entry in existing}
        for entry in additions:
            if _hashable(key_of(entry)) not in seen:
                existing.append(entry)
                seen.add(_hashable(key_of(entry)))
    if draft.get("grain") and not manifest.get("grain"):
        manifest["grain"] = draft["grain"]
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)


def _hashable(value):
    return tuple(value) if isinstance(value, list) else value


def _owner_repo(repository: str) -> str | None:
    """`owner/repo` from an HTTPS or SSH github.com URL, or None if it is not
    a github.com remote (e.g. a local-path test target, which opens no PR)."""
    ref = repository.strip()
    if ref.endswith(".git"):
        ref = ref[:-4]
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    ):
        if ref.startswith(prefix):
            parts = ref[len(prefix) :].strip("/").split("/")
            if len(parts) >= 2 and parts[0] and parts[1]:
                return f"{parts[0]}/{parts[1]}"
    return None


def _default_opener(
    proposal: ContextProposal, *, token: str | None = None
) -> str | None:  # pragma: no cover - needs network
    """Open the PR via the GitHub REST API and return its URL. Uses the REST API
    rather than the `gh` CLI so the server has no `gh` binary dependency (the api
    container ships none). The token -- a PAT or a short-lived App installation
    token -- authenticates the call in the Authorization header only, never on a
    command line and never logged (hy-eji4). Isolated behind the `opener` seam so
    the branch-push path stays testable without a network or a token."""
    owner_repo = _owner_repo(proposal.repository)
    if owner_repo is None:
        raise GitProposalError(
            f"cannot derive a github.com owner/repo from {proposal.repository!r}"
        )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.post(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            json={
                "title": proposal.title,
                "head": proposal.head_branch,
                "base": proposal.base_ref,
                "body": proposal.body,
            },
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise GitProposalError(f"opening the pull request failed: {exc}") from exc
    if response.status_code == 422:
        # A PR already exists for this head branch (re-proposal of the same
        # content). That is idempotent, not an error: return the open one.
        existing = _existing_pr_url(owner_repo, proposal, headers)
        if existing:
            return existing
    if response.status_code >= 300:
        # GitHub's error body names the problem (e.g. branch missing, no
        # permission) and carries no secret; the token is never echoed.
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = response.text[:200]
        raise GitProposalError(
            f"opening the pull request failed ({response.status_code}): {detail}"
        )
    return response.json().get("html_url")


def _existing_pr_url(owner_repo: str, proposal: ContextProposal, headers: dict) -> str | None:
    """The URL of the open PR already opened from this proposal branch, if any --
    so a re-proposal of identical content returns its existing PR (idempotent)."""
    owner = owner_repo.split("/", 1)[0]
    try:
        found = requests.get(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            params={"head": f"{owner}:{proposal.head_branch}", "state": "open"},
            headers=headers,
            timeout=30,
        )
    except requests.RequestException:
        return None
    if found.status_code >= 300:
        return None
    items = found.json()
    return items[0].get("html_url") if items else None


def read_pr_state(*, repository: str, head_branch: str, token: str | None = None) -> dict:
    """Read the current state of the proposal PR opened from `head_branch`.

    Proposal-lifecycle reconcile (hq-ci92) reads GitHub to learn whether a HUMAN
    has merged the PR -- it never merges, approves, or writes anything (ADR 0012).
    Returns a small NON-SECRET record: `state` is one of 'merged',
    'closed_unmerged', or 'open'; plus `pr_url`, `pr_number`, and a `merged` bool.
    A non-github or unreachable target yields state 'unknown' with merged False,
    so the caller records an explicit un-reconciled state and NEVER assumes a
    merge (never a silent 'done'). Read-only: it calls no mutating endpoint.
    """
    unknown = {"state": "unknown", "pr_url": None, "pr_number": None, "merged": False}
    owner_repo = _owner_repo(repository)
    if owner_repo is None:
        return unknown
    owner = owner_repo.split("/", 1)[0]
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        found = requests.get(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            params={"head": f"{owner}:{head_branch}", "state": "all"},
            headers=headers,
            timeout=30,
        )
    except requests.RequestException:
        return unknown
    if found.status_code >= 300:
        return unknown
    # FAIL CLOSED on a malformed 2xx payload: a body that is not JSON, not a
    # non-empty list, or whose first item is not a mapping cannot be classified,
    # so it is 'unknown' -- never guessed as a merge or silently 'open'. A merge is
    # only ever asserted from an explicit `merged_at`/`merged`, and an UNMERGED PR
    # must carry a recognised `state` ('closed' or 'open'); a missing/unexpected
    # state is 'unknown', not a default 'open'.
    try:
        items = found.json()
    except ValueError:
        return unknown
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return unknown
    pr = items[0]
    merged = bool(pr.get("merged_at")) or pr.get("merged") is True
    pr_url, pr_number = pr.get("html_url"), pr.get("number")
    if merged:
        state = "merged"
    elif pr.get("state") == "closed":
        state = "closed_unmerged"
    elif pr.get("state") == "open":
        state = "open"
    else:
        return unknown
    return {"state": state, "pr_url": pr_url, "pr_number": pr_number, "merged": merged}


_PROPOSAL_PREAMBLE = (
    "An unapproved candidate context definition drafted by Hyperset (flywheel "
    "step 4). This PR PROPOSES a change to the governed context; it does not "
    "approve or merge anything. Review, edit, and merge if it is correct -- a "
    "human Git merge is the only path to authority (ADR 0012)."
)


_REF_SLUG_SUB = re.compile(r"[^a-z0-9]+")


def _ref_slug(value: str) -> str:
    """Make a NON-SENSITIVE identifier ref-safe: lowercased, `[a-z0-9-]`, collapsed,
    trimmed, non-empty fallback. Used on the review TASK ID before it goes in a branch
    name -- ref-safety (no `@`, `/`, `:`, or `..`), NOT secrecy. A charset slug is not a
    secrecy boundary (it keeps `supersecrettoken123`), so it is only applied to a value
    that carries no sensitive content; the untrusted DOMAIN never reaches a ref (hy-w5gb
    #449 round 4)."""
    slug = _REF_SLUG_SUB.sub("-", value.strip().lower()).strip("-")
    return slug or "context"


def _guard_remote_text(text: str) -> str:
    """The ONE redaction+guard pipeline every remote-bound proposal string passes
    through (hy-w5gb #449): URL-userinfo redact with the canonical detector (the
    #447/#448 credential class), then PII-guard as a whole, FAILING CLOSED -- nothing
    is committed or pushed -- on a PII block. Used for the PR body, the PR title, and
    (because the title is the commit message) the permanent git history, so no
    remote-bound surface bypasses the boundary."""
    try:
        return guard_text(redact_free_text_userinfo(text), boundary="git_proposal")
    except PiiError as exc:
        raise GitProposalError(str(exc)) from exc


def _proposal_title(domain: str, review: dict | None) -> str:
    """The PR title, naming the domain and -- when the proposal came from a review
    task -- that task's id, so a reviewer on GitHub can trace it back (hy-w5gb)."""
    base = f"Propose context definition for {domain!r} (unapproved, for review)"
    task_id = (review or {}).get("task_id")
    return f"{base} [review task {task_id}]" if task_id else base


def _proposal_body(review: dict | None, *, base_commit: str, base_ref: str) -> str:
    """The minimal remote PR body for a proposal-only change.

    Keep only non-sensitive task/source linkage. Questions, evidence refs, notes,
    feedback, and proposer identities remain in Hyperset's local audit/review store
    and never become remote PR payload.
    """
    lines = [_PROPOSAL_PREAMBLE, ""]
    meta = review or {}
    if meta.get("task_id"):
        lines.append(f"- Review task: {meta['task_id']}")
    lines.append(f"- Source commit: {base_commit} ({base_ref})")
    preview = meta.get("preview")
    lines.append(
        f"- Preview: {preview}"
        if preview
        else (
            "- Preview: the ephemeral proposed-context preview is a separate step and "
            "has not been run for this proposal."
        )
    )
    if meta.get("backlink"):
        lines.append(f"- Backlink: {meta['backlink']}")
    return "\n".join(lines)


def propose_context_change(
    *,
    draft: dict,
    domain: str,
    repository: str,
    base_ref: str,
    path: str,
    workdir: Path,
    token: str | None = None,
    opener: Callable[[ContextProposal], str | None] | None = None,
    review: dict | None = None,
    before_remote_write: Callable[[], None] | None = None,
) -> ContextProposal:
    """Prepare and push a proposal branch, open a PR, and STOP.

    Validates the draft against the manifest rules a human's commit faces,
    clones the repository at `base_ref`, merges the draft into the manifest at
    `path`, commits it on a new proposal branch, pushes THAT branch, and opens a
    PR. It never touches the base ref and never approves.

    `token`, when given, authenticates the clone and push against a private
    HTTPS remote and is handed to the default PR opener (hy-eji4). It is passed
    only through a git http header and the opener's child environment -- never
    embedded in a remote URL, a command line, or a logged error.

    `before_remote_write`, when supplied, is called immediately before the branch
    push and again immediately before PR creation. A caller can use it as a
    last-moment lease fence without holding a database transaction over network I/O.
    """
    validate_definition_draft(draft, domain=domain)

    auth = _auth_header(token) if token else None
    clone = workdir / "clone"
    _git(
        ["clone", "--quiet", "--branch", base_ref, "--depth", "1", "--", repository, str(clone)],
        auth=auth,
    )
    # The SOURCE commit the draft is proposed against -- the base ref's tip, captured
    # before the proposal commit is created -- so the enriched PR body names the exact
    # commit a reviewer's draft is relative to (hy-w5gb).
    base_commit = _git(["rev-parse", "HEAD"], cwd=clone).strip()

    # A missing manifest is not an error: it is a NEW domain. The flywheel builds
    # governed context up from little or none, so the first proposal for a domain
    # creates its manifest (and directory) rather than requiring it to pre-exist
    # (hy-gh-43). An empty base merges add-only, same as an existing one.
    manifest_path = clone / path / MANIFEST_FILE
    existing_text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
    merged = _merge_into_manifest(existing_text, draft, domain=domain)
    # PII guard on the proposal content BEFORE it is committed into the customer
    # repo (hy-hbtz). When the guard is engaged it redacts, or -- if Presidio
    # cannot be hosted, or it is configured to block on PII -- it fails closed by
    # refusing the proposal: nothing is written, committed, or pushed. A no-op
    # unless HYPERSET_PII_GUARD is set.
    # URL userinfo is a credential-bearing pointer, not optional PII policy. Redact it
    # even when the broader PII guard is disabled, so a legacy or agent-authored URL in
    # a definition cannot be committed into the customer's governed repository.
    merged = redact_free_text_userinfo(merged)
    try:
        merged = guard_text(merged, boundary="git_proposal")
    except PiiError as exc:
        raise GitProposalError(str(exc)) from exc
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(merged, encoding="utf-8")

    # The branch name carries NO domain content (hy-w5gb #449 round 4). A charset slug is
    # ref-SAFE but not SECRET -- it keeps `supersecrettoken123` and an email's letters --
    # and the branch is a pushed, remote-bound surface. So the ref is built from
    # non-sensitive parts only: the review TASK ID (a system-generated `rt-...`, ref-safed
    # defensively) when there is one, plus `_short(merged)`, a sha256 of the manifest that
    # reveals none of its content. The domain's sensitive content lives ONLY in the
    # Presidio-guarded, userinfo-redacted title/body/commit.
    task_ref = _ref_slug(review["task_id"]) if (review and review.get("task_id")) else ""
    head_branch = (
        f"hyperset/proposal/{task_ref}-{_short(merged)}"
        if task_ref
        else f"hyperset/proposal/{_short(merged)}"
    )
    _git(["checkout", "-b", head_branch], cwd=clone)
    _git(["add", "--", f"{path}/{MANIFEST_FILE}"], cwd=clone)
    # EVERY remote-bound proposal string goes through ONE redaction+guard pipeline
    # (hy-w5gb #449). The TITLE interpolates the domain and task id and is ALSO the git
    # COMMIT MESSAGE (`-m title` below) -- permanent history, the worse surface -- and a
    # refname-legal slug can smuggle a token or PII (e.g. `revenue-tok_x-a@b.example`)
    # into it, bypassing a body-only guard. The remote body is deliberately minimal: the
    # miss question, evidence, feedback, and proposer identity stay in Hyperset's audit store.
    # `_guard_remote_text` URL-userinfo redacts (the #447/#448 class) then PII-guards each,
    # failing closed -- nothing is committed or pushed -- so the title, the commit, and the
    # body are all covered by the same boundary.
    title = _guard_remote_text(_proposal_title(domain, review))
    body = _guard_remote_text(_proposal_body(review, base_commit=base_commit, base_ref=base_ref))
    _git(
        [
            "-c",
            f"user.name={PROPOSAL_AUTHOR_NAME}",
            "-c",
            f"user.email={PROPOSAL_AUTHOR_EMAIL}",
            "commit",
            "--quiet",
            "-m",
            title,
        ],
        cwd=clone,
    )
    commit_sha = _git(["rev-parse", "HEAD"], cwd=clone).strip()
    # The one push, and it is the proposal branch -- never `base_ref`.
    # Force-push scoped to hyperset's own ephemeral proposal ref only (the `+`),
    # so a re-proposal overwrites a stale proposal branch instead of failing on a
    # non-fast-forward. The base ref and every governed ref are never pushed to.
    if before_remote_write is not None:
        before_remote_write()
    _git(
        ["push", "--quiet", "--force", "origin", f"+{head_branch}:{head_branch}"],
        cwd=clone,
        auth=auth,
    )

    proposal = ContextProposal(
        repository=repository,
        base_ref=base_ref,
        head_branch=head_branch,
        path=path,
        title=title,
        body=body,
        commit_sha=commit_sha,
        manifest=merged,
    )
    open_pr = opener or (lambda ready: _default_opener(ready, token=token))
    if before_remote_write is not None:
        before_remote_write()
    pr_url = open_pr(proposal)
    return ContextProposal(**{**proposal.__dict__, "pr_url": pr_url})


def _short(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]
