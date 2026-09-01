"""What a run rested on, at two identities: the asset and the version (hy-o79s).

`source_refs` is the field a recording persists and `source_pairs` is the
finer identity the stability report derives from the same trace -- a set of
`(ref, identity)` PAIRS rather than joined `ref@identity` tokens, because `'@'`
is legal in both halves and a token is therefore a lossy key (hy-q2mn). Both walk one
payload, and this is where that walk is exercised at all: until hy-o79s the
only thing that ran it was an hour-long scheduled job against a live model, so
a parser that quietly stopped matching the payload would have been found by the
job it was recorded from rather than by a test.

The pairing is the point of most of what follows. A finer identity that agreed
with `source_refs` in every world would be a second copy of it, so each test
below names the world where the two counts come apart.
"""

from __future__ import annotations

import json
from pathlib import Path

from hyperset.evals.cases import load_cases
from hyperset.evals.raw_operations import GET_RAW_ASSET
from hyperset.evals.recording import GOVERNED_ARM, RAW_ARM
from hyperset.evals.run import recordings_of
from hyperset.evals.source_identity import (
    UNVERSIONED,
    observed_entries,
    source_pairs,
    source_refs,
)
from hyperset.planner.trace import PLANNER_MESSAGE, TOOL_RESULT
from hyperset.repositories.hash_basis import DROP_KEY_SUFFIXES, apply_hash_basis

REF = "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8"
BEFORE = "af1865adbac5ea97ae6382d17a063ca30abf0857956bf865c6e81a54e2dd6e77"
AFTER = "bbbb1865adbac5ea97ae6382d17a063ca30abf0857956bf865c6e81a54e2dd6e"


def governed_trace(*assets: dict) -> dict:
    """One tool result carrying linked evidence, in the shape a bundle has."""
    return {
        "provenance": {"runtime": "openai_agents_sdk"},
        "steps": [
            {
                "kind": TOOL_RESULT,
                "at": "2026-07-29T00:00:00+00:00",
                "detail": {
                    "operation": "resolve_context",
                    "result": {"linked_evidence": {"observed_assets": list(assets)}},
                },
                "summary": "",
            }
        ],
    }


def observed(ref: str = REF, *, content_sha256: str | None = BEFORE) -> dict:
    return {
        "ref": ref,
        "connector": "superset",
        "asset_type": "dataset",
        "observed_version": 1,
        "observed_version_id": "oav-508be14f8f6d",
        "content_sha256": content_sha256,
    }


def raw_trace(*payloads: dict) -> dict:
    return {
        "provenance": {"runtime": "openai_agents_sdk"},
        "steps": [
            {
                "kind": TOOL_RESULT,
                "at": "2026-07-29T00:00:00+00:00",
                "detail": {"operation": GET_RAW_ASSET, "result": payload},
                "summary": "",
            }
            for payload in payloads
        ],
    }


def test_a_re_observation_of_one_asset_is_one_ref_and_two_versions():
    """The critic's measurement at #143's head e90ccaf, as a test.

    Two recordings differing only in the observed asset's version fields print
    `source_refs_distinct=1` and `EVIDENCE identical`, because a ref names the
    ASSET. This is the world the second identity exists for, and the assertion
    that matters is that the two counts DISAGREE here -- a version identity
    that tracked the refs would be a second copy of a number the report
    already had.
    """
    before = governed_trace(observed(content_sha256=BEFORE))
    after = governed_trace(observed(content_sha256=AFTER))

    assert source_refs(before) == source_refs(after) == [REF]
    assert source_pairs(before) == [(REF, BEFORE)]
    assert source_pairs(after) == [(REF, AFTER)]
    assert source_pairs(before) != source_pairs(after)


def test_the_same_asset_at_the_same_version_is_one_version_too():
    """The other-way world, and the reason the test above is evidence.

    A projection that returned a fresh value per call -- a timestamp, an id
    minted at read time -- would make the disagreement above unfalsifiable by
    disagreeing always. Two runs that read the same bytes must come out equal
    at BOTH identities.
    """
    first = governed_trace(observed(content_sha256=BEFORE))
    second = governed_trace(observed(content_sha256=BEFORE))

    assert source_refs(first) == source_refs(second)
    assert source_pairs(first) == source_pairs(second) == [(REF, BEFORE)]


def test_one_repetition_that_read_two_versions_of_one_asset_keeps_both():
    """A refresh landing between two tool calls of the SAME run.

    `observed_entries` does not de-duplicate on ref, so the pair survives to
    the caller: one ref, two versions, inside one repetition. Collapsing here
    would hide it before either projection could report it, and the repetition
    would look like it read one thing.
    """
    trace = {
        "provenance": {"runtime": "openai_agents_sdk"},
        "steps": (
            governed_trace(observed(content_sha256=BEFORE))["steps"]
            + governed_trace(observed(content_sha256=AFTER))["steps"]
        ),
    }

    assert [ref for ref, _ in observed_entries(trace)] == [REF, REF]
    assert source_refs(trace) == [REF]
    assert source_pairs(trace) == sorted([(REF, BEFORE), (REF, AFTER)])


def test_an_asset_with_no_version_is_named_rather_than_dropped():
    """`content_sha256=None` is a real state, and silence about it is the defect.

    An entry omitted here would make a repetition that read an unversioned
    asset indistinguishable from one that never touched it -- the same shape as
    the finding this function closes, one level down.
    """
    trace = governed_trace(observed(content_sha256=None))

    assert source_refs(trace) == [REF]
    assert source_pairs(trace) == [(REF, UNVERSIONED)]


def test_the_raw_arms_identity_is_the_payload_it_fetched():
    """That surface has no version field, so the bytes are the only identity.

    `get_raw_asset` returns `external_id` and `raw_payload` and nothing about a
    version, so a re-sync between two repetitions moves neither the ref nor any
    version field -- and would read as identical evidence at BOTH identities if
    the payload were not hashed.
    """
    external = {"external_id": "ae48881d", "asset_type": "dataset"}
    before = raw_trace({**external, "raw_payload": {"name": "public.finance_orders_daily"}})
    after = raw_trace({**external, "raw_payload": {"name": "public.finance_orders_v2"}})

    assert source_refs(before) == source_refs(after) == ["dataset:ae48881d"]
    assert source_pairs(before) != source_pairs(after)
    assert source_pairs(before)[0][0] == "dataset:ae48881d"
    assert source_pairs(before)[0][1].startswith("sha256:")


def test_the_raw_identity_does_not_move_when_the_payload_did_not():
    """Key order is not a re-sync: the hash is over a normalised serialisation."""
    first = raw_trace(
        {
            "external_id": "ae48881d",
            "asset_type": "dataset",
            "raw_payload": {"name": "orders", "id": 1},
        }
    )
    second = raw_trace(
        {
            "external_id": "ae48881d",
            "asset_type": "dataset",
            "raw_payload": {"id": 1, "name": "orders"},
        }
    )

    assert source_pairs(first) == source_pairs(second)


def test_the_raw_identity_is_version_level_with_the_stores_narrowing():
    """The bound the raw arm's hash actually has, which is not byte-level.

    `get_raw_asset` serves `asset.current_version.raw_payload`, so the bytes
    this walk hashes are the bytes the STORE holds -- and the store advances
    `current_version` only when the hash over the `hash_basis`-NARROWED payload
    moves. Both halves are asserted here: the narrowed projections of a payload
    whose only movement is a `*_humanized` re-render are equal, so the store
    writes no version and the arm keeps serving the old payload, while the walk
    is shown to be sensitive enough to have seen the change had it landed. So a
    re-sync of that shape is invisible at BOTH identities, and calling the raw
    arm's identity a content hash of the payload without saying so would claim
    coverage it does not have (hy-szg4).

    The rule itself is bound to the real connector by
    `tests/postgres/test_superset_rest_sync.py::
    test_resync_of_unchanged_source_creates_no_version_despite_rerendered_times`,
    which asserts the stored `hash_basis` and that the resync writes no version.
    """
    basis = {DROP_KEY_SUFFIXES: ["_humanized"]}
    stored = {"name": "orders", "changed_on_humanized": "10 minutes ago"}
    re_rendered = {"name": "orders", "changed_on_humanized": "now"}

    assert apply_hash_basis(stored, basis) == apply_hash_basis(re_rendered, basis), (
        "the store sees no change, so it writes no version and the raw arm goes on serving the "
        "payload it already had"
    )
    assert source_pairs(raw_trace({"external_id": "ae48881d", "raw_payload": stored})) != (
        source_pairs(raw_trace({"external_id": "ae48881d", "raw_payload": re_rendered}))
    ), "the walk would have seen it -- what hides the re-sync is the store, not this hash"


def test_an_asset_named_under_another_key_contributes_neither_a_ref_nor_a_version():
    """The bound the finer identity does NOT close, asserted so it stays known.

    The walk reads `linked_evidence.observed_assets[].ref` and the raw arm's
    `external_id` only. A repetition that rested on an asset in any other shape
    records the empty set at both identities and prints `<none>`, which is why
    the document calls `<none>` on a governed line a finding rather than a
    formatting choice.
    """
    trace = {
        "provenance": {"runtime": "openai_agents_sdk"},
        "steps": [
            {
                "kind": TOOL_RESULT,
                "at": "2026-07-29T00:00:00+00:00",
                "detail": {"operation": "resolve_context", "result": {"assets": [{"ref": REF}]}},
                "summary": "",
            },
            {
                "kind": PLANNER_MESSAGE,
                "at": "2026-07-29T00:00:00+00:00",
                "detail": {"text": "answered from it anyway"},
                "summary": "",
            },
        ],
    }

    assert source_refs(trace) == []
    assert source_pairs(trace) == []


def test_two_distinct_pairs_that_join_to_one_token_stay_two():
    """Why the finer identity is pairs and not `ref@identity` strings (hy-q2mn).

    Both halves are arbitrary payload strings, so the join is not injective:
    `('dataset:a@b', 'c')` and `('dataset:a', 'b@c')` are one token. A projection
    returning tokens de-duplicates them, and a caller counting the result gets a
    set SMALLER than `source_refs` -- the refinement the stability line prints,
    inverted. The pairs are distinct, and the count that carries the finding is
    that this is not fewer than the coarse one.
    """
    left = governed_trace({"ref": "dataset:a@b", "content_sha256": "c"})
    right = governed_trace({"ref": "dataset:a", "content_sha256": "b@c"})

    assert source_pairs(left) == [("dataset:a@b", "c")]
    assert source_pairs(right) == [("dataset:a", "b@c")]
    assert source_pairs(left) != source_pairs(right), "distinct pairs, whatever they join to"
    assert len({*source_pairs(left), *source_pairs(right)}) == 2
    joined = {f"{ref}@{identity}" for ref, identity in (*source_pairs(left), *source_pairs(right))}
    assert joined == {"dataset:a@b@c"}, (
        "the collision itself, measured: one token for two pairs, which is why nothing counts it"
    )


def test_both_identities_read_the_committed_recordings():
    """The real payloads, not the fixtures above, at both arms.

    A parser exercised only against hand-built traces answers about the test
    file. Every ref the governed recording persisted must carry a version here,
    and the raw recording's must hash -- otherwise the finer count silently
    equals the coarse one on the only evidence this repository actually has.

    WHAT IT READ IS ASSERTED, BECAUSE EVERYTHING ELSE HERE IS INSIDE THE GLOB
    (hy-w07q). An empty directory runs the body zero times, so every assertion
    below is skipped and the test passes having read nothing -- while still
    reporting, to anyone reading the suite result, that the real evidence was
    checked. Measured at bcb891a by moving the four recordings one directory
    deeper, which is the exact restructure hy-qc4u performs: `1 passed, 9
    deselected`.

    The oracle is `load_cases()` rather than a hand-typed four, and it is the
    same one the required gate demands a recording per arm from, so a new case
    makes this red until its recordings exist instead of making this wrong. The
    pair comes from the recording's own `case_id` rather than the filename,
    because the filename is the half hy-qc4u is about to change.
    """
    read: list[tuple[str, str]] = []
    for arm in (GOVERNED_ARM, RAW_ARM):
        for path in [path for case in load_cases() for path in recordings_of(arm, case.id)]:
            recording = json.loads(Path(path).read_text())
            read.append((arm, recording["case_id"]))
            refs = source_refs(recording["trace"])
            versions = source_pairs(recording["trace"])

            assert refs == recording["source_refs"], (
                f"{arm}/{path.name}: the persisted field and the walk must agree, or the two "
                "counts on the stability line are about different sets"
            )
            assert len(versions) == len(refs) and refs, f"{arm}/{path.name} carries no evidence"
            assert [ref for ref, _ in versions] == refs
            assert UNVERSIONED not in {identity for _, identity in versions}, (
                f"{arm}/{path.name}: a committed recording whose evidence carries no version "
                "would make the finer identity vacuous on the only real evidence there is"
            )
            assert not [pair for pair in versions if "@" in pair[0] or "@" in pair[1]], (
                f"{arm}/{path.name}: the bound on the render token's ambiguity (hy-q2mn). "
                "Nothing counts the joined token, so an '@' here is not a wrong number -- but it "
                "is the only way `ref@identity` in a rendered report becomes unreadable, and no "
                "producer in this repository writes one"
            )

    expected = sorted((arm, case.id) for arm in (GOVERNED_ARM, RAW_ARM) for case in load_cases())
    assert sorted(read) == expected, (
        f"read {sorted(read)} and the arms owe {expected}: this test is the one place both "
        "source identities meet the only real evidence there is, so reading none of it must be "
        "red rather than an empty loop reported as a pass"
    )
