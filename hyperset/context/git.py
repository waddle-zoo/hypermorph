"""Read-only Git access for the configured context source (hy-gh-43).

Uses the `git` CLI over a local bare mirror rather than a Git library: the
same code path reads a local repository, a remote URL, or a CI-produced Git
bundle, the exact commands are auditable, and no new dependency is added to
read what is ultimately four small text files. A Git bundle is useful when a
runtime image ships the source without a checkout or `.git` directory: it
still carries the exact commit and tree that the reader records.

Nothing here writes to the customer's repository. The mirror under
`cache_dir` is a fetch-only cache; the authority is the commit it fetched,
and every read states which commit it read.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from hyperset.config.runtime import active_settings
from hyperset.context.errors import GitReadError

# A v0 context directory is a handful of small text files. The cap exists so
# a mis-configured path (a repository root, a data directory) fails loudly
# instead of loading an arbitrary tree into Postgres.
MAX_FILE_BYTES = 1_000_000
# The default file-count cap; the effective limit is read from the environment
# by `_max_files` (hy-gh-288), so a corpus with a larger-but-still-bounded
# context directory can raise it without a code change, and a hostile path still
# fails loudly at whatever bound is configured.
MAX_FILES = 50
_MAX_FILES_ENV = "HYPERSET_CONTEXT_MAX_FILES"


def _max_files() -> int:
    """The configured file-count cap, defaulting to `MAX_FILES` (hy-gh-288).

    Read at use time rather than import time so a test or a deployment can set it
    without re-importing the module. A non-integer or non-positive value is a
    configuration error and fails loudly rather than silently reverting to the
    default -- a cap the operator thinks they raised but did not is the same class
    of quiet failure the cap itself exists to prevent.

    Migrated to the settings object (hy-tc4o): when the serving process loaded a config the cap
    comes from `context.max_files` (a legacy env var was folded in as a deprecation-warned
    override), and the same value resolves. When startup has not run -- a unit test, a
    non-serving CLI call -- the legacy env read below is the exact prior behaviour."""
    settings = active_settings()
    if settings is not None:
        configured = settings.get("context", {}).get("max_files")
        return configured if configured is not None else MAX_FILES
    raw = os.environ.get(_MAX_FILES_ENV, "").strip()
    if not raw:
        return MAX_FILES
    try:
        value = int(raw)
    except ValueError as exc:
        raise GitReadError(f"{_MAX_FILES_ENV} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise GitReadError(f"{_MAX_FILES_ENV} must be >= 1, got {value}")
    return value


# Where repository-level ownership conventions live, in the order GitHub
# itself resolves them. Owner metadata is *captured*, never invented: if no
# CODEOWNERS entry matches the configured path, the read carries none.
CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class GitContextRead:
    """One read of the configured repository/ref/path at an exact commit.

    `files` maps a path relative to the configured `path` to its exact file
    content. `repository_owner_refs` is whatever CODEOWNERS said about that
    path at this commit -- empty when the repository states nothing.
    """

    repository: str
    ref: str
    path: str
    commit_sha: str
    committed_at: datetime
    files: dict[str, str]
    repository_owner_refs: list[str] = field(default_factory=list)


# Git appends an identical transport trailer to every unreachable / permission /
# missing-repo failure -- "Please make sure you have the correct access rights\nand
# the repository exists." -- AFTER the line that names the real cause. Reporting only
# the LAST stderr line (the old code) therefore surfaced just that boilerplate ("git
# fetch failed: and the repository exists.") and dropped the actual reason, which is
# exactly the malformed message an operator saw on /admin/sources Validate (hy-ppufd).
# Drop the boilerplate lines and keep the meaningful ones -- "Could not read from
# remote repository.", an HTTP status, a "does not appear to be a git repository" --
# so the message names the cause. We never drop a line we do not recognise, so a
# phrasing this list misses degrades to MORE detail, never less.
_GIT_BOILERPLATE = (
    "please make sure you have the correct access rights",
    "and the repository exists.",
)


def _git_error_detail(stderr: bytes) -> str:
    """The informative part of a failed git command's stderr: every non-empty line
    that is not git's generic access-rights/repository-exists trailer, joined. Falls
    back to 'no output' when git said nothing."""
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines()]
    meaningful = [line for line in lines if line and line.lower() not in _GIT_BOILERPLATE]
    return "; ".join(meaningful) if meaningful else "no output"


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    git_config_global: Path | None = None,
) -> str:
    env = os.environ.copy()
    # Never block a sync on an interactive credential prompt: a repository
    # needing credentials must fail as an error the operator can see.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    if git_config_global is not None:
        env["GIT_CONFIG_GLOBAL"] = str(git_config_global)
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment guard
        raise GitReadError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitReadError(f"git {args[0]} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise GitReadError(f"git {args[0]} failed: {_git_error_detail(result.stderr)}")
    return result.stdout.decode("utf-8", "replace")


def _run_bytes(args: list[str], *, cwd: Path, timeout: int = 120) -> bytes:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, env=env, timeout=timeout
    )
    if result.returncode != 0:
        raise GitReadError(f"git {args[0]} failed: {_git_error_detail(result.stderr)}")
    return result.stdout


def _ref_dest(ref: str) -> str:
    """The local ref a single-ref fetch of `ref` lands in (hy-gh-288).

    Namespaced under `refs/hyperset/` and keyed by a hash of the configured ref,
    so any ref string (a branch with slashes, a tag, a raw revision) maps to a
    valid local ref name, and two sources fetching DIFFERENT refs into one shared
    mirror never write the same slot. Same ref -> same dest, so a re-sync is
    idempotent."""
    return "refs/hyperset/" + hashlib.sha256(ref.encode()).hexdigest()[:16]


def normalize_path(path: str) -> str:
    """`domains/revenue/`, `/domains/revenue`, and `domains/revenue` are the
    same configured path; store and compare exactly one of them."""
    return path.strip().strip("/")


class GitContextReader:
    """Fetch-only mirror of one or more configured repositories."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._cache_dir = Path(cache_dir)

    def read(self, *, repository: str, ref: str, path: str) -> GitContextRead:
        repository = repository.strip()
        ref = ref.strip()
        path = normalize_path(path)
        if not repository or repository.startswith("-"):
            raise GitReadError(f"invalid repository {repository!r}")
        if not ref or ref.startswith("-"):
            raise GitReadError(f"invalid ref {ref!r}")
        if not path:
            raise GitReadError("a context path is required; the repository root is not a context")

        # Validate the file-count cap BEFORE any network I/O: a bad
        # HYPERSET_CONTEXT_MAX_FILES is a configuration error the operator should
        # see immediately, not after a wasted fetch (hy-gh-288).
        max_files = _max_files()

        mirror = self._mirror(repository)
        self._fetch(mirror, repository, ref)
        commit_sha = self._resolve(mirror, ref)
        committed_at = datetime.fromisoformat(
            _run(["show", "-s", "--format=%cI", commit_sha], cwd=mirror).strip()
        )
        files = self._read_tree(mirror, commit_sha, path, max_files)
        if not files:
            raise GitReadError(f"path {path!r} holds no files at commit {commit_sha}")
        return GitContextRead(
            repository=repository,
            ref=ref,
            path=path,
            commit_sha=commit_sha,
            committed_at=committed_at,
            files=files,
            repository_owner_refs=self._codeowners(mirror, commit_sha, path),
        )

    def _mirror(self, repository: str) -> Path:
        # Keyed by the repository string, so two configured sources on the same
        # repository share ONE object store (hy-gh-43). Preserved deliberately
        # (hy-gh-288): the fetch below narrows to the one configured ref, but the
        # store stays shared, so sources on one repo still reuse each other's
        # objects. This method now only ensures the store exists; the ref-scoped
        # fetch is `_fetch`.
        mirror = self._cache_dir / hashlib.sha256(repository.encode()).hexdigest()[:16]
        if not (mirror / "HEAD").exists():
            mirror.mkdir(parents=True, exist_ok=True)
            _run(["init", "--bare", "--quiet", str(mirror)])
            _run(["remote", "add", "origin", "--", repository], cwd=mirror)
        else:
            _run(["remote", "set-url", "origin", "--", repository], cwd=mirror)
        return mirror

    def _fetch(self, mirror: Path, repository: str, ref: str) -> None:
        """Fetch ONLY the configured ref, shallowly (hy-gh-288).

        `_resolve` only ever looks for this one ref, so fetching every head and
        tag was a full clone in all but name -- re-run every sync, and ruinous on
        a monorepo-hosted corpus. `--depth 1` bounds it to the tip commit and its
        tree, which is all a read at the tip needs (content, `committed_at`,
        CODEOWNERS -- no history walk). Cost is proportional to the CONTEXT now,
        not the repository.

        Branch BEFORE tag. A bare `+{ref}:` refspec would make the REMOTE resolve
        `ref` in git's DWIM order, which peels TAGS BEFORE HEADS -- the opposite of
        the precedence the fetch-everything `_resolve` used to apply (it tried
        `refs/heads/{ref}` first). A name that is BOTH a branch and a tag must
        still pin the BRANCH's commit, so the candidates are fetched explicitly in
        that order -- `refs/heads/{ref}`, then `refs/tags/{ref}` only if the branch
        is absent, then `{ref}` as given (a full `refs/...` ref, or a bare sha the
        transport serves) -- into ONE per-ref dest, first success winning. No
        stderr/version-string parsing decides precedence; the candidate ORDER
        does.

        Fetched into a PER-REF local ref rather than `FETCH_HEAD`: `FETCH_HEAD` is
        a single slot two sources on one shared mirror would clobber between a
        read's fetch and its resolve, and the per-ref dest keyed by `_ref_dest`
        gives each configured ref its own slot -- no cross-source clobber. This is
        NOT concurrent sync: two `--depth 1` fetches into the one shared bare
        mirror serialize on `.git/shallow.lock`, and a loser fails loud
        (`GitReadError`) rather than corrupting the store.

        These dests ACCUMULATE: one `refs/hyperset/<hash>` per distinct ref-string
        ever fetched into this mirror, and nothing here prunes them (the reader is
        per-read and does not hold the source registry, so it cannot know which
        dests are still configured). A source reconfigured from ref A to ref B
        leaves A's dead ref and its shallow tip behind. The leak is BOUNDED -- one
        dead ref plus one shallow commit per retired ref-string, objects shared
        with the live refs -- and v0-acceptable; a registry-driven prune is future
        work if it ever matters.

        `--no-tags` stops the auto-follow of unrelated tags; `--depth 1` is
        ignored (harmless warning) on a bundle or local transport, which are not
        the monorepo cost.

        `ref` is a BRANCH OR TAG NAME -- what the config advertises (`hyperset
        context add --ref`, "Branch or tag"). A bare commit SHA is a NARROWING
        (hy-gh-288): fetching all heads and tags used to leave a SHA resolvable if
        it was reachable from any of them, and asking for exactly one ref does
        not. A SHA the server will serve by `want-sha` still works (a local
        transport always does) via the final `{ref}` candidate; one it will not
        fails with a clear error rather than silently -- graceful, and off the
        documented branch/tag path.
        """
        # Docker Desktop presents a host bind mount as root-owned inside this unprivileged
        # container. Git then refuses even a READ from the explicitly configured local
        # repository as "dubious ownership". Trust only that exact configured repository's
        # git dir, in a mirror-local GLOBAL config (safe.directory is ignored outside a
        # protected config scope). Never use the unsafe `*` wildcard and never write to the
        # customer's checkout. Remote URLs and bundle files need no exception.
        fetch_config = None
        repository_path = Path(repository)
        if repository_path.is_dir():
            safe_directory = repository_path / ".git"
            if not safe_directory.is_dir():
                safe_directory = repository_path
            safe_directory = safe_directory.resolve()
            fetch_config = mirror / "hyperset-fetch.gitconfig"
            _run(["config", "--file", str(fetch_config), "safe.directory", str(safe_directory)])

        dest = _ref_dest(ref)
        last: GitReadError | None = None
        for candidate in (f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
            try:
                _run(
                    [
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        "--depth",
                        "1",
                        "origin",
                        f"+{candidate}:{dest}",
                    ],
                    cwd=mirror,
                    git_config_global=fetch_config,
                )
                return
            except GitReadError as exc:
                last = exc
        assert last is not None
        raise last

    def _resolve(self, mirror: Path, ref: str) -> str:
        """The exact commit the just-fetched ref points at, peeled through an
        annotated tag. `_fetch` created the per-ref local ref this reads."""
        dest = _ref_dest(ref)
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{dest}^{{commit}}"],
            cwd=str(mirror),
            capture_output=True,
        )
        if result.returncode == 0:
            return result.stdout.decode().strip()
        raise GitReadError(f"ref {ref!r} does not resolve to a commit")

    def _read_tree(
        self, mirror: Path, commit_sha: str, path: str, max_files: int
    ) -> dict[str, str]:
        listing = _run(["ls-tree", "-r", "-l", "-z", commit_sha, "--", f"{path}/"], cwd=mirror)
        files: dict[str, str] = {}
        for entry in listing.split("\0"):
            if not entry:
                continue
            meta, _, full_path = entry.partition("\t")
            _mode, obj_type, obj_id, size = meta.split()
            if obj_type != "blob":
                # Submodules and nested trees are not v0 context content.
                continue
            if int(size) > MAX_FILE_BYTES:
                raise GitReadError(f"{full_path} is {size} bytes; the limit is {MAX_FILE_BYTES}")
            if len(files) >= max_files:
                raise GitReadError(f"path {path!r} holds more than {max_files} files")
            blob = _run_bytes(["cat-file", "blob", obj_id], cwd=mirror)
            try:
                files[full_path[len(path) + 1 :]] = blob.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GitReadError(f"{full_path} is not UTF-8 text") from exc
        return files

    def _codeowners(self, mirror: Path, commit_sha: str, path: str) -> list[str]:
        for candidate in CODEOWNERS_PATHS:
            try:
                content = _run_bytes(
                    ["cat-file", "blob", f"{commit_sha}:{candidate}"], cwd=mirror
                ).decode("utf-8", "replace")
            except GitReadError:
                continue
            return _codeowners_for(content, path)
        return []


def _codeowners_for(content: str, path: str) -> list[str]:
    """Owner refs whose CODEOWNERS pattern covers the configured path.

    Deliberately narrow: exact/prefix patterns and `*` globs, last matching
    rule wins as in GitHub's own precedence. Anything this does not
    understand contributes no owner at all, because a wrong owner is worse
    than a missing one.
    """
    owners: list[str] = []
    target = f"{path}/"
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *entries = line.split()
        if not entries:
            continue
        pattern = pattern.lstrip("/")
        matched = (
            target.startswith(pattern.rstrip("*"))
            if pattern.endswith("*") or pattern.endswith("/")
            else fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(target, pattern)
        )
        if matched:
            owners = list(entries)
    return owners
