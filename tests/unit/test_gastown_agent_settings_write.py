"""The settings write in `scripts/gastown-agent.sh`, driven through a stubbed `gt`.

This write is the script's first user-visible step and it edits a user-scope
`settings.json` that can hold `env` and `apiKeyHelper`, so what it does to a file
it did not create is the contract -- not an internal variable. Each test below
therefore runs the real script against a sandboxed `CLAUDE_CONFIG_DIR` with `gt`
replaced by a recording stub, and asserts on the file left behind.

The scratch path is where the interesting failures live. It is fixed and
predictable (`settings.json.gastown-agent`), so a run killed part-way leaves one
behind for the next run to meet, and three rounds of #154 turned on what the next
run does with it. hy-2eez is the leftover the `os.open` could not survive at all.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/gastown-agent.sh"

WINDOW = 800000
"""What the script writes, and the only key this suite asserts it adds."""

GT_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
"""

SCRATCH_NAME = "settings.json.gastown-agent"
"""The predictable scratch name, asserted rather than recomputed.

Several probes across #154 asserted on this name; spelling it here means a change
to it fails a test instead of silently passing one that no longer plants a
leftover where the script will look.
"""

WINDOW_SWAP_SITECUSTOMIZE = '''\
"""Plant a path-component swap in the child, exactly in the close->rename window.

Reaching a live TOCTOU window against a one-shot subprocess needs the swap to fire
BETWEEN the descriptor's close and the rename, and nothing observable from outside
the process hits that instant. `sitecustomize` loads at interpreter start (the child
runs `site`), so wrapping `os.replace` here fires the swap on the child's own call --
the settings rename is the first `os.replace` whose source is the scratch leaf. The
wrapper repoints the config-directory symlink to an attacker directory and THEN calls
the real rename: a by-path `os.replace(scratch, target)` would now resolve the swapped
link and miss the scratch (or land in the attacker dir); the dir-fd rename resolves
against the descriptor opened before the swap and lands in the pinned real directory.
"""
import os

_real_replace = os.replace
_fired = {"done": False}


def _replace(src, dst, *args, **kwargs):
    if not _fired["done"] and str(src).endswith(".gastown-agent"):
        _fired["done"] = True
        link = os.environ.get("HYPERSET_SWAP_LINK")
        to = os.environ.get("HYPERSET_SWAP_TO")
        if link and to:
            try:
                os.unlink(link)
            except FileNotFoundError:
                pass
            os.symlink(to, link)
    return _real_replace(src, dst, *args, **kwargs)


os.replace = _replace
'''
"""A test-only child harness; never installed into the shipped tree."""

OWNERSHIP_REFUSAL_SITECUSTOMIZE = '''\
"""Force the ownership-preservation branch to fire and fail, in the child.

The branch only runs when the target is owned by someone other than the runner, and
a test cannot chown a file to another uid without privilege. So fake the comparison:
make os.geteuid/os.getegid return a value that cannot match the file's real owner, so
the branch fires, and wrap os.fchown to raise -- the state a real run meets when it may
not preserve a foreign owner. The script must then REFUSE, not silently reassign the
file to the runner.
"""
import os

os.geteuid = lambda: -1
os.getegid = lambda: -1


def _fchown(fd, uid, gid):
    raise PermissionError(1, "Operation not permitted")


os.fchown = _fchown
'''
"""A test-only child harness; never installed into the shipped tree."""

ATOMIC_OBSERVER_SITECUSTOMIZE = '''\
"""Observe the settings.json swap at the atomic barrier, in the child.

A concurrent reader cannot be raced against a one-shot subprocess deterministically,
so instead wrap `os.replace` -- the atomic rename the write ends with -- and read both
sides AT the barrier: the scratch source (the COMPLETE new content, fully staged) and
the target destination (the COMPLETE old content, untouched until now). Then let the
real rename run and read the target again (the COMPLETE new content, now in place).
Because the swap is a rename, there is no instant between those reads where the target
is empty or half-written. The three snapshots go to a file the test reads.

A non-atomic write -- truncate the target in place and write into it -- never calls
`os.replace` with the scratch as source, so this observer never fires and the file
stays absent, which is how the test tells the two apart.
"""
import json
import os

_real_replace = os.replace


def _read(name, dir_fd):
    if dir_fd is not None:
        fd = os.open(name, os.O_RDONLY, dir_fd=dir_fd)
    else:
        fd = os.open(name, os.O_RDONLY)
    try:
        return os.read(fd, 1 << 20).decode()
    finally:
        os.close(fd)


def _replace(src, dst, *args, **kwargs):
    obs = os.environ.get("HYPERSET_ATOMIC_OBS")
    if obs and str(src).endswith(".gastown-agent"):
        src_dir_fd = kwargs.get("src_dir_fd")
        dst_dir_fd = kwargs.get("dst_dir_fd")
        pre_src = _read(src, src_dir_fd)
        try:
            pre_dst = _read(dst, dst_dir_fd)
        except FileNotFoundError:
            pre_dst = None
        result = _real_replace(src, dst, *args, **kwargs)
        post_dst = _read(dst, dst_dir_fd)
        with open(obs, "w") as handle:
            json.dump({"pre_src": pre_src, "pre_dst": pre_dst, "post_dst": post_dst}, handle)
        return result
    return _real_replace(src, dst, *args, **kwargs)


os.replace = _replace
'''
"""A test-only child harness; never installed into the shipped tree."""


def _sandbox(
    tmp_path: Path,
    *,
    settings: dict | None = None,
    raw: str | None = None,
    mode: int = 0o600,
) -> dict:
    """A town, a stubbed `gt`, and a settings file this script did not create.

    `raw` writes the file's bytes directly, for the shapes `json.dumps` cannot
    produce from a dict -- a top-level array, string, number, `null` or `true`,
    each of which parses and none of which is an object (hy-x83h).
    """
    town = tmp_path / "gt"
    (town / "mayor").mkdir(parents=True)
    (town / "mayor" / "town.json").write_text("{}\n")
    (town / "hyperset").mkdir()

    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    gt = stub_dir / "gt"
    gt.write_text(GT_STUB)
    gt.chmod(0o755)

    config = tmp_path / ".claude"
    config.mkdir()
    target = config / "settings.json"
    target.write_text(raw if raw is not None else json.dumps(settings, indent=2) + "\n")
    target.chmod(mode)

    return {
        "target": target,
        "scratch": config / SCRATCH_NAME,
        "log": tmp_path / "gt.log",
        "directives": town / "hyperset" / "directives",
        "env": {
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "GASTOWN_ROOT": str(town),
            "GASTOWN_RIG": "hyperset",
            "CLAUDE_CONFIG_DIR": str(config),
            "STUB_LOG": str(tmp_path / "gt.log"),
        },
    }


def _run(sandbox: dict, *, umask: int = 0o022) -> subprocess.CompletedProcess:
    """The real script, under an explicit umask.

    The umask matters: replacing the target renames a new inode over it, and that
    inode carries the umask rather than the original mode unless the script
    preserves it (hy-qdd2). A test that let the runner's umask decide would pass
    or fail by accident.

    The timeout turns a hang into a failure. An `O_WRONLY` open on a FIFO blocks
    for a reader that never comes, so without it a regression that stopped refusing
    non-regular scratch shapes would hang the suite rather than redden it (hy-lsrx).
    """
    return subprocess.run(
        ["bash", "-c", f"umask {umask:04o}; exec {SCRIPT} claude"],
        env=sandbox["env"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _body(target: Path) -> dict:
    return json.loads(target.read_text())


def _switched(sandbox: dict) -> bool:
    """Whether the run reached `gt config default-agent`.

    The town half of the ordering contract, read from what the stub recorded rather
    than inferred from the settings file. Asserting the settings file was unwritten
    and calling that "the town is unswitched" described the wrong file with the
    instrument already in the fixture (hy-yrrv).
    """
    log = sandbox["log"]
    return log.exists() and "default-agent" in log.read_text()


def test_a_leftover_scratch_at_mode_000_does_not_wedge_the_script(tmp_path):
    """The permanent wedge, and the reason it needed a fix ahead of the open (hy-2eez).

    `os.open` on an existing mode-000 file raises `EACCES`, and that raise happened
    BEFORE the `try` whose `except BaseException` unlinks the scratch -- so the 000
    file survived its own failure and every later run failed identically. Measured
    at f9c19a5: `rc=1`, `PermissionError: [Errno 13] Permission denied`, target not
    updated, scratch left behind, 60 runs out of 60. No attacker is needed to plant
    it; a SIGKILL of an older version of this script is enough.

    The assertion that carries the finding is the pair: the run SUCCEEDS and the
    scratch is GONE. A fix that merely reported the leftover better would satisfy
    the first half of a weaker test and leave the wedge in place.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=0o600)
    sandbox["scratch"].write_text("left over from a killed run\n")
    sandbox["scratch"].chmod(0o000)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW
    assert _body(sandbox["target"])["keep"] == "me", "the other keys survive the replacement"
    assert not sandbox["scratch"].exists(), (
        "the leftover must be gone, or the next run meets it again and the wedge is permanent"
    )


def test_a_run_that_succeeds_leaves_no_scratch_to_wedge_the_next_one(tmp_path):
    """The world where the test above comes out the other way (hq-xneo).

    If a passing run left a 000 scratch behind, the assertion above would be
    describing a first run that happens to work rather than a script that cannot
    wedge. So: two runs back to back with no leftover planted, and nothing at the
    scratch path after either. The second is a no-op -- the key is already the
    script's own value -- which is itself the path that must not write a scratch.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=0o600)

    first = _run(sandbox)
    assert first.returncode == 0, first.stderr
    assert not sandbox["scratch"].exists()

    second = _run(sandbox)
    assert second.returncode == 0, second.stderr
    assert not sandbox["scratch"].exists()
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW


def test_a_symlink_planted_at_the_scratch_path_is_still_refused_with_its_victim_intact(tmp_path):
    """The half of hy-2eez's fix that was deliberately NOT changed.

    Removing a stale scratch fixes the wedge, and removing *whatever* is at that
    path would fix it too -- while deleting a planted symlink instead of refusing
    it. That is a different contract than the one measured and cleared under
    hy-fhz0, so the removal is limited to regular files and the refusal decides the
    symlink case. This test is what makes that limit visible: a later change to a
    bare `unlink()` turns it red rather than passing quietly.

    The refusal now carries a sentence rather than `OSError: [Errno 62]`, and it
    tells the operator to find out what created the link rather than to delete it,
    because deleting is the one action that would destroy evidence here (hy-lsrx).
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=0o600)
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n")
    sandbox["scratch"].symlink_to(victim)

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is a symlink" in result.stderr, result.stderr
    assert "rather than deleting it" in result.stderr, "the message names the right action"
    assert "Traceback" not in result.stderr, "a deliberate refusal does not speak in tracebacks"
    assert victim.read_text() == "do not touch\n", "the link's victim is never written through"
    assert sandbox["scratch"].is_symlink(), "the planted link is refused, not deleted"
    assert "autoCompactWindow" not in _body(sandbox["target"])
    assert not _switched(sandbox), (
        "the write is the first step, so a refusal here leaves the town unswitched"
    )


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o640])
@pytest.mark.parametrize("umask", [0o022, 0o077])
@pytest.mark.parametrize("leftover", [None, 0o000, 0o644])
def test_the_targets_mode_survives_every_leftover_and_umask(tmp_path, mode, umask, leftover):
    """The mode matrix, re-derived because rewriting the open has invalidated it twice.

    `os.replace` renames a new inode over the target, so the mode is the new
    inode's unless the script copies the old one -- 0600 widens to 0644 at umask
    022, and 0644 narrows to 0600 at umask 077 (hy-qdd2). Removing the stale
    scratch changes which inode gets written, so the preservation is measured again
    here rather than assumed to have survived the change.

    The leftovers parametrized are the ones the script REMOVES: absent, and a
    regular file at 0000 or 0644. The shapes it refuses instead have their own
    tests, because there is no mode to preserve on a run that never writes.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=mode)
    if leftover is not None:
        sandbox["scratch"].write_text("stale\n")
        sandbox["scratch"].chmod(leftover)

    result = _run(sandbox, umask=umask)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(sandbox["target"].stat().st_mode) == mode, (
        "the umask must never be substituted for the file's own mode"
    )
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW
    assert not sandbox["scratch"].exists()


def test_a_directory_at_the_scratch_path_is_refused_by_name(tmp_path):
    """The third shape, which had no sentence and no test (hy-lsrx).

    A directory there wedged exactly like the symlink -- permanently, since the
    script never removes it -- and said `IsADirectoryError: [Errno 21]`. Refusing
    is still right, for hy-2eez's reason: the script never creates a directory at
    that path, so removing one would destroy what it did not create.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["scratch"].mkdir()

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is a directory" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert sandbox["scratch"].is_dir(), "refused, not removed"
    assert not _switched(sandbox)


def test_a_fifo_at_the_scratch_path_is_refused_rather_than_waited_on(tmp_path):
    """The shape that did not fail at all: it HUNG (hy-lsrx).

    Every other non-regular shape made `os.open` raise. A FIFO makes an `O_WRONLY`
    open BLOCK until a reader arrives, and under launchd no reader ever does -- so
    falling through to the open was not merely untidy for this one, it was an
    unattended script waiting forever with nothing on stderr. This is why the
    refusal covers "not a regular file" rather than enumerating symlink and
    directory.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    os.mkfifo(sandbox["scratch"])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is not a regular file" in result.stderr, result.stderr
    assert not _switched(sandbox)


def test_a_fifo_at_the_settings_path_is_refused_rather_than_blocking_the_read(tmp_path):
    """The READ side of the same hang, one step earlier than every scratch guard (hy-luw9).

    The tests above cover a FIFO planted where the script WRITES. The script's first
    contact with the file is the READ, and it had no shape guard: `read_text()`
    opened O_RDONLY with no O_NONBLOCK, so a FIFO at settings.json BLOCKED for a
    writer that never comes -- before the script printed anything, hanging every run.
    That is a separate open from the scratch one, so a separate guard and a separate
    test.

    The fstat is the load-bearing half, not the flag. O_RDONLY | O_NONBLOCK SUCCEEDS
    on a FIFO and returns EOF, so a flag-only fix would read raw="" and write a fresh
    settings file straight over the planted FIFO -- a silent overwrite, not a
    refusal. So this asserts the FIFO is left INTACT and the town unswitched, both of
    which a flag-only fix fails: only the S_ISREG check refuses.

    The 60s timeout in `_run` is what turns the original hang into a failure rather
    than a suite that never returns.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["target"].unlink()
    os.mkfifo(sandbox["target"])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is a FIFO" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, (
        "a deliberate refusal, not an O_RDONLY hang turned into a crash"
    )
    assert stat.S_ISFIFO(os.lstat(sandbox["target"]).st_mode), (
        "the FIFO is refused with its shape intact, never read-as-empty and overwritten"
    )
    assert not _switched(sandbox), (
        "the read is the first step, so a refusal here leaves the town unswitched"
    )


def test_a_directory_at_the_settings_path_is_refused_cleanly_not_crashed_on(tmp_path):
    """The shape whose refusal was DEAD CODE, and that crashed instead (hy-luw9 round 2).

    The fstat has to come BEFORE the text handle. `os.fdopen`/read on a directory
    raises `IsADirectoryError`, so an `S_ISDIR` branch placed AFTER the wrap never
    runs and the directory reaches the operator as a traceback rather than a
    sentence. Refusing on the raw descriptor's fstat is what makes the branch live:
    a directory at settings.json meets a named refusal with its shape intact and the
    town unswitched, no crash.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["target"].unlink()
    sandbox["target"].mkdir()

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is a directory" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, (
        "refused cleanly on the raw fd, not crashed on fdopen/read"
    )
    assert sandbox["target"].is_dir(), "the directory is refused, not consumed"
    assert not _switched(sandbox)


def test_a_legitimately_symlinked_settings_file_is_still_read_through(tmp_path):
    """Dotfiles compat on the READ: the open FOLLOWS the link (hy-luw9 round 2).

    The write side follows a symlinked settings.json -- a link into a tracked repo --
    and writes THROUGH it, so the read must be consistent and follow it too, or a
    legitimate dotfiles setup breaks at the very first step. The guard follows and
    enforces `S_ISREG` on the FINAL target, which reads a symlink->regular normally
    while still refusing a symlink->FIFO (its own arm below).

    The `from` key is the load-bearing half: it proves the REAL file's content was
    read, not that the target was treated as absent and a fresh empty settings
    written over the link.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir(parents=True)
    real.write_text('{"keep": "me", "from": "the real file"}\n')
    link = sandbox["target"]
    link.unlink()
    link.symlink_to(real)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert link.is_symlink(), "the link is followed, never replaced"
    assert _body(real)["autoCompactWindow"] == WINDOW
    assert _body(real)["from"] == "the real file", (
        "the real file's content was read, not a fresh empty settings written over the link"
    )


def test_a_symlink_to_a_fifo_at_the_settings_path_is_refused_by_the_followed_target(tmp_path):
    """Following the link must not reopen the hang (hy-luw9 round 2).

    Removing O_NOFOLLOW keeps dotfiles compat, and the FIFO block stays closed by the
    SHAPE check rather than by refusing to follow: a symlink pointing at a FIFO
    resolves to the FIFO, and `S_ISREG` on the followed target refuses it with the
    same sentence a bare FIFO gets.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    fifo = tmp_path / "planted.fifo"
    os.mkfifo(fifo)
    link = sandbox["target"]
    link.unlink()
    link.symlink_to(fifo)

    result = _run(sandbox)

    assert result.returncode == 1
    assert "it is a FIFO" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert not _switched(sandbox)


def _scratch_open_flags() -> list[str]:
    """The flag names the script's scratch `os.open` actually passes.

    Read out of the script rather than retyped, because a retyped flag set is a
    measurement of this test's own literal: it would keep reporting ENXIO after
    somebody removed the flag from the script. The names are extracted and
    rebuilt in the child from `os`, so the numbers are this platform's.
    """
    # The scratch open is now relative to the pinned directory fd (hy-5pjk): the first
    # argument is the leaf `scratch.name` and a `dir_fd=dir_fd` follows the mode. The FLAGS --
    # what this helper reads -- are unchanged; only the resolution of the name moved onto the fd.
    call = re.search(
        r"fd = os\.open\(\s*scratch\.name,\s*(.+?),\s*0o600,\s*dir_fd=dir_fd,?\s*\)",
        SCRIPT.read_text(),
        re.S,
    )
    assert call, "the scratch open is no longer one os.open call this test can read"
    names = re.findall(r"os\.(O_[A-Z_]+)", call.group(1))
    assert names, f"no open flags found in {call.group(1)!r}"
    return names


def _open_with_the_scripts_flags(path: Path, *, timeout: float = 5.0) -> str:
    """What the script's own flag set does to `path`: an errno, or `opened`.

    In a child process with a timeout, because the failure being measured is a
    BLOCK, and a block inside the test process is a hung suite rather than a red
    one. `TimeoutExpired` is turned into the finding's own sentence here so the
    caller does not have to know that a hang is the "before" state.
    """
    flags = " | ".join(f"os.{name}" for name in _scratch_open_flags())
    program = (
        "import os, sys\n"
        f"flags = {flags}\n"
        "try:\n"
        "    os.open(sys.argv[1], flags, 0o600)\n"
        "except OSError as error:\n"
        "    print(error.errno)\n"
        "    sys.exit(0)\n"
        "print('opened')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", program, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"blocked for {timeout}s"
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_scratch_open_refuses_a_fifo_rather_than_blocking_on_it(tmp_path):
    """The window the lstat above cannot cover (hy-cznz).

    The refusal that names a FIFO runs BEFORE the open, so it decides a FIFO that
    is already there and nothing about one planted in the lstat-to-open window.
    For the symlink shape that gap is covered anyway -- `O_NOFOLLOW` makes the
    open raise `ELOOP` on its own -- but for the FIFO shape, which is the one
    that motivated the branch, the open did not refuse at all: it BLOCKED for a
    reader that never comes, silently, forever, under launchd.

    `O_NONBLOCK` closes it in one flag. Measured through the script's own flag
    set: without it the open was still blocked when this test's child was killed;
    with it a FIFO fails immediately with `ENXIO`.

    Not a test of a race, which could not be made deterministic: it exercises the
    open's behaviour on the shape directly, which is the property the window
    needs. The lstat refusal stays and keeps its own test above -- it is what
    produces the operator-facing sentence, and a bare `ENXIO` would not.
    """
    fifo = tmp_path / "planted.fifo"
    os.mkfifo(fifo)

    assert _open_with_the_scripts_flags(fifo) == str(errno.ENXIO)


def test_the_same_flags_still_open_an_ordinary_scratch_file(tmp_path):
    """The control that keeps the flag from being bought with the write.

    `O_NONBLOCK` is a no-op for regular-file writes, and this measures that
    rather than asserting it: the same flag set, the shape the script actually
    creates, opened rather than refused. Without this line the assertion above
    is satisfied by a flag set that refuses everything.
    """
    ordinary = tmp_path / "settings.json.gastown-agent"

    assert _open_with_the_scripts_flags(ordinary) == "opened"


def test_an_unremovable_stale_scratch_names_the_directory_as_well_as_the_file(tmp_path):
    """The remedy that could not be followed (hy-lsrx).

    The stale REGULAR file is the one shape the script removes, and when the unlink
    fails the message told the operator to "delete it and re-run". The cause can be
    the containing directory -- at mode 0500 the unlink fails and the operator's own
    `rm` fails for the same reason -- so the message now names that too.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["scratch"].write_text("left over\n")
    config = sandbox["target"].parent
    config.chmod(0o500)
    try:
        result = _run(sandbox)
    finally:
        config.chmod(0o700)

    assert result.returncode == 1
    assert "cannot remove the stale scratch file" in result.stderr, result.stderr
    assert str(config) in result.stderr, "the message names the directory, not only the file"
    assert "is not writable" in result.stderr
    assert not _switched(sandbox)


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("[1, 2]\n", "array"),
        ('"hi"\n', "string"),
        ("42\n", "number"),
        ("null\n", "null"),
        ("true\n", "boolean"),
    ],
)
def test_a_settings_file_that_parses_but_is_not_an_object_is_refused_by_name(tmp_path, raw, kind):
    """Parsed, but not an object -- five shapes, one AttributeError (hy-x83h).

    All five are valid JSON, so the `json.JSONDecodeError` refusal one line above
    never saw them; `settings.get(...)` then died on `AttributeError: 'list' object
    has no attribute 'get'`, which tells the operator nothing about which of the two
    things went wrong. The refusal must NOT be folded into the parse clause: these
    parse, so widening that sentence would make it false, and a bare
    `except AttributeError` is how a real refusal gets swallowed later.

    Each shape asserts its OWN name in the message rather than that a refusal
    happened, because the subject of this sentence is machine-readable: `"hi"` and
    `[1, 2]` are different diagnoses and a message that said neither would satisfy
    a weaker test.
    """
    sandbox = _sandbox(tmp_path, raw=raw)

    result = _run(sandbox)

    assert result.returncode == 1
    assert f"parsed as a JSON {kind}, not an object" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, "a named refusal, not an AttributeError"
    assert sandbox["target"].read_text() == raw, "the file it cannot use is left byte-untouched"
    assert not _switched(sandbox)


def test_a_non_directory_at_the_directives_leaf_refuses_before_the_agent_switch(tmp_path):
    """The half-applied state reached from below (hy-0zy2).

    `mkdir -p "$root/$rig/directives"` ran AFTER `gt config default-agent`, so a
    failure there left the default agent switched with the old directive still
    installed -- the state the directive preflight exists to prevent, one level
    lower down. The two checks above it cover a bad `$GASTOWN_ROOT` or `$GASTOWN_RIG`
    and never covered the `directives` LEAF, which is why the original finding
    overstated its own reachability until it was probed through the script.

    The assertion that carries it is `not _switched`: refusing is not the point,
    refusing BEFORE the switch is.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["directives"].write_text("not a directory\n")

    result = _run(sandbox)

    assert result.returncode != 0
    assert not _switched(sandbox), "the mkdir must fail before the default agent moves"
    assert "autoCompactWindow" not in _body(sandbox["target"]), (
        "and before the settings write, so the whole run is a no-op"
    )


def test_a_symlinked_target_is_written_through_with_the_real_files_mode_and_indent(tmp_path):
    """The property most likely to regress silently, and it was hand-measured (hy-qbvs).

    A dotfiles-managed `settings.json` is a symlink into a tracked repository.
    Renaming over the link would delete it and strand the real file stale, so the
    script resolves the link and writes THROUGH. Everything about that depends on
    `target = realpath(path) if path.is_symlink() else path` staying above every use
    of `target`, and nothing tested it: the suite's other symlink test plants at the
    SCRATCH path, not at the target.

    The indent assertion is load-bearing rather than cosmetic. It is the cheapest
    proof that the mode and content came from the REAL file: a run that read the
    link's own metadata, or that replaced the link with a fresh file, could not
    reproduce the real file's four spaces.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    real = tmp_path / "dotfiles" / "claude" / "settings.json"
    real.parent.mkdir(parents=True)
    real.write_text('{\n    "keep": "me"\n}\n')
    real.chmod(0o640)
    link = sandbox["target"]
    link.unlink()
    link.symlink_to(real)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert link.is_symlink(), "the link is followed, never replaced"
    assert stat.S_IMODE(real.stat().st_mode) == 0o640, "the mode preserved is the real file's"
    assert _body(real)["autoCompactWindow"] == WINDOW
    assert _body(real)["keep"] == "me"
    assert real.read_text().splitlines()[1].startswith('    "'), (
        "the real file's indent, not two spaces"
    )
    assert not (real.parent / SCRATCH_NAME).exists(), "the scratch lands beside the real file"
    assert not sandbox["scratch"].exists()


def test_a_dangling_symlinked_target_is_created_through_the_link_at_0600(tmp_path):
    """The link that points nowhere yet, which is a first-run dotfiles checkout.

    `realpath` resolves a broken link to the path it names, so the script creates
    the real file and its parents and leaves the link working. There is no mode to
    copy on a file the script creates, and 0600 is the right default for one holding
    `env` and `apiKeyHelper` (hy-qdd2).
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    real = tmp_path / "dotfiles" / "claude" / "settings.json"
    link = sandbox["target"]
    link.unlink()
    link.symlink_to(real)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert real.exists(), "the missing parents are created and the link is left working"
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert _body(real)["autoCompactWindow"] == WINDOW


def test_an_absent_settings_file_is_created_at_0600(tmp_path):
    """Property 2 pinned DIRECTLY, not only through a dangling symlink (hy-qbvs).

    A first run on a host with no settings.json must CREATE one, and 0600 is the
    right default for a file that can hold `env` and `apiKeyHelper`. The suite proved
    this only through a dangling SYMLINK, where `realpath` resolves the link to a
    missing path; this pins the plain absent case that stands behind it. Run under
    umask 022, which is the umask that DISCRIMINATES: a create-at-umask would leave
    0644 here, so a forced 0600 is what this asserts, not the shell's default.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    sandbox["target"].unlink()  # no file and no symlink: the true first-run state

    result = _run(sandbox, umask=0o022)

    assert result.returncode == 0, result.stderr
    assert sandbox["target"].exists() and not sandbox["target"].is_symlink()
    assert stat.S_IMODE(sandbox["target"].stat().st_mode) == 0o600, (
        "a file this script creates holds env/apiKeyHelper, so 0600 rather than the umask"
    )
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW


def test_a_detectable_indent_unit_is_reused_on_a_plain_file(tmp_path):
    """Property 7, the reuse half, on a NON-symlinked target (hy-qbvs).

    The symlinked-target test asserts indent reuse on a `realpath`-resolved real file;
    this pins the plain path (`target = path`) the resolution stands in for. The
    file's own four-space unit is read off its first indented line and reused, so the
    edit reads as a diff of one key rather than a reformat.
    """
    sandbox = _sandbox(tmp_path, raw='{\n    "keep": "me"\n}\n')

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    line = sandbox["target"].read_text().splitlines()[1]
    assert len(line) - len(line.lstrip(" ")) == 4, (
        f"the file's own four-space unit, reused: {line!r}"
    )
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW
    assert _body(sandbox["target"])["keep"] == "me"


def test_a_minified_file_falls_back_to_two_space_indent(tmp_path):
    """Property 7, the fallback half, and the honest limit of it (hy-qbvs).

    A minified file has no indented line to read a unit off, so the script falls back
    to two spaces -- which ADDS indentation rather than preserving it. AGENTS.md says
    "where one can be detected" for exactly this case; pinning the fallback keeps the
    documented limit from drifting into either a crash or a preserved-minification.
    """
    sandbox = _sandbox(tmp_path, raw='{"keep": "me"}\n')

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    indented = [line for line in sandbox["target"].read_text().splitlines() if line[:1] == " "]
    assert indented, "the output is pretty-printed, so there is an indented line to measure"
    assert len(indented[0]) - len(indented[0].lstrip(" ")) == 2, (
        "no detectable unit, so the documented two-space fallback"
    )
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW


def test_an_unparseable_settings_file_is_refused_with_the_bytes_untouched(tmp_path):
    """Property 8: a file `json` cannot parse is refused, not overwritten (hy-qbvs).

    Claude accepts `//` comments in this file and `json` does not, so a real
    settings.json can fail `json.loads`. The refusal must NAME the parse failure and
    leave the bytes exactly as found -- overwriting settings it cannot read back is
    the loss this guards. The non-object test above covers valid-JSON-but-not-an-
    object; this covers the `JSONDecodeError` branch one clause up, a different refusal.
    """
    raw = '{\n  // a comment claude allows and json rejects\n  "keep": "me"\n}\n'
    sandbox = _sandbox(tmp_path, raw=raw)

    result = _run(sandbox)

    assert result.returncode == 1
    assert "cannot parse" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, "a named refusal, not a JSONDecodeError traceback"
    assert sandbox["target"].read_text() == raw, "the file it cannot parse is left byte-untouched"
    assert not _switched(sandbox), "a refusal at the read leaves the town unswitched"


def test_ownership_that_cannot_be_preserved_is_refused_not_reassigned(tmp_path):
    """Property 6: refuse rather than silently reassign a foreign-owned file (hy-qbvs).

    Replacing the target renames a NEW inode over it, which would hand a settings file
    owned by someone else to whoever ran the script. The script preserves the owner by
    `fchown` on the descriptor and, when it MAY NOT, refuses rather than reassigning.
    That branch only fires for a target owned by another uid, which a test cannot
    create without privilege, so a child `sitecustomize` fakes it: `os.geteuid`/`getegid`
    return a value that cannot match the file's real owner (so the branch fires) and
    `os.fchown` raises (the state a real run meets when it may not preserve the owner).
    The refusal must land BEFORE the rename, so the target is left byte-untouched and
    the town unswitched.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"})
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "sitecustomize.py").write_text(OWNERSHIP_REFUSAL_SITECUSTOMIZE)
    before = sandbox["target"].read_text()

    env = dict(sandbox["env"])
    env["PYTHONPATH"] = str(harness)
    result = subprocess.run(
        ["bash", "-c", f"umask 0022; exec {SCRIPT} claude"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "cannot preserve ownership" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, "a named refusal, not an fchown traceback"
    assert sandbox["target"].read_text() == before, (
        "the target is left untouched, never reassigned to the runner"
    )
    assert not sandbox["scratch"].exists(), "and the scratch it was writing is cleaned up"
    assert not _switched(sandbox)


def test_a_stale_autocompactwindow_is_rewritten_to_the_correct_value(tmp_path):
    """The UPDATE path: a wrong existing value is corrected, not left (hy-qbvs adversary).

    The suite's other content arms start from a file with NO `autoCompactWindow`, so
    they exercise the ADD. A settings.json that already carries a stale value -- an
    older window, or one an operator set by hand -- must be rewritten to the value this
    script owns. The early-exit `if settings.get("autoCompactWindow") == window` only
    fires on an EXACT match, so a non-matching value has to fall through to the write;
    an edit that dropped the assignment would leave the stale number in place.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me", "autoCompactWindow": 111})

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert _body(sandbox["target"])["autoCompactWindow"] == WINDOW, (
        "a stale, non-matching value is corrected, not preserved"
    )
    assert _body(sandbox["target"])["keep"] == "me"


def test_user_keys_are_preserved_and_no_credential_leaks_out_of_the_process(tmp_path):
    """Real user content survives byte-for-byte and no secret escapes (hy-qbvs adversary).

    This file can hold `env` and `apiKeyHelper`, which is exactly why the write is
    careful -- so the fixture carries them, not a toy `{'keep': 'me'}`. Every user key
    must round-trip unchanged, and no credential-shaped value may appear anywhere the
    process emits: not stdout, not stderr, not the `gt` stub log, and not a leftover
    scratch artifact. The settings file itself legitimately carries the secret (it is
    the user's own file); everywhere else is a leak.
    """
    secret_key = "sk-ant-DEADBEEFDO_NOT_LOG_ME_0001"
    secret_helper = "op read op://vault/anthropic/key# TOKEN-HELPER-SECRET-0002"
    original = {
        "env": {"ANTHROPIC_API_KEY": secret_key, "HTTPS_PROXY": "http://proxy.internal:8080"},
        "apiKeyHelper": secret_helper,
        "permissions": {"allow": ["Bash(git status)"], "deny": []},
        "keep": "me",
    }
    sandbox = _sandbox(tmp_path, settings=original)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    body = _body(sandbox["target"])
    assert {key: value for key, value in body.items() if key != "autoCompactWindow"} == original, (
        "every user key -- env, apiKeyHelper, permissions -- round-trips unchanged"
    )
    assert body["autoCompactWindow"] == WINDOW

    # The secret lives in the user's settings.json and NOWHERE the process emitted.
    for secret in (secret_key, secret_helper):
        assert secret in sandbox["target"].read_text(), "preserved in the user's own file"
        assert secret not in result.stdout, "a credential must never reach stdout"
        assert secret not in result.stderr, "a credential must never reach stderr"
        log = sandbox["log"]
        assert not (log.exists() and secret in log.read_text()), "and never the gt stub log"
    # No scratch artifact survives to carry the secret out of band.
    assert not sandbox["scratch"].exists()
    leftovers = [
        path
        for path in sandbox["target"].parent.iterdir()
        if path.name != "settings.json" and secret_key in path.read_text(errors="ignore")
    ]
    assert leftovers == [], f"a credential leaked into {leftovers}"


def test_the_write_is_atomic_a_reader_sees_only_complete_old_or_complete_new(tmp_path):
    """Atomicity: the swap is a rename, never a truncate-in-place (hy-qbvs adversary).

    Replacing settings.json by renaming a fully-written scratch over it means a reader
    at any instant sees the COMPLETE old file or the COMPLETE new one -- never an empty
    or half-written file, which a `open(target, 'w')` truncate-then-write would expose.
    A child `sitecustomize` observes the atomic barrier: at `os.replace` it reads the
    scratch (complete NEW, fully staged) and the target (complete OLD, untouched), then
    reads the target again after the rename (complete NEW, in place). Because the swap
    is a rename there is no partial state between them.

    The fixture starts from a stale value so a write actually happens. A non-atomic
    truncate-in-place never reaches this `os.replace`, so the observation file stays
    absent -- which is what reddens this test on that mutation.
    """
    original = {"keep": "me", "autoCompactWindow": 111}
    sandbox = _sandbox(tmp_path, settings=original)
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "sitecustomize.py").write_text(ATOMIC_OBSERVER_SITECUSTOMIZE)
    obs = tmp_path / "obs.json"

    env = dict(sandbox["env"])
    env["PYTHONPATH"] = str(harness)
    env["HYPERSET_ATOMIC_OBS"] = str(obs)
    result = subprocess.run(
        ["bash", "-c", f"umask 0022; exec {SCRIPT} claude"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert obs.exists(), (
        "the write must go through an atomic os.replace of the scratch; a truncate-in-place "
        "never reaches it, so no barrier is observed"
    )
    seen = json.loads(obs.read_text())
    # Complete OLD in the target right up to the swap -- never empty or partial.
    pre_dst = json.loads(seen["pre_dst"])
    assert pre_dst["autoCompactWindow"] == 111 and pre_dst["keep"] == "me", (
        "the target is the complete OLD file until the atomic swap"
    )
    # Complete NEW already staged in the scratch before the swap.
    pre_src = json.loads(seen["pre_src"])
    assert pre_src["autoCompactWindow"] == WINDOW and pre_src["keep"] == "me", (
        "the new content is fully written to the scratch before the rename"
    )
    # Complete NEW in the target immediately after.
    post_dst = json.loads(seen["post_dst"])
    assert post_dst["autoCompactWindow"] == WINDOW and post_dst["keep"] == "me", (
        "the target is the complete NEW file immediately after the atomic swap"
    )


def test_the_scratch_mode_and_owner_are_set_on_the_descriptor_not_the_path(tmp_path):
    """A source-level guard, because this fix has no behavioural difference (hy-anhb).

    `os.chmod` and `os.chown` follow symlinks, so applying the mode by PATH undid
    the `O_NOFOLLOW` above it: a symlink swapped in after the open would take the
    settings file's mode, and the chown would aim at whatever it pointed to.
    Measured under #154: chmod through a symlink moves the victim's mode, and
    `os.replace` of a planted symlink makes the target a symlink to attacker
    content.

    Reaching that window needs a real race, so no test in this file can distinguish
    the descriptor version from the path version by observing a file -- both leave
    the same mode behind on every run that is not attacked. Asserting on the source
    is the honest way to hold it: the property IS structural, so a structural
    assertion is not a substitute for a behavioural one, it is the shape of the
    claim. What it buys is that a later edit back to `os.chmod(scratch, ...)`
    reddens instead of passing.
    """
    source = SCRIPT.read_text()

    assert "os.fchmod(handle.fileno(), mode)" in source
    assert "os.fchown(handle.fileno()" in source
    assert "os.chmod(scratch" not in source, "the mode must not be applied by path"
    assert "os.chown(scratch" not in source, "the owner must not be applied by path"


def test_the_rename_is_directory_fd_relative_not_by_path():
    """A source-level guard on the SHAPE of the rename (hy-5pjk).

    A by-path `os.replace(scratch, target)` re-resolves every path component on the call, so a
    symlink swapped into a component between the descriptor's close and the rename could redirect
    where settings.json (which holds `env` and `apiKeyHelper`) lands. A verify-then-rename would
    only NARROW that window; the fix pins the containing directory with a descriptor and renames
    RELATIVE to it (renameat semantics), resolving the directory ONCE and closing the
    path-component redirection outright. Reaching a live swap needs a real race, so as with the
    mode/owner guard above no behavioural test distinguishes a by-path rename from a dir-fd one on
    an unattacked run; what this guard buys is that an edit back to the by-path form reddens.
    """
    source = SCRIPT.read_text()

    assert "dir_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)" in source, (
        "the containing directory must be pinned with a descriptor before the write and rename"
    )
    assert (
        "os.replace(scratch.name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)" in source
    ), "the rename must be relative to the pinned dir fd for BOTH names (renameat semantics)"
    assert "os.replace(scratch, target)" not in source, (
        "the rename must not revert to the by-path form a swapped component can redirect"
    )
    # The scratch open and the cleanup unlink are pinned to the same directory fd, so neither can
    # be redirected by a component swap either.
    assert "dir_fd=dir_fd," in source
    assert "os.unlink(scratch.name, dir_fd=dir_fd)" in source


def test_a_symlinked_config_directory_lands_the_write_in_the_pinned_real_directory(tmp_path):
    """The dir-fd rename's outcome, behavioural (hy-5pjk).

    The script opens `target.parent` once and does the write and rename relative to that
    descriptor. When the config directory is itself a symlink, that open resolves it ONCE and
    pins the real directory's inode; the written inode must land in the REAL directory's
    settings.json as a fresh regular file. This exercises the dir-fd path end to end: the write
    reaches the resolved directory rather than a name a later re-resolution could point elsewhere.
    """
    real = tmp_path / "real-config"
    real.mkdir()
    (real / "settings.json").write_text('{"existing": true}\n')
    before = (real / "settings.json").stat().st_ino

    link = tmp_path / "config-link"
    link.symlink_to(real, target_is_directory=True)

    sandbox = _sandbox(tmp_path, settings={"existing": True})
    env = dict(sandbox["env"])
    env["CLAUDE_CONFIG_DIR"] = str(link)
    subprocess.run(
        ["bash", "-c", f"exec {SCRIPT} claude"], env=env, check=True, capture_output=True
    )

    written = real / "settings.json"
    assert not written.is_symlink(), "the settings file must be a real regular file, not a link"
    assert written.stat().st_ino != before, "a fresh inode must have been renamed into place"
    loaded = json.loads(written.read_text())
    assert loaded["autoCompactWindow"] == WINDOW, loaded  # the key the write adds
    assert loaded["existing"] is True, "the other keys survive the replacement"


def test_a_path_component_swapped_in_the_close_to_rename_window_cannot_redirect_the_write(tmp_path):
    """The parent-component class, PROVEN closed by planting IN the window (hy-5pjk, Option B).

    The earlier arms measure the dir-fd path on an UNATTACKED run; this one measures it
    under the attack the fix exists to stop. A `sitecustomize` loaded into the child wraps
    `os.replace` and, on the settings rename itself, repoints the config-directory symlink
    from the real directory to an attacker-controlled one -- firing precisely in the
    close->rename window a live race could not be made to hit deterministically.

    Because the rename resolves both names against the descriptor opened BEFORE the swap,
    the written inode still lands in the PINNED real directory: the swapped-in link is
    ineffective. That is the parent-component redirection closed. A revert to a by-path
    `os.replace(scratch, target)` reddens this: after the swap the scratch leaf resolves
    through the now-evil link and is not found, so the run aborts and the real file never
    gains the key.

    Option B, stated by the outcome: the LEAF residue (an unlink+recreate of `scratch.name`
    itself) is NOT closed here, and is not exercised, because it requires write access to
    the config directory -- at which privilege the same actor rewrites settings.json
    directly and the atomic write is not the boundary.
    """
    real = tmp_path / "real-config"
    real.mkdir()
    (real / "settings.json").write_text('{"existing": true}\n')

    evil = tmp_path / "evil-config"
    evil.mkdir()

    link = tmp_path / "config-link"
    link.symlink_to(real, target_is_directory=True)

    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "sitecustomize.py").write_text(WINDOW_SWAP_SITECUSTOMIZE)

    sandbox = _sandbox(tmp_path, settings={"existing": True})
    env = dict(sandbox["env"])
    env["CLAUDE_CONFIG_DIR"] = str(link)
    env["PYTHONPATH"] = str(harness)
    env["HYPERSET_SWAP_LINK"] = str(link)
    env["HYPERSET_SWAP_TO"] = str(evil)

    result = subprocess.run(
        ["bash", "-c", f"exec {SCRIPT} claude"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert Path(os.readlink(link)) == evil, (
        "the harness must have fired: the config link was repointed inside the window"
    )
    assert not (evil / "settings.json").exists(), (
        "the write must never land in the directory swapped in during the window"
    )
    written = real / "settings.json"
    assert not written.is_symlink(), "the pinned real file is a regular file, not a link"
    loaded = json.loads(written.read_text())
    assert loaded["autoCompactWindow"] == WINDOW, loaded
    assert loaded["existing"] is True, "the other keys survive the replacement"


def _skeleton(tmp_path: Path, *, directives=("mayor", "refinery")) -> Path:
    """A repository root holding only what this script preflights.

    A copy rather than the real tree because the interesting case is a directive
    that is ABSENT, and the real `docs/directives/` has both. `repo_root` is
    derived from the script's own location (`script_dir/..`), so placing the copy
    at `<root>/scripts/` is what points the preflight at this skeleton.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "gastown-agent.sh").write_bytes(SCRIPT.read_bytes())
    (root / "scripts" / "gastown-agent.sh").chmod(0o755)
    for skill in ("caveman", "ponytail"):
        (root / ".agents" / "skills" / skill).mkdir(parents=True)
        (root / ".agents" / "skills" / skill / "SKILL.md").write_text("skill\n")
    (root / "docs" / "directives").mkdir(parents=True)
    for name in directives:
        (root / "docs" / "directives" / f"{name}.md").write_text(f"# {name}\n")
    return root


def test_both_directives_are_installed_into_the_rig(tmp_path):
    """The merge steps live in the refinery's directive, so it has to arrive (hy-yyzn).

    Installing only `mayor.md` while merge execution moved out of it would leave
    the rig with an installed directive that no longer describes a merge and no
    installed copy of the one that does -- the button documented nowhere the seat
    holding it can read.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=0o600)

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    installed = Path(sandbox["env"]["GASTOWN_ROOT"]) / "hyperset" / "directives"
    assert (installed / "mayor.md").exists()
    assert (installed / "refinery.md").exists(), (
        "the seat that merges needs its own procedure installed, not just the mayor's"
    )
    assert stat.S_IMODE((installed / "refinery.md").stat().st_mode) == 0o644


def test_critic_alias_is_read_only(tmp_path):
    """The Claude critic must not inherit the worker's write-capable mode."""
    source = SCRIPT.read_text()

    assert "gt config agent set sonnet5-reviewer" in source
    critic = source.split("gt config agent set sonnet5-reviewer", 1)[1].split("# Mayor:", 1)[0]
    assert "--permission-mode plan" in critic
    assert "--allowed-tools Bash,Read,Grep,Glob" in critic
    assert "--disallowed-tools Edit,Write,NotebookEdit" in critic
    assert "--dangerously-skip-permissions" not in critic


def test_a_missing_directive_aborts_before_the_default_agent_is_switched(tmp_path):
    """Preflight both, or the abort lands half-applied (hy-fhz0, hy-0zy2, hy-yyzn).

    `install` runs after `gt config default-agent`, so a directive checked only at
    the install would abort with the town already switched and the directive not
    installed. Adding a second directive doubles the ways that can happen, so the
    assertion is not merely that it fails: it is that `gt config default-agent`
    was never called.
    """
    sandbox = _sandbox(tmp_path, settings={"keep": "me"}, mode=0o600)
    root = _skeleton(tmp_path, directives=("mayor",))

    result = subprocess.run(
        ["bash", "-c", f"umask 0022; exec {root / 'scripts' / 'gastown-agent.sh'} claude"],
        env=sandbox["env"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "docs/directives/refinery.md" in result.stderr, result.stderr
    log = Path(sandbox["env"]["STUB_LOG"])
    calls = log.read_text() if log.exists() else ""
    assert "default-agent" not in calls, (
        "the town must be untouched when a required directive is missing"
    )
    assert "autoCompactWindow" not in _body(sandbox["target"]), (
        "and the settings write must not have happened either"
    )
