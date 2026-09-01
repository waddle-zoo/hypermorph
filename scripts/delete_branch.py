#!/usr/bin/env python3
"""Delete a MERGED PR's head branch, from a name the FORGE gives at the moment of use.

hy-mlj0 -- a hazard witnessed one step short (#172). A branch-deletion argument
taken from a merge message or from scrollback is UNVERIFIED INPUT one step from a
destructive command. On #172 the refinery wrote a merge subject naming the WRONG
branch -- a LIVE ref belonging to another PR -- and had it deleted "the branch"
named there, it would have destroyed live work. Nothing in the path would have
stopped it: not a check, not a confirmation, not the branch's own PR.

This is the guarded boundary the deletion must pass through. You give it the PR
NUMBER -- the one value in a merge subject that was correct and that RESOLVES --
never a branch string, and it:

  1. reads ``head.ref`` from the FORGE at the moment of use, bound to that PR
     number (``gh api repos/{owner}/{repo}/pulls/{N} --jq .head.ref``), never from
     a message or scrollback (hy-mlj0 item 1, provenance);
  2. VALIDATES the name against a safe branch charset -- rejecting a leading dash
     (option injection), path traversal (``..``), whitespace, and every shell
     metacharacter -- so a hostile or malformed name can never reach a command
     (hy-mlj0, the charset half);
  3. asserts the ref tip is an ANCESTOR of the default branch: a merged branch is
     safe to delete, an unmerged one -- or a wrong name that somehow validated --
     is not. This is what makes the step self-verifying regardless of where the
     name came from, and it is the durable half (hy-mlj0 item 2);
  4. only then deletes the ref, passing the name as a single ARGUMENT (argv, never
     a shell string) to ``gh api -X DELETE .../git/refs/heads/{name}``.

Any failure at any step ABORTS the deletion and returns non-zero; the destructive
command never runs on an unverified name (fail-closed). The name is never
interpolated into a shell: this module runs no shell, only argv lists.
"""

from __future__ import annotations

import re
import subprocess
import sys

# A branch name safe to hand a destructive command as an ARGUMENT. A POSITIVE,
# anchored charset -- alnum plus `.`, `_`, `/`, `-` -- with the first character
# forced to alnum, so nothing outside it (a shell metacharacter, whitespace, a
# leading dash the command would read as an OPTION) can appear. This is a
# deliberately TIGHT subset of what git itself allows: a legal but exotic ref name
# is refused rather than passed through, because the caller can always rename, and
# the cost of a false reject is a message while the cost of a false accept is a
# destroyed ref.
_SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def is_safe_branch_name(name: str | None) -> bool:
    """Whether ``name`` is safe to pass a destructive command as an argument.

    Fail-closed: any of these is rejected -- an empty or over-long name; leading
    or trailing whitespace; a leading dash (``-D``, ``--force``: option
    injection); anything off the ``_SAFE_BRANCH`` charset (so ``;``, ``|``, ``&``,
    ``$(...)``, backticks, ``<>``, spaces, newlines cannot appear); path traversal
    (``..``); a ``/.`` dot-led component or a leading dot; a trailing ``/`` or
    ``.``; or a ``.lock`` suffix. Everything git's own refname rules and the shell
    would find dangerous is off-pattern here.
    """
    if not name or len(name) > 255:
        return False
    if name != name.strip():
        return False
    if not _SAFE_BRANCH.match(name):
        return False
    if ".." in name:
        return False
    if "/." in name:
        return False
    if name.endswith((".", "/", ".lock")):
        return False
    return True


# Names never deleted as a merge-cleanup, whatever the forge says. The default
# branch is refused separately (it depends on the argument), but a branch LITERALLY
# named for a shared/symbolic ref is refused unconditionally: deleting one is never
# what post-merge cleanup means, and the ancestor check cannot catch it -- the
# default branch is vacuously an ancestor of itself, so "merged into the default"
# is trivially true for it (adversary, hy-mlj0).
_RESERVED_BRANCHES = frozenset({"head", "main", "master"})


def deletable_branch(
    pr: str,
    *,
    default_branch: str,
    pr_head_ref,
    is_ancestor,
) -> str | None:
    """The head branch name it is SAFE to delete for ``pr``, or ``None``.

    Pure and injectable: ``pr_head_ref(pr) -> str | None`` reads the forge and
    ``is_ancestor(ref, default_branch) -> bool`` reads git, so both arms run in the
    tests with no network. The order is load-bearing -- the name is VALIDATED
    before it is ever handed to ``is_ancestor`` (a git command), so an unsafe name
    never reaches a command at all -- and every failure returns ``None`` so the
    caller aborts the deletion rather than run it blind.

    The DEFAULT BRANCH (and any reserved/shared ref name) is refused before the
    ancestor check, not by it: a merged PR whose forge ``head.ref`` is the default
    branch itself -- a cross-fork PR opened from ``someone:main`` -- would otherwise
    pass, because a commit is vacuously its own ancestor, and the guard would hand
    ``DELETE .../heads/main`` to the forge (adversary, hy-mlj0). Relying on the
    forge's own default-branch protection to catch that is the anti-pattern this
    boundary exists to remove, so it fails closed here.
    """
    ref = pr_head_ref(pr)
    if not is_safe_branch_name(ref):
        return None
    if ref == default_branch or ref.casefold() in _RESERVED_BRANCHES:
        return None
    if not is_ancestor(ref, default_branch):
        return None
    return ref


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def _pr_head_ref(pr: str) -> str | None:
    """``head.ref`` for ``pr``, read from the forge at the moment of use. ``pr`` is
    the trusted value and is required to be digits before it reaches the URL."""
    if not pr.isdigit():
        return None
    result = _gh(["api", f"repos/{{owner}}/{{repo}}/pulls/{pr}", "--jq", ".head.ref"])
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    return ref or None


def _is_ancestor(ref: str, default_branch: str) -> bool:
    """Whether the remote tip of ``ref`` is an ancestor of the default branch --
    i.e. the branch is merged and safe to delete. Fetches the default branch, then
    the ref (so ``FETCH_HEAD`` is the ref tip), and asks git; any git failure is
    False, so an unreadable ref fails toward NOT deleting."""
    if subprocess.run(["git", "fetch", "-q", "origin", default_branch]).returncode != 0:
        return False
    if subprocess.run(["git", "fetch", "-q", "origin", ref]).returncode != 0:
        return False
    tip = subprocess.run(
        ["git", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True
    ).stdout.strip()
    base = subprocess.run(
        ["git", "rev-parse", f"origin/{default_branch}"], capture_output=True, text=True
    ).stdout.strip()
    if not tip or not base:
        return False
    return subprocess.run(["git", "merge-base", "--is-ancestor", tip, base]).returncode == 0


def delete_branch(pr: str, *, default_branch: str = "main") -> int:
    """Delete PR ``pr``'s head branch through the guarded boundary. 0 on delete,
    2 when the deletion is refused (no safe, merged name), 1 on a forge error."""
    if not pr.isdigit():
        print(f"ABORT: PR must be a number, got {pr!r}; branch NOT deleted", file=sys.stderr)
        return 2
    target = deletable_branch(
        pr, default_branch=default_branch, pr_head_ref=_pr_head_ref, is_ancestor=_is_ancestor
    )
    if target is None:
        print(
            f"ABORT: no forge head.ref for PR #{pr} that is a safe name AND merged into "
            f"{default_branch}; branch NOT deleted",
            file=sys.stderr,
        )
        return 2
    result = _gh(["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{target}"])
    if result.returncode != 0:
        print(f"! delete of {target} (PR #{pr}) failed: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"deleted merged branch {target} (PR #{pr})")
    return 0


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print("usage: delete_branch.py <pr-number> [default-branch]", file=sys.stderr)
        return 2
    default_branch = argv[1] if len(argv) == 2 else "main"
    return delete_branch(argv[0], default_branch=default_branch)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main(sys.argv[1:]))
