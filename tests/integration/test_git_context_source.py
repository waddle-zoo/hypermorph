"""Real Git behavior for the configured context source (hy-gh-43).

These build actual repositories with the `git` CLI rather than mocking it:
the acceptance criteria are about exact commits, refs, and no-op detection,
and a fake would only prove that the fake behaves as written.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from hyperset.context.errors import GitReadError
from hyperset.context.git import MAX_FILE_BYTES, MAX_FILES, GitContextReader


def _mirror_dir(tmp_path: Path, repository: Path) -> Path:
    """Where `GitContextReader(tmp_path / 'cache')` keeps the shared mirror for
    `repository` (hy-gh-288 tests inspect the fetched object store)."""
    return tmp_path / "cache" / hashlib.sha256(str(repository).encode()).hexdigest()[:16]


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "git_context" / "revenue"
CONTEXT_PATH = "domains/revenue"


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def make_repository(root: Path) -> Path:
    """A real repository holding the checked-in revenue context fixture."""
    repository = root / "customer-repo"
    (repository / CONTEXT_PATH).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    for path in sorted(FIXTURE_DIR.iterdir()):
        shutil.copy(path, repository / CONTEXT_PATH / path.name)
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add revenue context", cwd=repository)
    return repository


def read(tmp_path: Path, repository: Path, *, ref: str = "main", path: str = CONTEXT_PATH):
    return GitContextReader(tmp_path / "cache").read(repository=str(repository), ref=ref, path=path)


@pytest.mark.integration
def test_reads_the_exact_commit_currently_visible_at_the_ref(tmp_path):
    repository = make_repository(tmp_path)

    result = read(tmp_path, repository)

    assert result.commit_sha == git("rev-parse", "HEAD", cwd=repository)
    assert result.ref == "main"
    assert result.path == CONTEXT_PATH
    assert sorted(result.files) == ["context.md", "evals.yaml", "manifest.yaml"]
    # Original content, byte for byte -- not a re-serialized projection.
    assert result.files["manifest.yaml"] == (FIXTURE_DIR / "manifest.yaml").read_text()


@pytest.mark.integration
def test_a_local_checkout_is_trusted_only_in_the_mirrors_fetch_config(tmp_path):
    repository = make_repository(tmp_path)

    read(tmp_path, repository)

    fetch_config = _mirror_dir(tmp_path, repository) / "hyperset-fetch.gitconfig"
    assert fetch_config.is_file()
    assert git(
        "config", "--file", str(fetch_config), "--get", "safe.directory", cwd=tmp_path
    ) == str((repository / ".git").resolve())
    assert "*" not in fetch_config.read_text()


@pytest.mark.integration
def test_a_new_commit_changes_the_resolved_sha_and_content(tmp_path):
    repository = make_repository(tmp_path)
    before = read(tmp_path, repository)

    manifest = repository / CONTEXT_PATH / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("inner", "left"), encoding="utf-8")
    git("commit", "--quiet", "-am", "loosen the join", cwd=repository)

    after = read(tmp_path, repository)
    assert after.commit_sha != before.commit_sha
    assert "type: left" in after.files["manifest.yaml"]
    # The earlier commit is still readable: history is not rewritten by a read.
    assert read(tmp_path, repository, ref=before.commit_sha).files == before.files


@pytest.mark.integration
def test_an_unchanged_repository_resolves_to_the_same_commit(tmp_path):
    repository = make_repository(tmp_path)

    assert read(tmp_path, repository).commit_sha == read(tmp_path, repository).commit_sha


@pytest.mark.integration
def test_a_ci_git_bundle_is_read_without_a_runtime_checkout(tmp_path):
    repository = make_repository(tmp_path)
    bundle = tmp_path / "revenue-context.bundle"
    git("bundle", "create", str(bundle), "main", cwd=repository)

    result = read(tmp_path, bundle)

    assert result.commit_sha == git("rev-parse", "HEAD", cwd=repository)
    assert result.files["context.md"] == (FIXTURE_DIR / "context.md").read_text()


@pytest.mark.integration
def test_a_tag_resolves_and_pins_its_commit(tmp_path):
    repository = make_repository(tmp_path)
    tagged = git("rev-parse", "HEAD", cwd=repository)
    git("tag", "revenue-v1", cwd=repository)
    (repository / CONTEXT_PATH / "context.md").write_text("later edit\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "later edit", cwd=repository)

    assert read(tmp_path, repository, ref="revenue-v1").commit_sha == tagged


@pytest.mark.integration
def test_a_missing_ref_fails_without_returning_context(tmp_path):
    repository = make_repository(tmp_path)

    # Since the reader now fetches ONLY the configured ref (hy-gh-288), a ref the
    # repository does not have fails the fetch itself -- earlier and cheaper than
    # fetching everything and finding nothing. The message names the ref the
    # caller asked for; it is not pinned to git's exact wording, which differs by
    # version (CI git 2.54 vs local 2.39; hy-t0ot).
    with pytest.raises(GitReadError, match="release"):
        read(tmp_path, repository, ref="release")


@pytest.mark.integration
def test_a_path_holding_no_files_fails(tmp_path):
    repository = make_repository(tmp_path)

    with pytest.raises(GitReadError, match="holds no files"):
        read(tmp_path, repository, path="domains/marketing")


@pytest.mark.integration
def test_an_unreachable_repository_fails(tmp_path):
    with pytest.raises(GitReadError, match="git fetch failed"):
        read(tmp_path, tmp_path / "not-a-repository")


@pytest.mark.integration
def test_a_fetch_failure_names_the_cause_not_gits_boilerplate_trailer(tmp_path):
    # hy-ppufd: git appends "Please make sure you have the correct access rights\n
    # and the repository exists." AFTER the real cause, so reporting the LAST stderr
    # line surfaced only that trailer -- "git fetch failed: and the repository
    # exists." -- the malformed /admin/sources Validate message the overseer saw.
    # The message must name the real cause and must NOT be only the trailer.
    with pytest.raises(GitReadError) as excinfo:
        read(tmp_path, tmp_path / "not-a-repository")
    message = str(excinfo.value)
    # The informative cause git prints first is preserved...
    assert "Could not read from remote repository" in message
    # ...and the generic trailer is not what the message reduces to.
    assert not message.rstrip().endswith("and the repository exists.")
    assert "and the repository exists." not in message


@pytest.mark.integration
def test_an_oversized_file_fails_rather_than_loading_an_arbitrary_tree(tmp_path):
    repository = make_repository(tmp_path)
    (repository / CONTEXT_PATH / "dump.csv").write_text("x" * (MAX_FILE_BYTES + 1))
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "accidental data dump", cwd=repository)

    with pytest.raises(GitReadError, match="the limit is"):
        read(tmp_path, repository)


@pytest.mark.integration
def test_repository_codeowners_are_captured_for_the_configured_path(tmp_path):
    repository = make_repository(tmp_path)
    (repository / "CODEOWNERS").write_text(
        "* @org/data-platform\ndomains/revenue/ @org/finance-analytics\n", encoding="utf-8"
    )
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "declare owners", cwd=repository)

    # Last matching rule wins, and nothing is invented when no rule matches.
    assert read(tmp_path, repository).repository_owner_refs == ["@org/finance-analytics"]


@pytest.mark.integration
def test_no_codeowners_means_no_repository_owners(tmp_path):
    assert read(tmp_path, make_repository(tmp_path)).repository_owner_refs == []


# --- hy-gh-288: fetch only the configured ref; a configurable file cap ---


@pytest.mark.integration
def test_only_the_configured_ref_is_fetched_not_the_whole_repository(tmp_path):
    """Fetching every head and tag was a full clone in all but name (hy-gh-288).
    A commit that lives only on another branch must not enter the mirror when the
    configured ref is main -- cost proportional to the context, not the repo."""
    repository = make_repository(tmp_path)
    git("checkout", "-q", "-b", "other", cwd=repository)
    (repository / CONTEXT_PATH / "only_on_other.txt").write_text("x\n")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "unique commit on other", cwd=repository)
    other_only = git("rev-parse", "HEAD", cwd=repository)
    git("checkout", "-q", "main", cwd=repository)

    read(tmp_path, repository, ref="main")

    mirror = _mirror_dir(tmp_path, repository)
    present = subprocess.run(
        ["git", "-C", str(mirror), "cat-file", "-e", other_only], capture_output=True
    )
    assert present.returncode != 0, "other branch objects were pulled: fetch is not ref-scoped"


@pytest.mark.integration
def test_two_sources_on_one_repository_share_the_mirror(tmp_path):
    """The shared object store is preserved (hy-gh-288): two configured sources on
    one repository, at different refs, resolve from ONE mirror -- the per-ref
    fetch narrows what is pulled without breaking the sharing the design wants."""
    repository = make_repository(tmp_path)
    tagged = git("rev-parse", "HEAD", cwd=repository)
    git("tag", "v1", cwd=repository)
    git("commit", "--quiet", "--allow-empty", "-m", "move main past the tag", cwd=repository)

    reader = GitContextReader(tmp_path / "cache")
    main_read = reader.read(repository=str(repository), ref="main", path=CONTEXT_PATH)
    tag_read = reader.read(repository=str(repository), ref="v1", path=CONTEXT_PATH)

    assert tag_read.commit_sha == tagged
    assert main_read.commit_sha == git("rev-parse", "main", cwd=repository)
    assert main_read.commit_sha != tag_read.commit_sha
    # One mirror directory for the repository -- the object store is shared.
    assert _mirror_dir(tmp_path, repository).exists()


def _add_wide_dir(repository: Path, count: int) -> str:
    wide = "domains/wide"
    (repository / wide).mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (repository / wide / f"f{i:03d}.txt").write_text(f"file {i}\n")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", f"a {count}-file directory", cwd=repository)
    return wide


@pytest.mark.integration
def test_the_file_count_cap_is_configurable_by_env(tmp_path, monkeypatch):
    """MAX_FILES is no longer hardcoded (hy-gh-288): the same directory that fails
    the default cap reads once the cap is raised, and fails again at a lowered
    one -- the bound is what the operator configured, not a constant."""
    repository = make_repository(tmp_path)
    wide = _add_wide_dir(repository, MAX_FILES + 1)

    monkeypatch.delenv("HYPERSET_CONTEXT_MAX_FILES", raising=False)
    with pytest.raises(GitReadError, match=f"more than {MAX_FILES}"):
        read(tmp_path, repository, path=wide)

    monkeypatch.setenv("HYPERSET_CONTEXT_MAX_FILES", str(MAX_FILES + 10))
    result = read(tmp_path, repository, path=wide)
    assert len(result.files) == MAX_FILES + 1

    monkeypatch.setenv("HYPERSET_CONTEXT_MAX_FILES", "1")
    with pytest.raises(GitReadError, match="more than 1"):
        read(tmp_path, repository, path=wide)


@pytest.mark.integration
def test_a_non_positive_or_non_integer_file_cap_fails_loudly(tmp_path, monkeypatch):
    """A cap the operator thinks they raised but did not is the quiet failure the
    cap exists to prevent, so a bad value refuses rather than reverting to 50."""
    repository = make_repository(tmp_path)

    monkeypatch.setenv("HYPERSET_CONTEXT_MAX_FILES", "lots")
    with pytest.raises(GitReadError, match="HYPERSET_CONTEXT_MAX_FILES"):
        read(tmp_path, repository)

    monkeypatch.setenv("HYPERSET_CONTEXT_MAX_FILES", "0")
    with pytest.raises(GitReadError, match=">= 1"):
        read(tmp_path, repository)


@pytest.mark.integration
def test_a_name_that_is_both_a_branch_and_a_tag_resolves_to_the_branch(tmp_path):
    """Branch BEFORE tag (hy-gh-288). A single `+{ref}:` fetch makes the remote
    DWIM `{ref}` tag-first, inverting the fetch-everything path's branch-first
    precedence -- a silent wrong-commit regression on a name that is both. The
    per-candidate fetch (heads, then tags) must pin the BRANCH's commit."""
    repository = make_repository(tmp_path)
    branch_commit = git("rev-parse", "HEAD", cwd=repository)
    git("branch", "shared", cwd=repository)  # refs/heads/shared at branch_commit
    (repository / CONTEXT_PATH / "context.md").write_text("moved past\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "advance main", cwd=repository)
    git("tag", "shared", cwd=repository)  # refs/tags/shared at a DIFFERENT commit
    tag_commit = git("rev-parse", "HEAD", cwd=repository)
    assert branch_commit != tag_commit

    result = read(tmp_path, repository, ref="shared")

    assert result.commit_sha == branch_commit, "branch==tag collision resolved to the tag"


@pytest.mark.integration
def test_a_bare_sha_ref_resolves_where_served_and_an_absent_one_fails_gracefully(tmp_path):
    """The documented config is a branch or tag; a bare commit sha is a narrowing
    (hy-gh-288). It still works where the transport serves want-sha -- a local
    repository does -- and an absent one fails with a clear error, never a crash."""
    repository = make_repository(tmp_path)
    sha = git("rev-parse", "HEAD", cwd=repository)

    assert read(tmp_path, repository, ref=sha).commit_sha == sha

    with pytest.raises(GitReadError):
        read(tmp_path, repository, ref="deadbeef" * 5)


@pytest.mark.integration
def test_an_adapter_corpus_is_read_at_its_commit_and_projected(tmp_path):
    """A customer corpus carrying a context-adapter.yaml is read at its OWN commit
    and projected into v0 context (hy-s8up, #283). The provenance win: the commit
    the reader resolves is the reviewed commit in the CUSTOMER's repository, so a
    snapshot taken from it points at that commit, not a projector's build artifact.
    """
    from hyperset.context.adapter.apply import apply_adapter, has_adapter

    adapter = (
        "schema_version: 1\nadapter: acme\n"
        "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
        "map:\n  domain: '$.urn | slug'\n  title: '$.title'\n"
        "  owners: \"$.owners[*] | prefix('team:')\"\n"
    )
    project = (
        "---\nurn: Revenue By Region\ntitle: Revenue by Region\nowners:\n  - finance-data\n---\n"
        "# Revenue by Region\n\nHow revenue is governed.\n"
    )
    repository = tmp_path / "customer-repo"
    path = "docs/revenue"
    (repository / path).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    (repository / path / "context-adapter.yaml").write_text(adapter, encoding="utf-8")
    (repository / path / "project.md").write_text(project, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add adapter corpus", cwd=repository)
    corpus_commit = git("rev-parse", "HEAD", cwd=repository)

    result = read(tmp_path, repository, path=path)

    assert has_adapter(result.files)
    document = apply_adapter(result.files)
    assert document.domain == "revenue-by-region"
    assert document.title == "Revenue by Region"
    assert document.owner_refs == ["team:finance-data"]
    # The resolved commit IS the customer corpus commit -- the provenance a snapshot
    # would carry points at the reviewed commit, not a build artifact.
    assert result.commit_sha == corpus_commit
