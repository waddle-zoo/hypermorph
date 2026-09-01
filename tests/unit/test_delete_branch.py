"""A branch deletion validates its argument at the boundary, and fails closed.

hy-mlj0: a branch-deletion argument taken from a merge message / scrollback is
unverified input one step from a destructive command (#172: the refinery wrote a
merge subject naming the WRONG, live branch; deleting "the branch" named there
would have destroyed live work). ``delete_branch`` sources the name from the
FORGE bound to the PR number, validates its charset, and asserts it is merged
(ancestor of the default branch) BEFORE the destructive `gh api DELETE` -- and the
pure core runs both arms here with no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db = _load("delete_branch")


# --- the validator: a safe subset passes, every hazard is rejected ---

SAFE = [
    "crew/hy-mlj0-branch-safety",
    "main",
    "feature/x_1.2-3",
    "release/2026.08",
]

HAZARD = [
    "",  # empty
    "   ",  # whitespace only
    " lead",  # leading space
    "trail ",  # trailing space
    "-D",  # a bare option
    "--force",  # a long option
    "a b",  # embedded space
    "a;rm -rf /",  # command chaining
    "a && rm x",
    "a|b",  # pipe
    "a$(whoami)",  # command substitution
    "a`id`",  # backtick substitution
    "a>b",  # redirection
    "../etc/passwd",  # path traversal
    "a..b",  # traversal component / git refname rule
    "/absolute",  # leading slash
    "trail/",  # trailing slash
    ".hidden",  # leading dot
    "a/.b",  # dot-led component
    "name.lock",  # git lock suffix
    "a\nb",  # newline
    "a\tb",  # tab
    "x" * 256,  # over-long
]


@pytest.mark.parametrize("name", SAFE)
def test_a_safe_branch_name_passes(name):
    assert db.is_safe_branch_name(name) is True


@pytest.mark.parametrize("name", HAZARD)
def test_every_hazardous_branch_name_is_rejected(name):
    assert db.is_safe_branch_name(name) is False


def test_none_is_rejected():
    assert db.is_safe_branch_name(None) is False


# --- deletable_branch: provenance + validation + ancestor, fail-closed ---


def _ancestor_yes(ref, default):
    return True


def _ancestor_no(ref, default):
    return False


def test_a_safe_merged_branch_from_the_forge_is_deletable():
    # The name comes from pr_head_ref (the forge), not from any message, and it is
    # merged (ancestor). Only then is it returned as the deletion target.
    target = db.deletable_branch(
        "273",
        default_branch="main",
        pr_head_ref=lambda pr: "crew/hy-273-x",
        is_ancestor=_ancestor_yes,
    )
    assert target == "crew/hy-273-x"


def test_a_safe_but_unmerged_name_is_not_deletable():
    # A valid-charset name whose tip is NOT an ancestor of main -- an unmerged
    # branch, or a wrong-but-safe name pointing at live work -- must not delete.
    # This is the durable half: the ancestor check is self-verifying.
    target = db.deletable_branch(
        "273",
        default_branch="main",
        pr_head_ref=lambda pr: "crew/hy-ving-someone-elses-live-branch",
        is_ancestor=_ancestor_no,
    )
    assert target is None


def test_an_unsafe_name_is_rejected_before_the_ancestor_check_ever_runs():
    # Ordering guarantee: a hazardous name never reaches a git command. Record
    # whether is_ancestor was called; it must NOT be, for a metachar name.
    called = []

    def spy_ancestor(ref, default):
        called.append(ref)
        return True

    target = db.deletable_branch(
        "273",
        default_branch="main",
        pr_head_ref=lambda pr: "a; rm -rf /",
        is_ancestor=spy_ancestor,
    )
    assert target is None
    assert called == []  # the destructive-path git check never saw the bad name


def test_the_default_branch_is_never_deletable_even_though_it_is_its_own_ancestor():
    # A cross-fork merged PR can have head.ref == the default branch ("main"), and
    # a commit is vacuously its own ancestor -- so the ancestor check alone would
    # pass and delete main. It must be refused BEFORE the ancestor check runs.
    called = []
    target = db.deletable_branch(
        "273",
        default_branch="main",
        pr_head_ref=lambda pr: "main",
        is_ancestor=lambda ref, d: called.append(ref) or True,
    )
    assert target is None
    assert called == []  # the ancestor (git) check is never reached for the base


@pytest.mark.parametrize("reserved", ["main", "master", "HEAD", "head", "Main"])
def test_reserved_and_shared_ref_names_are_never_deletable(reserved):
    target = db.deletable_branch(
        "273",
        default_branch="develop",  # even when the DEFAULT is something else
        pr_head_ref=lambda pr: reserved,
        is_ancestor=lambda ref, d: True,
    )
    assert target is None


def test_a_head_ref_equal_to_a_non_default_default_branch_is_refused():
    # The refusal binds to the ACTUAL default branch argument, not only to "main".
    target = db.deletable_branch(
        "273",
        default_branch="trunk",
        pr_head_ref=lambda pr: "trunk",
        is_ancestor=lambda ref, d: True,
    )
    assert target is None


def test_a_forge_with_no_head_ref_is_not_deletable():
    called = []
    target = db.deletable_branch(
        "273",
        default_branch="main",
        pr_head_ref=lambda pr: None,  # forge returned nothing
        is_ancestor=lambda ref, d: called.append(ref) or True,
    )
    assert target is None
    assert called == []


# --- the CLI wrapper: only a safe, merged target reaches the DELETE ---


def test_a_non_numeric_pr_aborts_without_touching_forge_or_git(monkeypatch):
    touched = []
    monkeypatch.setattr(db, "_gh", lambda args: touched.append(args))
    assert db.delete_branch("../evil") == 2
    assert touched == []


def test_delete_branch_aborts_and_deletes_nothing_when_no_safe_target(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "_gh", lambda args: calls.append(args) or _ok())
    monkeypatch.setattr(db, "_pr_head_ref", lambda pr: "a; rm -rf /")  # hostile forge value
    monkeypatch.setattr(db, "_is_ancestor", lambda ref, d: True)
    assert db.delete_branch("273") == 2
    assert calls == []  # gh DELETE never invoked


def test_delete_branch_deletes_only_a_safe_merged_target(monkeypatch):
    calls = []

    monkeypatch.setattr(db, "_pr_head_ref", lambda pr: "crew/hy-273-x")
    monkeypatch.setattr(db, "_is_ancestor", lambda ref, d: True)
    monkeypatch.setattr(db, "_gh", lambda args: calls.append(args) or _ok())
    assert db.delete_branch("273") == 0
    # Exactly one DELETE, naming the forge-sourced, validated ref as one argument.
    assert calls == [["api", "-X", "DELETE", "repos/{owner}/{repo}/git/refs/heads/crew/hy-273-x"]]


class _ok:
    returncode = 0
    stdout = ""
    stderr = ""
