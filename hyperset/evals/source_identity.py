"""What one run's evidence rested on, at two identities (hy-o79s, GitHub #25).

ONE WALK, TWO PROJECTIONS. `observed_entries` is the only parser of the
evidence payload in this repository, and both identities are views of what it
returned: the `ref` half names the ASSET and the `(ref, identity)` PAIR names
the VERSION of it. Two parsers would be two answers to "which assets did the
run rest on", and the two counts the stability line prints are only comparable
while they see the same set.

THE VERSION IDENTITY IS THE PAIR AND NEVER A JOINED STRING, which is the fix
for hy-q2mn and the reason no function here returns one. `f"{ref}@{identity}"`
was the version projection, and that map is NOT injective: `ref` is
`asset.get('ref')` and `identity` is `asset.get('content_sha256')`, both
arbitrary payload strings, so `'@'` is legal in either half and
`('dataset:a@b', 'c')` and `('dataset:a', 'b@c')` collide on one token.
Measured, at this module's own level: two runs with those entries walk to two
distinct ref sets and one distinct token set -- the refinement the stability
line prints, inverted, arriving through the DELIMITER rather than through two
sources (which is what hy-szg4 closed). Grouping, counting and comparing all
happen on the pair, where the refinement is true by type because the ref set is
a projection of the pair set. A token is built at RENDER time only, by
`stability._render_versions`, where a collision is a cosmetic ambiguity in a
line a human reads rather than a false count.

THE BOUND ON THAT COLLISION, on the record because it decides how much it
mattered: the identity must contain `'@'`, and this repository's producers
never write one -- the governed arm's identity is a store `content_sha256` and
the raw arm's is a `sha256:`-prefixed content hash, and all four committed
recordings walk to entries with no `'@'` in either half. So what was defective
was the universal claim the report printed, not any number this repository has
produced.

WHY THIS IS NOT IN `run.py`, where the walk used to live. Nothing here needs a
model, a database session or a subprocess: it reads a trace a recording already
persisted. `stability.py` reports on recordings and must not be able to run
one, and importing the walk from `run.py` put `subprocess` and
`OpenAIAgentsRuntime` in the reporting module's import closure -- a reporting
path that can reach an inference runtime is one refactor away from re-running
the thing it reports on. `tests/unit/evals/test_report_time_purity.py` pins
that closure.

WHAT NEITHER IDENTITY SEES, because both walk exactly one payload shape: an
asset named under any key other than `linked_evidence.observed_assets[].ref` or
the raw arm's `GET_RAW_ASSET` `external_id` contributes no ref and therefore no
version. That repetition records the empty set and the report prints `<none>`.
"""

from __future__ import annotations

import json

from hyperset.evals.raw_operations import GET_RAW_ASSET
from hyperset.planner.trace import TOOL_RESULT, content_hash

UNVERSIONED = "unversioned"
"""An observed asset whose linked evidence carries no `content_sha256`.

Named rather than dropped: an entry silently omitted here would make a
repetition that read an unversioned asset indistinguishable from one that never
touched it, which is the shape of the defect the version identity exists to
close.

There is nothing finer to fall back to. `content_sha256`, `observed_version`
and `observed_version_id` are all read off `asset.current_version`
(`bundle/resolver.py`), and `observed_asset_versions.content_hash` is
`nullable=False`, so a missing hash means there is no current version at all
and the version NUMBER is `None` in the same payload. The report therefore
renders the vacuity instead of pretending to an identity: a count of zero
versions must not read as agreement (`stability.render`).
"""


def observed_entries(trace: dict) -> list[tuple[str, str]]:
    """Every (ref, version identity) pair in one run's evidence, in trace order.

    THE PAIR IS THE UNIT every caller compares, and it is returned unjoined for
    the reason the module docstring measures: `'@'` is legal in both halves, so
    a joined token is a lossy key and only ever a rendering.

    Pairs are not de-duplicated on ref: one ref appearing twice under two
    identities is one repetition that read two versions of one asset -- a
    refresh landing between two tool calls of the SAME run -- and collapsing on
    ref here is how that becomes invisible before either caller sees it.

    A VERSION IDENTITY IS A FACT ABOUT THE RUN, not today's reader's opinion of
    it: this walks the trace the recording already persisted, so nothing here
    re-reads an asset and the identity is the one the model actually read.

    WHAT THE IDENTITY IS PER SURFACE, and what each one bounds:

    - the governed arm's linked evidence carries the store's `content_sha256`,
      so the identity is the version the store assigned;
    - the raw arm's `GET_RAW_ASSET` result has no version field at all, so the
      identity is a content hash of the `raw_payload` it fetched -- which is
      VERSION-LEVEL WITH THE STORE'S NARROWING, not byte-level. That tool
      returns `asset.current_version.raw_payload` (`evals/raw_arm.py`), and the
      store advances `current_version` only when the hash over the
      `hash_basis`-narrowed payload moves (`repositories/postgres/
      observed_assets.py`). So a re-sync that changes only Superset's
      `*_humanized` relative times, or reorders a DataHub `customProperties`
      map, writes no version, leaves the bytes this arm returns untouched, and
      is invisible at BOTH identities. The bound is what the store counts as a
      change, not what the source's bytes did.
    """
    entries: list[tuple[str, str]] = []
    for step in trace.get("steps") or []:
        if step.get("kind") != TOOL_RESULT:
            continue
        detail = step.get("detail") or {}
        payload = detail.get("result") or {}
        for asset in (payload.get("linked_evidence") or {}).get("observed_assets") or []:
            ref = asset.get("ref")
            if isinstance(ref, str):
                entries.append((ref, asset.get("content_sha256") or UNVERSIONED))
        if detail.get("operation") == GET_RAW_ASSET and payload.get("external_id"):
            entries.append(
                (
                    f"{payload.get('asset_type')}:{payload['external_id']}",
                    content_hash(json.dumps(payload.get("raw_payload"), sort_keys=True)),
                )
            )
    return entries


def source_refs(trace: dict) -> list[str]:
    """The observed source dependency refs this run's evidence rests on.

    Derived AT RECORD TIME and persisted on the `Recording`, which is the
    difference between a fact about the run and today's reader's opinion of
    yesterday's payload. Both arms answer it from their own surface: the
    governed arm from the linked evidence in a bundle, the raw arm from the
    assets it actually fetched.
    """
    return sorted({ref for ref, _ in observed_entries(trace)})


def source_pairs(trace: dict) -> list[tuple[str, str]]:
    """The same evidence as a normalised SET of pairs, keyed by version (hy-o79s).

    `source_refs` identifies an ASSET and never an asset version, so a
    re-observation between two repetitions -- new `observed_version`, new
    `content_sha256`, the SAME ref -- is the same set there. This is the finer
    identity, and it exists because a mid-run re-observation does not merely
    mislabel: the governed context is read from the store per repetition, so a
    connector run or an approval landing mid-record changes what the model
    READ.

    PAIRS RATHER THAN `ref@identity` TOKENS (hy-q2mn). Both halves are arbitrary
    payload strings, so a joined token de-duplicates two distinct pairs into one
    and this function would return a set SMALLER than `source_refs` -- the
    refinement it exists to provide, inverted. As pairs, `source_refs` is the
    image of this set under `ref`, so it can never be the larger of the two.

    Derived rather than persisted because `source_refs` is the field GitHub #25
    requires a recording to carry, and a finer identity wanted by the report is
    not a reason to change what a recording means.
    """
    return sorted(set(observed_entries(trace)))
