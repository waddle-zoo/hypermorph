"""A gate is a claim about ONE tree, and that claim never expires (hy-kiwk).

The bug this closes: a gate's validity was read as a function of (base main, PR
head), so every landing moved the base and invalidated the pending gate of every
other queued PR -- quadratic regrade churn on a busy queue, each re-run producing
an identical verdict. The fix keys validity to the TREE that would land, not the
base: `would_land_tree` recomputes the merge tree from the graph each time (no
store, so no stale cache and no false-HIT skip), and `gate_describes_would_land`
asks only whether the tree that would land now IS the one the gate measured.

The mayor's ruling requires the acceptance arm to be a CONSTRUCTED POSITIVE for
each mechanism -- the 25-landing corpus held two hits sharing one mechanism, so a
replay cannot tell a correct predicate from a half-correct one. Both mechanisms
of "the head already contains the landing" are synthesised here: the landed
commit is an ANCESTOR of the head (a fast-forward), and the same CONTENT reached
by a different commit (a cherry-pick, which a bare ancestor test would miss).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gate", Path(__file__).resolve().parents[2] / "scripts" / "gate.py"
)
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["gate"] = gate
_SPEC.loader.exec_module(gate)


class Repo:
    """A throwaway git repo. `commit` returns the new HEAD sha; `tree` reads the
    tree oid of any commit -- the two the gate reasons about."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "t@t.test")
        self.git("config", "user.name", "t")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=True
        ).stdout.strip()

    def commit(self, name: str, content: str, message: str) -> str:
        (self.root / name).write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def checkout_new(self, branch: str, at: str) -> None:
        self.git("checkout", "-q", "-b", branch, at)

    def tree(self, commit: str) -> str:
        return self.git("rev-parse", f"{commit}^{{tree}}")

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", maybe_ancestor, descendant],
                cwd=str(self.root),
            ).returncode
            == 0
        )


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def _base_head(repo: Repo) -> tuple[str, str, str]:
    """A base `b0`, a PR head `h` built on it, and the tree a gate at the head
    measured (which on this fast-forward is the head's own tree)."""
    b0 = repo.commit("a", "1", "B0")
    h = repo.commit("c", "1", "the PR's own work")
    return b0, h, repo.tree(h)


# --- constructed positives: the head already contains the landing (reuse) ---


def test_a_landing_the_head_already_contains_as_an_ancestor_keeps_the_gate_valid(repo):
    """Mechanism one: the landed commit is an ANCESTOR of the head. A dependency
    PR the head was built on lands; the base advances to a commit the head
    already contains, the merge is a fast-forward, and the would-land tree is
    unchanged -- so the gate the queue already ran stays valid."""
    b0 = repo.commit("a", "1", "B0")
    dep = repo.commit("dep", "1", "a dependency the head builds on")  # on main
    head = repo.commit("c", "1", "the PR's own work on top of the dep")
    tree_id = repo.tree(head)  # gated while base was b0

    # The dependency lands: base advances from b0 to dep, which head contains.
    assert repo.is_ancestor(dep, head)
    assert gate.gate_describes_would_land(tree_id=tree_id, base=dep, head=head, root=repo.root)
    # ...and it was equally valid at the older base, because the tree is the same.
    assert gate.gate_describes_would_land(tree_id=tree_id, base=b0, head=head, root=repo.root)


def test_the_same_content_by_a_different_commit_keeps_the_gate_valid(repo):
    """Mechanism two, the one a bare ancestor test MISSES: the head carries the
    landing's CONTENT via a different commit (a cherry-pick). The landed commit is
    NOT an ancestor of the head, yet the would-land tree is identical, so the gate
    is still valid -- proving the check is tree identity, not ancestry."""
    b0 = repo.commit("shared", "v0\n", "B0 with a file both sides will patch")
    head = repo.commit("shared", "v1\n", "head patches shared v0 -> v1")
    tree_id = repo.tree(head)

    # Main lands the SAME patch (shared v0 -> v1) as a DIFFERENT commit off b0 --
    # a cherry-pick, not an ancestor of the head.
    repo.checkout_new("landed", b0)
    other = repo.commit("shared", "v1\n", "the same patch, a different commit")

    # The landed commit is NOT an ancestor of the head -- a bare ancestor test
    # would say "re-gate" here -- yet the would-land tree is identical, so tree
    # identity says "reuse". This is the arm that distinguishes the two.
    assert not repo.is_ancestor(other, head)
    assert gate.would_land_tree(other, head, root=repo.root) == tree_id
    assert gate.gate_describes_would_land(tree_id=tree_id, base=other, head=head, root=repo.root)


# --- negatives: a different tree would land, so the gate must be re-derived ---


def test_a_base_move_in_a_file_the_head_does_not_touch_needs_a_fresh_gate(repo):
    """The disjoint case the bead's caveat names: main moves in a file the PR does
    not touch, so the would-land tree genuinely DIFFERS and a fresh measurement is
    owed. The gate is not wrong -- it is a true claim about a tree that will not
    land -- so this is a re-gate, and 'stale' is the wrong word for it."""
    b0, head, tree_id = _base_head(repo)

    repo.checkout_new("landed", b0)
    disjoint = repo.commit("elsewhere", "1", "a change to a file the head never touches")

    assert gate.would_land_tree(disjoint, head, root=repo.root) != tree_id
    assert not gate.gate_describes_would_land(
        tree_id=tree_id, base=disjoint, head=head, root=repo.root
    )


def test_a_new_head_voids_the_gate(repo):
    """The head-change arm: real new work moves the head, and the gate measured a
    tree that is no longer what would land. Only a head/tree change voids a gate;
    a base-only move that leaves the tree unchanged does not."""
    b0, head, tree_id = _base_head(repo)
    new_head = repo.commit("c", "2", "new work on the PR")  # head advances

    assert repo.tree(new_head) != tree_id
    assert not gate.gate_describes_would_land(
        tree_id=tree_id, base=b0, head=new_head, root=repo.root
    )


def test_a_conflicting_would_land_merge_describes_nothing(repo):
    """A merge that conflicts has no single tree to land, so no gate can describe
    it: `would_land_tree` is None and the gate does not clear -- fail toward
    re-gate, never toward reusing a gate for a tree that cannot exist."""
    b0, head, tree_id = _base_head(repo)  # head sets c=1

    repo.checkout_new("landed", b0)
    conflicting = repo.commit("c", "999", "main changes the same file differently")

    assert gate.would_land_tree(conflicting, head, root=repo.root) is None
    assert not gate.gate_describes_would_land(
        tree_id=tree_id, base=conflicting, head=head, root=repo.root
    )


# --- the primitive and the CLI ---


def test_would_land_tree_is_the_head_tree_on_a_fast_forward(repo):
    b0, head, tree_id = _base_head(repo)
    assert gate.would_land_tree(b0, head, root=repo.root) == tree_id


def test_an_empty_tree_id_describes_nothing(repo):
    b0, head, _ = _base_head(repo)
    assert not gate.gate_describes_would_land(tree_id="", base=b0, head=head, root=repo.root)


def test_describes_cli_reuses_on_a_matching_tree_and_re_gates_otherwise(repo, monkeypatch, capsys):
    """The command a merger or refinery shells out to: exit 0 REUSE, exit 1
    RE-GATE. It accepts a bare tree id AND a whole HYPERSET-GATE line as the
    record, so a caller passes what it already has."""
    monkeypatch.setattr(gate, "ROOT", repo.root)
    b0, head, tree_id = _base_head(repo)

    # Bare tree id, would-land matches -> REUSE (0).
    assert gate.describes_cli([b0, head, tree_id]) == 0
    assert "REUSE" in capsys.readouterr().out

    # A whole gate line carrying that tree_id -> REUSE (0), field extracted.
    line = gate.evidence_line(
        sha="deadbeef",
        tree="clean",
        tree_id=tree_id,
        extras="all",
        collected=1,
        uncollected_modules=0,
        counts={"passed": 1},
        result=gate.PASS,
    )
    assert gate.describes_cli([b0, head, line]) == 0

    # A disjoint base move -> RE-GATE (1), naming the tree that would land.
    repo.checkout_new("landed", b0)
    disjoint = repo.commit("elsewhere", "1", "disjoint change")
    assert gate.describes_cli([disjoint, head, tree_id]) == 1
    assert "would land" in capsys.readouterr().out

    # A conflicting would-land -> RE-GATE (1), without over-claiming a conflict:
    # the head added file `c`, and a base commit adds `c` with different content.
    repo.checkout_new("clash", b0)
    clashing = repo.commit("c", "clashing\n", "main changes the head's own file differently")
    assert gate.would_land_tree(clashing, head, root=repo.root) is None
    assert gate.describes_cli([clashing, head, tree_id]) == 1
    assert "no single tree" in capsys.readouterr().out

    # An UNKNOWN tree id -> RE-GATE (1).
    assert gate.describes_cli([b0, head, "unknown"]) == 1
    assert "names no tree id" in capsys.readouterr().out

    # Wrong arity -> usage error (2).
    assert gate.describes_cli([b0, head]) == 2


# --- env fault is LOUD, conflict is None (hy-wpqa) ---


def test_would_land_tree_raises_on_a_non_git_directory(tmp_path):
    # The #305 root cause: running from a non-git dir made every call return None.
    # Now it raises, so the fault cannot masquerade as "no single tree, skip".
    with pytest.raises(gate.GitEnvError):
        gate.would_land_tree("HEAD", "HEAD", root=tmp_path)


def test_would_land_tree_raises_on_an_unresolvable_ref(repo):
    head = repo.commit("a", "1", "c0")
    with pytest.raises(gate.GitEnvError):
        gate.would_land_tree("f" * 40, head, root=repo.root)  # base names no object


def test_a_genuine_conflict_is_still_None_not_a_raise(repo):
    # A real merge conflict writes the merged tree oid on line 1, so it stays None
    # (a legitimate skip), distinct from the env fault above.
    b0 = repo.commit("c", "1\n", "b0")
    head = repo.commit("c", "head\n", "head changes c")
    repo.checkout_new("landed", b0)
    other = repo.commit("c", "other\n", "main changes c differently")
    assert gate.would_land_tree(other, head, root=repo.root) is None


def test_is_git_repo_is_true_for_a_repo(repo):
    assert gate.is_git_repo(repo.root) is True


def test_is_git_repo_is_false_for_a_bare_dir(tmp_path):
    # A fresh tmp dir with no Repo built on it -- not a git work tree.
    assert gate.is_git_repo(tmp_path) is False
