#!/usr/bin/env bash
#
# Switch the Gas Town default between Hyperset's Codex and Claude profiles.
# Repo-local operator scope only: profile registration, user settings, and the
# live Mayor directive. It does not configure or run the Hyperset product.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The repository root, one level up now that this lives in scripts/ (hy-gh-99).
# Resolved rather than assumed so the script works from any working directory,
# which is how launchd and the shim at the old path both invoke it.
repo_root="$(cd "$script_dir/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ./scripts/gastown-agent.sh codex|claude

  codex   Use GPT-5.6 Sol with high reasoning effort.
  claude  Use Claude Opus 4.8, medium effort, as the crew default (hyperion).
          Mayor runs opus48-high (opus 4.8, high effort). Review is dual-model:
          the Claude critic runs sonnet5-reviewer (Sonnet 5) and the adversary
          critic runs codex-luna-xhigh (GPT-5.6 Luna, xhigh). The mayor's
          cross-model consultant also runs codex-luna-xhigh. Each is set via
          --agent at start time.

The switch applies to newly started Gas Town agents. Restart an existing
crew session to move that session to the new default.

Set GASTOWN_ROOT or GASTOWN_RIG to override the default town ($HOME/gt) or
rig (hyperset).
EOF
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

case "$1" in
  codex)
    selected_agent="codex-sol-high"
    ;;
  claude)
    selected_agent="opus48-medium"
    ;;
  *)
    usage
    exit 2
    ;;
esac

command -v gt >/dev/null 2>&1 || {
  echo "ERROR: gt is not installed or not on PATH." >&2
  exit 1
}
for skill in caveman ponytail; do
  if [ ! -f "$repo_root/.agents/skills/$skill/SKILL.md" ]; then
    echo "ERROR: required repo skill missing: .agents/skills/$skill/SKILL.md" >&2
    exit 1
  fi
done
# Checked here rather than at the install below, which is the last step: without
# this, a missing or moved directive aborts under `set -e` with the default agent
# already switched and the directive not installed -- the same half-applied state
# the settings write was reordered to avoid, reached from the bottom instead of
# the top. hy-gh-99 moved this script's own path once, so a moved source file is
# not hypothetical.
# Readability, not just existence: `install` has to read this file, so a mode-000
# or otherwise unreadable one passed an existence check and then failed at line
# 197 with the default agent already switched -- the same half-applied state, one
# test short (hy-fhz0).
# BOTH directives, because the merge steps live in the refinery's (hy-yyzn). When
# only mayor.md was installed, moving merge execution out of it would have left
# the rig with an installed directive that no longer describes a merge and no
# installed copy of the one that does -- the button documented nowhere the seat
# holding it can read. Preflight both here, before the settings write and the
# default switch, so a missing or unreadable one aborts with the town untouched
# rather than half-applied.
directive_names="mayor refinery"
for directive in $directive_names; do
  source_path="$repo_root/docs/directives/$directive.md"
  if [ ! -f "$source_path" ]; then
    echo "ERROR: required directive missing: docs/directives/$directive.md" >&2
    exit 1
  fi
  if [ ! -r "$source_path" ]; then
    echo "ERROR: required directive not readable: docs/directives/$directive.md" >&2
    exit 1
  fi
done

gastown_root="${GASTOWN_ROOT:-$HOME/gt}"
gastown_rig="${GASTOWN_RIG:-hyperset}"
if [ ! -f "$gastown_root/mayor/town.json" ]; then
  echo "ERROR: Gas Town root not found at $gastown_root." >&2
  exit 1
fi
if [ ! -d "$gastown_root/$gastown_rig" ]; then
  echo "ERROR: Gas Town rig not found at $gastown_root/$gastown_rig." >&2
  exit 1
fi
# Created here rather than beside the `install` at the bottom, which is the last
# step. The two checks above cover a bad `$gastown_root` or `$gastown_rig`, but
# nothing covered the `directives` LEAF being a non-directory or the rig
# directory being unwritable, and under `set -e` either one aborted the mkdir
# AFTER `gt config default-agent` -- the half-applied state the directive
# preflight above was added to prevent, reached one level lower down (hy-0zy2).
# It depends on nothing between here and there, so making it early costs nothing.
mkdir -p "$gastown_root/$gastown_rig/directives"
cd "$gastown_root"

# Claude Code auto-compacts only a model it can resolve an auto-compact window
# for, and 2.1.206 ships a built-in window for exactly one model, which is not
# `claude-opus-5` (hy-gh-121). An opus-5 session therefore never compacts: it
# fills to the hard limit, prints "Context limit reached - /compact or /clear to
# continue", and waits for a keypress no unattended agent supplies. The
# PreCompact hook is innocent; it never fires, because compaction never starts.
#
# Measured on claude-code 2.1.206, which is the version this whole paragraph is
# pinned to: window resolution is undocumented and moved once already without a
# changelog entry. `claude -p "/context"` is the instrument -- it grows an
# "Autocompact buffer" row once a window resolves, and omits the row entirely
# when none does, which is why an unset seat has no reserve to fall back on.
# `--model claude-opus-5` alone: no row. With this setting at 800000: a 33k
# buffer row. That row is the reserve rather than the window, so it proves a
# window resolved without proving 800000 is the value that took. At 2000000: no
# row again, because the setting is schema-bounded to 100000..1000000 and an
# out-of-range value is discarded rather than clamped.
#
# This goes in Claude's own settings rather than on the agent command line: a
# leading `env VAR=... claude` displaces argv[0], and gt infers the provider
# that drives session resume from the command binary name (hy-9mrn).
#
# User scope rather than a per-project `.claude/settings.json`, because project
# settings reach only a session whose working directory is that project. The
# hyperset crew seats sit in clones carrying this repository's tracked
# `.claude/settings.json`, but `witness/` has no such file at all, so no
# per-project write covers every seat. This is not gt clobbering a key we add:
# `gt hooks sync --help` documents its step 4 as merging the hooks section into
# an existing settings.json "preserving all fields", and `gt hooks diff` on this
# workspace reports hook deltas only, never the removal of a non-hook key.
#
# The write runs before any registration or default switch below, so a settings
# file this script refuses to parse aborts with the town untouched rather than
# half-switched (hy-59sd).
auto_compact_window=800000
claude_settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
python3 - "$claude_settings" "$auto_compact_window" <<'PY'
import json
import os
import pathlib
import stat
import sys

path, window = pathlib.Path(sys.argv[1]), int(sys.argv[2])
# A dotfiles-managed settings.json is commonly a symlink into a tracked
# repository. Renaming over the link would delete the link and leave the real
# file stale, so resolve it and write THROUGH to the real file: the link keeps
# working and the dotfiles repo sees the change, which is what that operator set
# the link up to get. The alternative -- refusing to touch a symlink -- is safer
# in the abstract but would make this script unusable for them, and since the
# write is a precondition for everything below, it would refuse to run at all.
# Silently destroying the link was never an option (hy-fhz0).
target = pathlib.Path(os.path.realpath(path)) if path.is_symlink() else path
# This READ is the script's first contact with the file, and it must refuse a
# non-regular target for the same reason the scratch WRITE does (hy-cznz): an
# O_RDONLY open on a FIFO BLOCKS for a writer that never comes -- silently,
# forever, under launchd -- and here it happens before the script has printed
# anything at all. `read_text()` opened O_RDONLY with no O_NONBLOCK and no shape
# guard, so a FIFO planted at settings.json hung every run (hy-luw9).
#
# O_NONBLOCK stops the hang but is NOT enough on its own: O_RDONLY | O_NONBLOCK
# SUCCEEDS on a FIFO and returns EOF, so a bare read would see raw="" and treat
# the settings file as absent -- writing a fresh one straight over it. So fstat
# the RAW descriptor and enforce S_ISREG BEFORE wrapping it in a text handle: a
# DIRECTORY (and other non-regular shapes) makes `os.fdopen`/read raise before a
# post-wrap check could run, so the check has to come first -- then a directory,
# FIFO, or special is refused with a named sentence instead of a traceback.
#
# The open FOLLOWS symlinks, deliberately and consistently with the WRITE side. A
# dotfiles-managed settings.json is a symlink into a tracked repo, and the write
# resolves it and writes THROUGH (test_a_symlinked_target_is_written_through);
# refusing to follow here would break that legitimate setup. Following still
# closes the FIFO block -- a symlink pointing at a FIFO resolves to the FIFO and
# S_ISREG refuses it. The residual (a symlink swapped to an attacker's regular
# file between resolution and open) is the SAME directory-write threat model
# already accepted in hy-anhb/hy-5pjk: reaching it needs write access to the
# config directory, which already permits writing settings.json directly, so it
# is NOT closed with O_NOFOLLOW (that would break dotfiles). Absent stays absent:
# a missing file opens with ENOENT and is read as the empty string as before.
try:
    _fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
except FileNotFoundError:
    raw = ""
except OSError as error:
    # A genuine permission refusal, or a circular symlink (ELOOP): a sentence
    # rather than a traceback, the way the scratch refusals read.
    sys.exit(f"ERROR: cannot read {target}: {error}.")
else:
    _info = os.fstat(_fd)
    if not stat.S_ISREG(_info.st_mode):
        if stat.S_ISFIFO(_info.st_mode):
            detail = (
                "it is a FIFO, not a regular file; an O_RDONLY open on it blocks "
                "for a writer that never comes"
            )
        elif stat.S_ISDIR(_info.st_mode):
            detail = "it is a directory, not a regular file"
        else:
            detail = "it is not a regular file"
        os.close(_fd)
        sys.exit(f"ERROR: cannot read {target} as Claude's settings: {detail}.")
    with os.fdopen(_fd, "r") as _handle:
        raw = _handle.read()
try:
    settings = json.loads(raw) if raw.strip() else {}
except json.JSONDecodeError as error:
    # Claude accepts comments in this file and json does not. Refuse rather
    # than overwrite settings we cannot read back.
    sys.exit(f"ERROR: cannot parse {target}: {error}")
if not isinstance(settings, dict):
    # Parsed, but not an object. `[1, 2]`, `"hi"`, `42`, `null` and `true` are all
    # valid JSON, none of them has `.get`, and the next line therefore died on
    # `AttributeError` one line below a deliberate refusal -- telling the operator
    # nothing about WHICH of the two things happened, and leaving `set -e` to abort
    # at the same point either way. Deliberately NOT folded into the clause above:
    # all five DO parse, so widening that sentence would make it false, and a bare
    # `except AttributeError` there is how a real refusal gets swallowed later
    # (hy-x83h).
    kind = {
        list: "array",
        str: "string",
        bool: "boolean",
        int: "number",
        float: "number",
        type(None): "null",
    }.get(type(settings), type(settings).__name__)
    sys.exit(
        f"ERROR: cannot use {target}: it parsed as a JSON {kind}, not an object. "
        "Claude's settings file must be a JSON object."
    )
if settings.get("autoCompactWindow") == window:
    # Already ours. Do not rewrite a file to say what it already says.
    sys.exit(0)
# Reuse the file's own indent unit where one can be detected: the first indented
# line of a JSON object is one level deep, so its leading whitespace is the unit.
# A minified file has no indented line and falls back to two spaces, which adds
# indentation rather than preserving it -- the honest limit of this, and why
# AGENTS.md says "where one can be detected" (hy-59sd, hy-qdd2).
indent = 2
for line in raw.splitlines()[1:]:
    body = line.lstrip(" \t")
    if body and body != line:
        indent = line[: len(line) - len(body)]
        break
settings["autoCompactWindow"] = window
target.parent.mkdir(parents=True, exist_ok=True)
# This file can hold `env` and `apiKeyHelper`, so treat it as credentials-capable.
# Replacing it renames a NEW inode over the old one, and that inode carries the
# invoking shell's umask rather than the original mode: the write does not merely
# widen, it DISCARDS the mode and substitutes umask -- 0600 becomes 0644 at umask
# 022, and 0644 becomes 0600 at umask 077. So preserve the mode instead of
# hardening it. Forcing 0600 would satisfy the widening case and still be wrong,
# because it would silently narrow a legitimately 0644 file and break other
# readers of it. Only a file we create has no mode to copy, and 0600 is the right
# default for one this script creates (hy-qdd2).
previous = target.stat() if target.exists() else None
mode = stat.S_IMODE(previous.st_mode) if previous else 0o600
trailing = "\n" if not raw or raw.endswith("\n") else ""
scratch = target.with_name(target.name + ".gastown-agent")
# O_NOFOLLOW because this scratch path is predictable and must not be followed
# somewhere else. The 0o600 mode argument applies ONLY when open() creates the
# file, so it does nothing for a scratch that already exists -- O_TRUNC empties
# such a file but leaves its old mode intact, and the content would then be
# written while it is still world-readable. No attacker is needed to reach that:
# the version of this script before mode preservation created the scratch at
# umask, so a SIGKILL of that version leaves a 0644 scratch behind for this
# version to reuse. Hence fchmod on the descriptor before any write (hy-fhz0).
#
# REMOVE A STALE REGULAR SCRATCH FIRST, because there is one leftover the open
# itself cannot survive (hy-2eez). A leftover at mode 000 makes os.open raise
# EACCES, and that raise happens BEFORE the try below -- so the unlink that
# guarantees "never leave a scratch behind" never runs, the 000 file survives,
# and every later run fails identically. A permanent wedge needing no attacker:
# a SIGKILL of an older version of this script is enough to plant it. Removing it
# here also means the open always creates a fresh inode, so the mode argument is
# honoured and the fchmod above is belt to its braces rather than the only guard.
#
# REGULAR FILES ONLY, and that is the deliberate half. The line is whether THIS
# SCRIPT can plant the shape itself: it plants a regular file whenever a run is
# killed mid-write, so removing that is cleaning up after itself, not a licence.
# It never creates a symlink or a directory there, so one of those means something
# else made it, and removing it would destroy what this script did not create --
# unrecoverably, and in the symlink case by deleting a link rather than refusing
# it. So those are refused, with the link's victim untouched. Nothing legitimate
# ever lives at this path: it is only this script's own in-flight scratch, and a
# run that completes leaves none.
#
# Removal rather than a try/except around the open, because catching EACCES there
# would also catch a genuine permission refusal on the TARGET's directory and
# retry into the same wall. This only ever unlinks the scratch; a refusal that is
# really about the target still surfaces from the open unchanged.
#
# Each refused shape gets its OWN message, because the right operator action
# differs and for the symlink it is not "delete it". These refusals do not replace
# the flags below -- the path can change between this lstat and that open, so the
# open must still refuse on its own. The two are not symmetric and were once written
# as though they were: O_NOFOLLOW makes the open raise ELOOP for a symlink planted in
# that window, but nothing in the shipped flag set refused a FIFO planted there --
# it BLOCKED. O_NONBLOCK is what makes the open cover this shape too, failing with
# ENXIO instead (hy-cznz). The refusals exist so the operator meets a sentence
# instead of `OSError: [Errno 62] Too many levels of symbolic links`, which is what
# the deliberate refusals used to say (hy-lsrx). Everything non-regular is refused
# here rather than left to the open, because one shape does not fail there at all:
# an `O_WRONLY` open on a FIFO BLOCKS for a reader that never comes. Measured --
# still blocked after 5s with no reader, and with this branch removed the suite's
# FIFO test stops at its 60s timeout instead of failing. A hang with nothing on
# stderr is the worst of the three for an unattended run.
try:
    stale = scratch.lstat()
except FileNotFoundError:
    stale = None
if stale is not None and not stat.S_ISREG(stale.st_mode):
    if stat.S_ISLNK(stale.st_mode):
        detail = (
            "it is a symlink, and this script only ever creates a regular file "
            "there, so something else made it; find out what did rather than "
            "deleting it"
        )
    elif stat.S_ISDIR(stale.st_mode):
        detail = "it is a directory; this script's scratch path must be free"
    else:
        detail = (
            "it is not a regular file, and this script only ever creates a regular "
            "file there, so something else made it; find out what did"
        )
    sys.exit(f"ERROR: cannot use the scratch path {scratch}: {detail}.")
if stale is not None:
    try:
        scratch.unlink()
    except OSError as error:
        # Name the remedy, not just the errno: the failure a reader meets here is
        # a file they can delete, and the previous version left them to infer that
        # from `Permission denied: '.gastown-agent'`. The second clause is there
        # because the cause can be the CONTAINING DIRECTORY -- at mode 0500 the
        # unlink fails and the operator cannot delete it either, so "delete it"
        # alone is advice they cannot follow (hy-lsrx).
        sys.exit(
            f"ERROR: cannot remove the stale scratch file {scratch}: {error}. "
            "It is a leftover from an interrupted run; delete it and re-run. If "
            f"deleting it also fails, {scratch.parent} is not writable."
        )
# O_NONBLOCK so a FIFO reaching this open fails with ENXIO instead of waiting for a
# reader that never comes. Measured on this machine against a FIFO with no reader:
# without it the open was still blocked after 3s; with it, ENXIO in 0.00s; and a
# regular file opens in 0.00s either way, because O_NONBLOCK is a no-op for
# regular-file writes -- so the flag costs the ordinary path nothing (hy-cznz).
# Pin the CONTAINING DIRECTORY with a descriptor and do BOTH the scratch open and
# the rename RELATIVE to it (renameat semantics), so the rename cannot be redirected
# by a symlink swapped into a path COMPONENT between the open and the rename (hy-5pjk).
# `os.replace` by full path re-resolves every component on the call, so an attacker
# who wins the close->rename window could move settings.json (which holds `env` and
# `apiKeyHelper`) somewhere they control, or turn it into a symlink. A rename against
# this dir fd resolves the directory ONCE, here, and is pinned to that inode for the
# open, the rename, and the cleanup below; no later swap of a path component moves it.
# NOT O_NOFOLLOW: a legitimately symlinked config directory must still resolve -- what
# matters is that it resolves once and is then held by descriptor, not re-walked.
dir_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
try:
    fd = os.open(
        scratch.name,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        os.fchmod(fd, 0o600)
        # Mode and owner go on the DESCRIPTOR, not on the path. `os.chmod` and
        # `os.chown` follow symlinks, so doing this by path undid the O_NOFOLLOW two
        # lines up: a symlink swapped in after the open would take the settings file's
        # mode, and the chown would aim at whatever it pointed to. Measured: chmod
        # through a symlink moves the victim's mode, not the link's. The descriptor
        # cannot be redirected, so these reach the inode this script opened and nothing
        # else (hy-anhb). The ownership refusal stays inside the `with` because
        # SystemExit is a BaseException and the handler below still unlinks the scratch.
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(settings, indent=indent) + trailing)
            if previous is not None and (previous.st_uid, previous.st_gid) != (
                os.geteuid(),
                os.getegid(),
            ):
                try:
                    os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
                except OSError as error:
                    # Refuse rather than quietly reassign someone else's settings file
                    # to whoever ran the script.
                    sys.exit(f"ERROR: cannot preserve ownership of {target}: {error}")
            # After the chown, which can clear mode bits, and before the close.
            os.fchmod(handle.fileno(), mode)
        # renameat against the pinned dir fd for BOTH names. The directory these names
        # resolve in is the one opened above, not whatever the path resolves to now, so
        # a symlink swapped into a PATH COMPONENT -- a parent of settings.json, or the
        # config directory itself -- between the descriptor's close and the rename cannot
        # redirect where settings.json (which holds `env` and `apiKeyHelper`) lands. That
        # PARENT-COMPONENT class is CLOSED here (hy-5pjk). Both names live in the same
        # directory, so this stays an atomic same-filesystem rename exactly as before.
        #
        # THREAT MODEL, narrowed on purpose (Option B), not left half-done. A rename
        # primitive still moves a NAME, so the ONE residue is the scratch LEAF itself: an
        # attacker who can unlink `scratch.name` in THIS directory and recreate it inside
        # the close->rename window could have that entry renamed into place. That requires
        # WRITE access to the config directory -- and at that privilege the atomic write is
        # NOT the security boundary: the same access rewrites settings.json directly,
        # symlink or not. So this is hardening of the path-component class, with no
        # privilege escalation left on the leaf. Closing the leaf too would need an
        # inode-exact swap (renameat2 RENAME_EXCHANGE / renameatx_np) or an O_TMPFILE +
        # linkat-by-fd handoff -- none of which is portable (macOS has no O_TMPFILE, no
        # /proc/self/fd, no renameat2) nor exposed by `os.replace`, and `linkat` cannot
        # atomically overwrite an existing file. No clean portable full closure exists,
        # so the fix pins the DIRECTORY component rather than chasing one.
        os.replace(scratch.name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        # Never leave a scratch file behind; unlink it relative to the SAME pinned
        # directory so the cleanup cannot be redirected either.
        try:
            os.unlink(scratch.name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
finally:
    os.close(dir_fd)
PY

# Keep worker, mayor, and reviewer aliases registered across switches.
gt config agent set codex-sol-high \
  'codex --dangerously-bypass-approvals-and-sandbox --model gpt-5.6-sol --config model_reasoning_effort="high"'
gt config agent set codex-reviewer \
  'codex --sandbox read-only --ask-for-approval never --model gpt-5.6-sol'
# Cross-model adversary: GPT-5.6 Luna at xhigh reasoning, read-only. Drives both
# the second review seat (adversary critic, alongside the Claude critic) and the
# mayor's cross-model consultant seat -- one alias, both are strictly read-only.
gt config agent set codex-luna-xhigh \
  'codex --sandbox read-only --ask-for-approval never --model gpt-5.6-luna --config model_reasoning_effort="xhigh"'
# Reviews (critic) run on Sonnet 5 while the Codex usage limit is down. Plan
# mode is Claude's enforced read-only permission mode; keep the shell available
# for inspection and local tests without granting the critic edit/write tools.
gt config agent set sonnet5-reviewer \
  'claude --model claude-sonnet-5 --effort high --permission-mode plan --allowed-tools Bash,Read,Grep,Glob --disallowed-tools Edit,Write,NotebookEdit'
# Mayor: opus 4.8, high effort.
gt config agent set opus48-high \
  'claude --model claude-opus-4-8 --effort high --dangerously-skip-permissions --tools Bash,Edit,Read,Grep,Glob,Task'
# Crew worker (hyperion, the only crew member): opus 4.8, medium effort.
gt config agent set opus48-medium \
  'claude --model claude-opus-4-8 --effort medium --dangerously-skip-permissions --tools Bash,Edit,Read,Grep,Glob,Task'
gt config default-agent "$selected_agent"

for directive in $directive_names; do
  install -m 0644 "$repo_root/docs/directives/$directive.md" \
    "$gastown_root/$gastown_rig/directives/$directive.md"
done

echo "Gas Town default agent: $selected_agent"
echo "Claude auto-compact window: $auto_compact_window in $claude_settings (new sessions only)"
for directive in $directive_names; do
  echo "Gas Town directive: $gastown_rig/directives/$directive.md"
done
echo "Repo skills: caveman + ponytail"
