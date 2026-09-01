"""The proposal-only Git-PR writer, against a real repository (hy-8b5h).

Real `git`, a real local repository, no mocking of the surface under test: the
boundary that matters -- it proposes a branch and never advances the base ref --
is only meaningful against actual refs. ADR 0012: the writer PROPOSES, a human
merges.
"""

from __future__ import annotations

import base64
import re

import pytest

from hyperset.context.errors import ContextValidationError
from hyperset.flywheel import git_pr
from hyperset.flywheel.git_pr import ContextProposal, propose_context_change
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository

DRAFT = {
    "definitions": [{"term": "churn", "statement": "customers lost in a period"}],
    "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
    "fields": [
        {
            "name": "churn_rate",
            "source_ref": "table:postgres:analytics.public.churn",
            "expression": "lost / total",
        }
    ],
}


def _recorder(calls):
    def _open(proposal: ContextProposal) -> str:
        calls.append(proposal)
        return "https://example.test/customer/context/pull/1"

    return _open


def test_the_proposal_body_keeps_remote_metadata_minimal():
    """Remote proposals carry task linkage, never human identity or review free text."""
    body = git_pr._proposal_body(
        {
            "proposer": "sub-x@https://iss.example",
            "task_id": "rt-x",
            "question": "private question",
            "evidence_summary": "private evidence",
            "feedback": "private feedback",
        },
        base_commit="c0ffee",
        base_ref="main",
    )
    assert "Review task: rt-x" in body
    assert all(
        value not in body
        for value in ("sub-x", "private question", "private evidence", "private feedback")
    )


def test_the_proposal_body_omits_proposer_and_review_text(tmp_path):
    """A remote PR is traceable to a task without exporting the caller or its evidence."""
    repo = make_repository(tmp_path)
    calls: list[ContextProposal] = []
    proposer = "auth0|abc123@https://issuer.example"
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder(calls),
        review={"task_id": "rt-0123456789abcdef", "proposer": proposer},
    )

    assert proposer not in proposal.body
    assert "Review task: rt-0123456789abcdef" in proposal.body
    assert proposal.body is calls[0].body  # the exact body handed to the opener
    # ...and the ref does NOT -- neither the subject nor the issuer leaks into the branch.
    assert "abc123" not in proposal.head_branch
    assert "issuer.example" not in proposal.head_branch
    assert proposal.head_branch.startswith("hyperset/proposal/rt-0123456789abcdef-")


def test_a_proposal_with_no_proposer_omits_the_line(tmp_path):
    """A direct propose with no proposer (no verified caller) simply omits the line --
    the field is optional, not a rendered `None`."""
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
        review={"task_id": "rt-0123456789abcdef"},
    )
    assert "Proposed by" not in proposal.body


def test_a_draft_is_proposed_as_a_branch_and_never_touches_base(tmp_path):
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    calls: list[ContextProposal] = []

    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder(calls),
    )

    # The base ref the customer governs is untouched: no merge, no push to it.
    assert git("rev-parse", "main", cwd=repo) == base_before
    # A new proposal branch exists on the origin, at the proposed commit.
    assert git("rev-parse", proposal.head_branch, cwd=repo) == proposal.commit_sha
    assert proposal.head_branch.startswith("hyperset/proposal/")
    # The PR was opened exactly once, with this proposal, and then it STOPPED.
    assert len(calls) == 1
    assert calls[0].head_branch == proposal.head_branch
    assert proposal.pr_url == "https://example.test/customer/context/pull/1"


def test_the_proposal_extends_the_manifest_without_removing_what_governs_it(tmp_path):
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
    )
    manifest = git("show", f"{proposal.head_branch}:{CONTEXT_PATH}/manifest.yaml", cwd=repo)

    # The draft is added...
    assert "churn" in manifest
    assert "analytics.public.churn" in manifest
    # ...and nothing the customer already governs is removed.
    assert "recognized_revenue" in manifest
    assert "finance_orders_daily" in manifest


def test_the_manifest_redacts_url_credentials_even_without_the_pii_guard(tmp_path, monkeypatch):
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    repo = make_repository(tmp_path)
    draft = {
        "definitions": [
            {
                "term": "churn",
                "statement": (
                    "customers lost in a period; see "
                    "https://alice:ghp_MANIFESTSECRET@example.com/context"
                ),
            }
        ],
    }
    proposal = propose_context_change(
        draft=draft,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
    )
    manifest = git("show", f"{proposal.head_branch}:{CONTEXT_PATH}/manifest.yaml", cwd=repo)
    assert "ghp_MANIFESTSECRET" not in manifest
    assert "alice:" not in manifest
    assert "https://example.com/context" in manifest


def test_a_first_proposal_creates_the_manifest_for_a_new_domain(tmp_path):
    # The flywheel builds governed context up from little or none: proposing for
    # a domain that has no manifest yet CREATES it (path + file), rather than
    # erroring "no manifest.yaml" (hy-gh-43). The seeded repo has no manifest at
    # this path, so this exercises the create-from-scratch branch.
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    calls: list[ContextProposal] = []

    proposal = propose_context_change(
        draft=DRAFT,
        domain="supply_chain",
        repository=str(repo),
        base_ref="main",
        path="domains/supply_chain",
        workdir=tmp_path / "work",
        opener=_recorder(calls),
    )

    # The base ref is untouched, and the PR opened exactly once.
    assert git("rev-parse", "main", cwd=repo) == base_before
    assert len(calls) == 1
    # The new manifest exists on the proposal branch, naming the new domain and
    # carrying the drafted definition.
    manifest = git("show", f"{proposal.head_branch}:domains/supply_chain/manifest.yaml", cwd=repo)
    assert "domain: supply_chain" in manifest
    assert "churn" in manifest


def test_a_reproposal_overwrites_its_own_stale_proposal_branch(tmp_path):
    # The proposal branch is content-hash-named, so re-proposing the same draft
    # targets the same branch. An earlier run may have left it behind; the push
    # is force-scoped to that ref, so the second proposal overwrites it instead
    # of failing on a non-fast-forward. The base ref is never force-pushed.
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    first = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "one",
        opener=_recorder([]),
    )
    # Same draft -> same branch name; the second run must not fail on it.
    second = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "two",
        opener=_recorder([]),
    )

    assert first.head_branch == second.head_branch
    assert git("rev-parse", second.head_branch, cwd=repo) == second.commit_sha
    # The base ref the customer governs is still untouched by either run.
    assert git("rev-parse", "main", cwd=repo) == base_before


def test_the_opener_returns_the_existing_pr_when_one_already_exists(monkeypatch):
    """A re-proposal whose PR is already open is idempotent: GitHub answers the
    create with 422, and the opener returns the existing PR's URL, not an error."""

    class _Post:
        status_code = 422

        @staticmethod
        def json():
            return {"message": "A pull request already exists for acme:branch."}

    class _Get:
        status_code = 200

        @staticmethod
        def json():
            return [{"html_url": "https://github.com/acme/context/pull/7"}]

    monkeypatch.setattr(git_pr.requests, "post", lambda url, **kw: _Post())
    monkeypatch.setattr(git_pr.requests, "get", lambda url, **kw: _Get())
    proposal = ContextProposal(
        repository="https://github.com/acme/context",
        base_ref="main",
        head_branch="hyperset/proposal/revenue-x",
        path="domains/revenue",
        title="t",
        body="b",
        commit_sha="deadbeef",
        manifest="definitions: []\n",
    )

    assert (
        git_pr._default_opener(proposal, token="ghp_x") == "https://github.com/acme/context/pull/7"
    )


def test_an_invalid_draft_is_refused_before_any_clone_or_push(tmp_path):
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    calls: list[ContextProposal] = []
    # A field reading a source it does not approve: refused by the manifest rules.
    invalid = {
        "definitions": [{"term": "churn", "statement": "x"}],
        "approved_sources": [{"ref": "table:postgres:a.b.c", "role": "primary"}],
        "fields": [{"name": "f", "source_ref": "table:postgres:z.z.z", "expression": "e"}],
    }

    with pytest.raises(ContextValidationError):
        propose_context_change(
            draft=invalid,
            domain="revenue",
            repository=str(repo),
            base_ref="main",
            path=CONTEXT_PATH,
            workdir=tmp_path / "work",
            opener=_recorder(calls),
        )

    assert git("rev-parse", "main", cwd=repo) == base_before
    assert calls == []  # no PR was opened
    assert git("branch", "--list", "hyperset/proposal/*", cwd=repo) == ""  # no branch pushed


def test_a_token_never_appears_in_any_git_subprocess_argv(tmp_path, monkeypatch):
    """A URL target's token authenticates the clone and push, and the credential
    -- the raw token AND its reversible base64 -- must NEVER appear in a
    subprocess argv, where it would land in /proc/<pid>/cmdline and process-exec
    auditing (hy-6haz). It travels only in the child ENVIRONMENT (git's
    GIT_CONFIG_* mechanism). Spies on the ACTUAL args and env passed to
    subprocess.run, not merely the header string handed to `_git`."""
    token = "ghp_secret_xyz"
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    repo = make_repository(tmp_path)
    calls: list[ContextProposal] = []
    seen: list[dict] = []
    real_run = git_pr.subprocess.run

    def _verb(argv):
        # The subcommand, wherever it sits -- robust to any `-c ...` flags before
        # it, so a leaking `-c http.extraheader=...` form is still classified as
        # the clone/push it is and fails on the argv assertion, not on lookup.
        for token_arg in argv[1:]:
            if token_arg in {"clone", "push"}:
                return token_arg
        return argv[-1]

    def _spy_run(argv, **kwargs):
        env = kwargs.get("env") or {}
        seen.append({"verb": _verb(argv), "argv": argv, "env": env})
        return real_run(argv, **kwargs)

    monkeypatch.setattr(git_pr.subprocess, "run", _spy_run)

    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        token=token,
        opener=_recorder(calls),
    )

    clone = next(call for call in seen if call["verb"] == "clone")
    push = next(call for call in seen if call["verb"] == "push")
    for call in (clone, push):
        flat_argv = " ".join(call["argv"])
        # Neither the raw token nor its base64 form is anywhere on the command line.
        assert token not in flat_argv
        assert b64 not in flat_argv
        # The credential rides in the environment instead, and the raw token is
        # base64-wrapped even there (never plaintext in any env value).
        assert call["env"].get("GIT_CONFIG_VALUE_0") == f"AUTHORIZATION: basic {b64}"
        assert all(token not in str(value) for value in call["env"].values())

    # And it is absent from the pushed content and the returned proposal.
    assert token not in proposal.manifest
    assert token not in repr(proposal)
    assert proposal.pr_url == "https://example.test/customer/context/pull/1"


def test_a_local_target_still_proposes_with_no_token(tmp_path, monkeypatch):
    """The local-path target keeps working with no token and no auth header
    threaded (hy-eji4): URL support is an added target TYPE, not a replacement."""
    repo = make_repository(tmp_path)
    captured: list[str | None] = []
    real_git = git_pr._git

    def _spy(args, *, cwd=None, timeout=120, auth=None):
        captured.append(auth)
        return real_git(args, cwd=cwd, timeout=timeout, auth=auth)

    monkeypatch.setattr(git_pr, "_git", _spy)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
    )

    assert proposal.head_branch.startswith("hyperset/proposal/")
    assert all(auth is None for auth in captured)  # no auth header on any git call


@pytest.mark.parametrize(
    "repository, expected",
    [
        ("https://github.com/acme/context", "acme/context"),
        ("https://github.com/acme/context.git", "acme/context"),
        ("git@github.com:acme/context.git", "acme/context"),
        ("ssh://git@github.com/acme/context", "acme/context"),
        ("/tmp/local/repo", None),
        ("https://gitlab.com/acme/context", None),
    ],
)
def test_owner_repo_is_parsed_from_github_remotes_only(repository, expected):
    assert git_pr._owner_repo(repository) == expected


def test_the_default_opener_sends_the_token_in_the_header_not_the_url(monkeypatch):
    """The default opener creates the PR via the GitHub REST API and sends the
    token only in the Authorization header -- never in the URL, body, or a
    command line (hy-eji4). No `gh` binary is invoked."""
    captured: dict = {}

    class _Response:
        status_code = 201

        @staticmethod
        def json():
            return {"html_url": "https://github.com/acme/context/pull/9"}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers") or {}
        captured["json"] = kwargs.get("json") or {}
        return _Response()

    monkeypatch.setattr(git_pr.requests, "post", _fake_post)
    proposal = ContextProposal(
        repository="https://github.com/acme/context",
        base_ref="main",
        head_branch="hyperset/proposal/revenue-x",
        path="domains/revenue",
        title="t",
        body="b",
        commit_sha="deadbeef",
        manifest="definitions: []\n",
    )

    url = git_pr._default_opener(proposal, token="ghp_secret_xyz")

    assert url == "https://github.com/acme/context/pull/9"
    assert captured["url"] == "https://api.github.com/repos/acme/context/pulls"
    assert captured["headers"]["Authorization"] == "Bearer ghp_secret_xyz"
    # the token is nowhere but the header
    assert "ghp_secret_xyz" not in captured["url"]
    assert "ghp_secret_xyz" not in str(captured["json"])


def test_the_writer_holds_no_approval_or_governed_writer():
    """Structural: the module cannot approve or advance governed context -- it
    imports none of those writers, and there is no approval call to reach."""
    for forbidden in (
        "PostgresReviewRepository",
        "PostgresGovernedContextRepository",
        "approve",
        "propose_version",
    ):
        assert not hasattr(git_pr, forbidden)


def test_an_engaged_pii_guard_without_presidio_refuses_the_proposal(tmp_path, monkeypatch):
    """hy-hbtz fail-closed at the proposal boundary: with the guard engaged and
    Presidio unhostable, the writer REFUSES -- nothing is committed or pushed and
    the base ref is untouched, rather than committing unredacted content."""
    from hyperset.flywheel.git_pr import GitProposalError
    from hyperset.security import pii

    monkeypatch.setattr(pii, "_engines", False)  # force the can't-host state on any seat
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    calls: list[ContextProposal] = []

    with pytest.raises(GitProposalError):
        propose_context_change(
            draft=DRAFT,
            domain="revenue",
            repository=str(repo),
            base_ref="main",
            path=CONTEXT_PATH,
            workdir=tmp_path / "work",
            opener=_recorder(calls),
        )

    assert git("rev-parse", "main", cwd=repo) == base_before
    assert calls == []  # no PR opened
    assert git("branch", "--list", "hyperset/proposal/*", cwd=repo) == ""  # no branch pushed


def test_the_pr_body_embeds_only_safe_review_linkage_and_source_commit(tmp_path):
    # The proposal PR names its review task and source commit, but keeps question/evidence
    # in Hyperset's local review store rather than exporting it to the remote.
    repo = make_repository(tmp_path)
    base_commit = git("rev-parse", "main", cwd=repo)
    review = {
        "task_id": "rt-abc123",
        "question": "How is churn defined?",
        "evidence_summary": "2 observed source(s) considered: table:orders, table:customers",
        "backlink": "Hyperset review task rt-abc123 -- fetch it with the get_review_task tool",
    }
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
        review=review,
    )
    assert "[review task rt-abc123]" in proposal.title
    body = proposal.body
    assert "Review task: rt-abc123" in body
    assert "How is churn defined?" not in body
    assert "2 observed source(s) considered" not in body
    assert f"Source commit: {base_commit}" in body
    assert "Backlink: Hyperset review task rt-abc123" in body
    # The proposal-only preamble (ADR 0012) is preserved, not replaced.
    assert "human Git merge is the only path to authority" in body


def test_a_propose_without_a_review_task_omits_metadata_but_keeps_the_source_commit(tmp_path):
    repo = make_repository(tmp_path)
    base_commit = git("rev-parse", "main", cwd=repo)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
    )
    assert "[review task" not in proposal.title  # no task -> the plain title
    assert "Review task:" not in proposal.body
    assert f"Source commit: {base_commit}" in proposal.body


def test_the_pr_body_is_still_guarded_and_fails_closed(tmp_path, monkeypatch):
    # The remote body remains behind the same fail-closed guard even though it is minimal.
    repo = make_repository(tmp_path)

    def _block(text, *, boundary):
        if boundary == "git_proposal" and "Review task" in text:
            raise git_pr.PiiError("blocked PII in the proposal body")
        return text

    monkeypatch.setattr(git_pr, "guard_text", _block)
    with pytest.raises(git_pr.GitProposalError):
        propose_context_change(
            draft=DRAFT,
            domain="revenue",
            repository=str(repo),
            base_ref="main",
            path=CONTEXT_PATH,
            workdir=tmp_path / "work",
            opener=_recorder([]),
            review={"task_id": "rt-x"},
        )


def test_the_pr_body_does_not_export_credential_bearing_review_text(tmp_path):
    # Review free text can contain a scheme://user:token@host, so it never enters the
    # remote body at all.
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
        review={
            "task_id": "rt-x",
            "question": "why does https://user:supersecret@warehouse.example/db fail?",
            "evidence_summary": "1 observed source(s) considered: https://svc:token@host/x",
        },
    )
    assert "supersecret" not in proposal.body
    assert "svc:token@" not in proposal.body
    assert "user:supersecret@" not in proposal.body
    assert "warehouse.example" not in proposal.body


def test_the_title_and_commit_message_redact_a_credential_url(tmp_path):
    # hy-w5gb #449 (unanimous bounce): the title interpolates the task id AND is the git
    # COMMIT MESSAGE (permanent history), so a credential in it must be redacted on BOTH
    # surfaces, not just the body.
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
        review={"task_id": "rt-https://user:supersecret@gateway.example/x"},
    )
    # The PR title is redacted...
    assert "supersecret" not in proposal.title
    assert "user:supersecret@" not in proposal.title
    # ...and so is the permanent commit message (read back from the pushed branch).
    subject = git("log", "-1", "--format=%s", proposal.head_branch, cwd=repo)
    assert "supersecret" not in subject
    assert "user:supersecret@" not in subject
    # The non-secret host survives on both.
    assert "gateway.example" in proposal.title and "gateway.example" in subject


def test_the_title_pii_guard_fails_closed_and_pushes_nothing(tmp_path, monkeypatch):
    # A slug-shape token/PII the URL redactor cannot see is caught by the PII guard, which
    # FAILS CLOSED before any commit or push -- so nothing leaks to the remote or history.
    repo = make_repository(tmp_path)

    def _block(text, *, boundary):
        if boundary == "git_proposal" and "PIILEAK" in text:
            raise git_pr.PiiError("blocked PII in a remote-bound proposal string")
        return text

    monkeypatch.setattr(git_pr, "guard_text", _block)
    with pytest.raises(git_pr.GitProposalError):
        propose_context_change(
            draft=DRAFT,
            domain="revenue",
            repository=str(repo),
            base_ref="main",
            path=CONTEXT_PATH,
            workdir=tmp_path / "work",
            opener=_recorder([]),
            review={"task_id": "rt-PIILEAK"},
        )
    # Nothing was pushed: no proposal branch exists on the origin, so the title/commit
    # never reached history.
    refs = git("for-each-ref", "--format=%(refname)", "refs/heads/", cwd=repo)
    assert "hyperset/proposal/" not in refs


def test_the_branch_ref_carries_no_domain_content(tmp_path):
    # hy-w5gb #449 round 4: a charset slug is ref-SAFE but not SECRET -- it keeps the
    # letters of a token/email. So the branch name carries NO domain content at all: it is
    # built from the (non-sensitive) review task id + a sha256 of the manifest. Proposing
    # with a domain that embeds a token AND an email must leave NEITHER in the pushed ref.
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue-supersecrettoken123-jane.doe@example.com",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
        review={"task_id": "rt-0123456789ab"},
    )
    # The token and the email are absent from head_branch AND the pushed ref (checked via
    # for-each-ref against the real remote), and the ref is refname-safe.
    refs = git("for-each-ref", "--format=%(refname)", "refs/heads/", cwd=repo)
    for surface in (proposal.head_branch, refs):
        assert "supersecrettoken123" not in surface
        assert "jane.doe" not in surface and "example.com" not in surface
        assert "@" not in surface
    # It is derived from the non-sensitive task id + content hash, and the branch exists.
    assert proposal.head_branch.startswith("hyperset/proposal/rt-0123456789ab-")
    assert git("rev-parse", proposal.head_branch, cwd=repo) == proposal.commit_sha


def test_a_propose_without_a_task_names_the_branch_by_content_hash_only(tmp_path):
    # No review task -> the branch is the manifest content hash alone (still no domain).
    repo = make_repository(tmp_path)
    proposal = propose_context_change(
        draft=DRAFT,
        domain="revenue-supersecrettoken123",
        repository=str(repo),
        base_ref="main",
        path=CONTEXT_PATH,
        workdir=tmp_path / "work",
        opener=_recorder([]),
    )
    assert proposal.head_branch.startswith("hyperset/proposal/")
    assert "supersecrettoken123" not in proposal.head_branch
    assert re.fullmatch(r"hyperset/proposal/[a-f0-9]+", proposal.head_branch)


def test_ref_slug_reduces_a_nonsensitive_id_to_a_safe_charset():
    # Unit-level: the real sanitizer, directly. It is ref-safety for a NON-sensitive id
    # (the task id), not secrecy -- a clean id is unchanged, an unsafe one collapses to
    # [a-z0-9-], an all-unsafe one falls back to a constant.
    assert git_pr._ref_slug("rt-0123abcd") == "rt-0123abcd"
    assert re.fullmatch(r"[a-z0-9-]+", git_pr._ref_slug("RT@ 1:/..x")) is not None
    assert "@" not in git_pr._ref_slug("a@b") and "/" not in git_pr._ref_slug("a/b")
    assert git_pr._ref_slug("@@@") == "context"
