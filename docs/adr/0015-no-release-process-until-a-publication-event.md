# 0015: No release process until a publication event, and beads hold the notes until then

Status: accepted.

## Context

`hy-yvrx` recorded a real obligation and could not find anywhere to put it. PR
#98 renamed the optional extra from `agent` to `planner`, which breaks
`pip install hyperset[agent]` for anyone who had used it. The recommendation
was to carry it as a line against the first release. Measured, twice and
independently:

- no git tags, at all;
- no `CHANGELOG` at the repository root or under `docs/`;
- no `docs/release*.md`;
- no publish step in any workflow — nothing uploads to PyPI, nothing pushes an
  image;
- `pyproject.toml` nevertheless declares a distributable surface: name
  `hyperset`, version `0.1.0`, Apache-2.0, a `hyperset` console script, and the
  `planner` extra.

Meanwhile `README.md` delivers the project as `docker compose` and
`uv run hyperset`, and ADR 0008 makes v0 a local Docker Compose stack with
`#34` as the gate for v0 being done.

So the obligation had nowhere to go because **this project has no release
surface: it has never published anything**. Nobody can be installing
`hyperset[agent]` today, and "version-affecting" is a property of a thing that
does not exist. Creating a one-entry `CHANGELOG` to hold the line would have
moved the decision rather than made it, and left the next person deciding what
the file was for.

## Decision

1. **The trigger is a publication event, not a date or a milestone.** The first
   git tag, PyPI upload, or pushed image is what creates the obligation to have
   release machinery. Until one of those is wanted: no `CHANGELOG`, no tags, no
   release document. This is a decision about scope, not a deferral — there is
   nothing to release *from* and nothing released *to*.
2. **Beads are the register, under the `release-note` label.** A bead carrying
   a release obligation gets that label and stays open; the first release
   assembles its notes with `bd list --label release-note` rather than from a
   file nobody maintained between releases. The notes already exist as a side
   effect of doing the work, which is the argument for using them instead of a
   parallel file that must be remembered separately.

   A `CHANGELOG` earns its keep at the **second** release, when two published
   things exist to diff. Before the first, it is a file nobody maintains and
   everybody trusts.

   Use the exact `--label` form. `bd list --label-pattern` is inert as of beads
   1.1.0: measured in this rig, `--label-pattern 'release*'` and
   `--label-pattern 'zzznope*'` both return 74 rows against 41 open — a pattern
   that cannot match anything returns the same rows as one that can, and the
   count exceeds the open set, so it bypasses the default status filter as
   well. It fails silently in the direction that resembles success. Filed as
   `hq-jz3h` in the town database, which is a different database from this
   rig's; the behaviour above is what was reproduced here.
3. **What the first publication must do**, recorded now so it is not
   re-derived under time pressure:
   - cut the tag at a SHA that passes `#34`'s end-to-end gate;
   - **the first tag *sets* `pyproject.toml`'s version; it does not agree with
     it.** `0.1.0` is a placeholder that no tag corresponds to and no artifact
     was ever built from. It is not churned now, and it must not be read later
     as evidence that `0.1.0` was released;
   - carry every `release-note` bead, `hy-yvrx` included.

## Consequences

- `hy-yvrx` stays open with the `release-note` label. It is not orphaned by
  depending on work nobody has been asked to do, which was `hy-x3gr`'s finding.
- Anyone who wants a release must first decide to publish. That decision brings
  the machinery with it — tag, notes, version — rather than the machinery
  arriving first and waiting for a purpose.
- A reader finding `version = "0.1.0"` in `pyproject.toml` should read it as
  unset. This ADR is what that reader is pointed at.
- If publication never happens in v0, nothing here has cost anything: no file
  was created, and the obligations are attached to the beads that generated
  them.
