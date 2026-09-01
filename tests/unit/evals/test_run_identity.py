"""Telling two runs of one case apart, and refusing when they cannot be (hy-qc4u).

The store used to hold one file per (arm, case), so a second session of a case
overwrote the first and #25's close condition -- every number carries `n` and
its cross-session variance -- was unmeetable from committed artifacts rather
than merely unmet. A run id fixes that, and the four recordings already on disk
predate it: they are schema 1, they are not rewritten, and their run id is a
sentinel that refuses every comparison instead of matching another one.

WHAT IS ACTUALLY AT RISK, and it is why the refusal is structural rather than
polite: the sentence a reader of this benchmark most wants to be true is "these
two runs agree". A run id that could be absent, shared or defaulted answers that
sentence with silence, and silence here reads as agreement.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json

import pytest

from hyperset.evals.recording import (
    GOVERNED_ARM,
    RAW_ARM,
    RECORDING_SCHEMA_VERSION,
    Recording,
    RefusedComparison,
    Unidentified,
    UnreadableRecording,
    describe_run_id,
)
from hyperset.evals.run import (
    UNIDENTIFIED_RUN_STEM,
    case_recordings_dir,
    recording_path,
    recordings_of,
)
from tests.unit.evals.test_pins import pins

SCHEMA_1 = {
    "schema_version": 1,
    "arm": GOVERNED_ARM,
    "case_id": "revenue_by_region",
    "task_version": "revenue@1",
    "git_commit": "0" * 40,
    "recorded_at": "2026-07-28T00:00:00+00:00",
    "pins": pins().to_dict(),
    "source_refs": [],
    "trace": {},
}
"""The shape of the four committed recordings, minus their traces. Hand-built
rather than copied off disk so this file says what schema 1 IS, and so a change
to the corpus cannot quietly change what these tests mean."""


def schema_1_file(tmp_path, name="unidentified.json"):
    path = tmp_path / name
    path.write_text(json.dumps(SCHEMA_1))
    return path


def test_two_reads_of_one_file_mint_run_ids_that_are_not_the_same_object(tmp_path):
    """THE LOAD-BEARING ONE. A cached or memoized read reintroduces the
    singleton, and every other test in this file stays green when it does.

    A module-level sentinel cannot refuse anything: `is` is checked before
    `__eq__` inside containers and dataclass-generated `__eq__`, so one shared
    instance makes `[U] == [U]`, `(U,) == (U,)`, `{'a': U} == {'a': U}`,
    `U in [U]` and `Rec(U) == Rec(U)` all answer True with `__eq__` never
    called. Measured, all five. Distinct instances is what makes the refusal
    reachable at all.
    """
    path = schema_1_file(tmp_path)

    first, second = Recording.read(path), Recording.read(path)

    assert isinstance(first.run_id, Unidentified)
    assert isinstance(second.run_id, Unidentified)
    assert first.run_id is not second.run_id
    with pytest.raises(RefusedComparison):
        _ = first == second


@pytest.mark.parametrize(
    "ask",
    [
        pytest.param(lambda one, two: one == two, id="a == b"),
        pytest.param(lambda one, two: one != two, id="a != b"),
        pytest.param(lambda one, two: [one] == [two], id="[a] == [b]"),
        pytest.param(lambda one, two: (one,) == (two,), id="(a,) == (b,)"),
        pytest.param(lambda one, two: {"k": one} == {"k": two}, id="{'k': a} == {'k': b}"),
        pytest.param(lambda one, two: one in [two], id="a in [b]"),
        pytest.param(lambda one, two: {one, two}, id="{a, b}"),
        pytest.param(lambda one, two: {one: "x"}, id="{a: 'x'}"),
    ],
)
def test_every_shape_that_hides_a_comparison_refuses_too(ask, tmp_path):
    """The short-circuits, each one a way to ask "same run?" without an `==` a
    reader would notice. `in` is a comparison, a set is a comparison, and a dict
    key is a comparison.

    Parametrized rather than looped so a regression names the shapes it let
    through. Measured with the sentinel made a module-level singleton: `a == b`
    and `a != b` still refuse (no identity shortcut on a bare `==`), the set and
    the dict key still refuse (`__hash__` raises before identity is consulted),
    and exactly the four `PyObject_RichCompareBool` shapes -- list, tuple, dict
    value, `in` -- answer True with `__eq__` never called. A loop would report
    the first of those four and hide the other three.
    """
    path = schema_1_file(tmp_path)
    one, two = Recording.read(path).run_id, Recording.read(path).run_id

    with pytest.raises(RefusedComparison):
        ask(one, two)


def test_no_recording_in_this_package_is_copied_or_replaced(tmp_path):
    """The sharing hazard, shown unreachable rather than made to refuse.

    Distinct-per-read closes two READS. It does not close instance SHARING:
    `copy.copy(rec)` is shallow and `dataclasses.replace(rec)` reconstructs
    from the same field values, so both hand the new recording the old one's
    sentinel, the identity shortcut fires and the comparison answers True with
    the refusal never running. Measured, both.

    Neither can be made to refuse from inside the sentinel -- a shallow copy
    never touches the field, and `replace` reads it rather than copying it --
    so what is asserted is that no such call exists over `Recording` anywhere in
    the package. `copy.deepcopy` is the one that is safe on its own: it builds a
    new sentinel, so it already refuses.
    """
    package = case_recordings_dir(GOVERNED_ARM, "x").parent.parent.parent
    sources = sorted(package.glob("*.py"))
    assert sources, "the sweep found no modules, so its zero means nothing"

    hazards = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            if called in ("copy.copy", "dataclasses.replace", "replace"):
                hazards.append(f"{path.name}:{node.lineno}: {called}")
    assert not hazards, (
        "these hand one recording's run id to another recording, which is the one path from "
        f"distinct-per-read back to two runs agreeing: {hazards}"
    )

    # THE BOUND OF THE INSTRUMENT, stated rather than implied: it reads calls
    # in this package's own ast, so it does not see one reached through an
    # alias (`from copy import copy as clone`), one made in a caller outside
    # `hyperset/evals`, or one built at runtime by name. It sees the three
    # spellings that would actually get written.

    # And the measurement the sweep is protecting, stated as a fact about the
    # mechanism rather than as an approval of it.
    shared = Recording.read(schema_1_file(tmp_path))
    assert copy.copy(shared).run_id is shared.run_id
    assert dataclasses.replace(shared).run_id is shared.run_id
    with pytest.raises(RefusedComparison):
        _ = copy.deepcopy(shared) == shared


def test_run_id_is_declared_early_enough_that_the_refusal_is_reached(tmp_path):
    """Field order is load-bearing, and this is what pins it.

    Dataclass `__eq__` compares field tuples and stops at the first unequal
    element. With `run_id` declared LAST, two recordings whose arms differ
    answer False before it is ever consulted -- correct by luck, and the
    refusal never runs. Declared FIRST, the same pair refuses. Measured both
    ways. A later field reorder that pushes `run_id` down disarms the refusal
    silently, and this is the test that notices.
    """
    one = Recording.read(schema_1_file(tmp_path, "a.json"))
    other = dataclasses.replace(Recording.read(schema_1_file(tmp_path, "b.json")), arm=RAW_ARM)

    assert one.arm != other.arm
    assert one.run_id is not other.run_id
    with pytest.raises(RefusedComparison):
        _ = one == other


def test_an_unidentified_run_has_no_serializable_form(tmp_path):
    """`json.dumps` is what enforces "no new write carries the sentinel".

    A rule someone has to remember is a rule that gets forgotten in the commit
    that most needed it; this is the same rule as a property of the serializer.
    The consequence is deliberate: a schema-1 recording read back cannot be
    written out as a schema-2 artifact, because re-recording it is a live run's
    job (hy-zwoj) and not a serializer's.
    """
    recording = Recording.read(schema_1_file(tmp_path))

    with pytest.raises(TypeError):
        json.dumps(recording.to_dict())
    with pytest.raises(TypeError):
        recording.write(tmp_path / "rewritten.json")


def test_the_reader_dispatches_on_version_and_refuses_one_it_has_no_reader_for(tmp_path):
    """A dispatch table, so an unknown version stops at the door rather than
    falling through to the newest reader. The repr is checked here because it
    lands in failure output: `<no run id: schema 1>` cannot be mistaken for an
    identifier, where `<run 0000...>` invites someone to paste it into a
    comparison."""
    unknown = {**SCHEMA_1, "schema_version": RECORDING_SCHEMA_VERSION + 1}

    with pytest.raises(UnreadableRecording) as raised:
        Recording.from_dict(unknown)

    assert "this reader understands 1 and 2" in str(raised.value)
    assert repr(Recording.read(schema_1_file(tmp_path)).run_id) == "<no run id: schema 1>"
    assert describe_run_id(Recording.read(schema_1_file(tmp_path)).run_id) == (
        "<no run id: schema 1>"
    )
    assert describe_run_id("f" * 32) == "f" * 32


def test_a_schema_2_recording_that_names_no_run_is_refused():
    """Absent must not read as matching -- `PinsIncomplete`'s doctrine, which
    is what ruled out an in-band optional run id. A schema-2 producer that
    forgot to write one would otherwise be indistinguishable from the frozen
    corpus that honestly has none."""
    with pytest.raises(UnreadableRecording) as raised:
        Recording.from_dict({**SCHEMA_1, "schema_version": RECORDING_SCHEMA_VERSION})

    assert "carries no run_id" in str(raised.value)


def test_two_runs_of_one_case_coexist_in_the_store(tmp_path):
    """The whole bead in one assertion.

    Before this, `recordings/<arm>/<case>.json` meant the second session of a
    case overwrote the first, so the pair #25 wants compared could not both be
    on disk. Two runs, two files, both enumerated.
    """
    for run_id in ("a" * 32, "b" * 32):
        path = recording_path(GOVERNED_ARM, "revenue_by_region", run_id, directory=tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**SCHEMA_1, "schema_version": 2, "run_id": run_id}))

    found = recordings_of(GOVERNED_ARM, "revenue_by_region", directory=tmp_path)

    assert [path.stem for path in found] == ["a" * 32, "b" * 32]
    assert [Recording.read(path).run_id for path in found] == ["a" * 32, "b" * 32]


def test_the_committed_corpus_is_schema_1_and_unidentified():
    """A true fact about those four files, asserted so that the day one of them
    carries a real run id this reader stops being needed (hy-l13a deletes it)."""
    from hyperset.evals.cases import load_cases

    for arm in (GOVERNED_ARM, RAW_ARM):
        for case in load_cases():
            paths = recordings_of(arm, case.id)
            assert [path.stem for path in paths] == [UNIDENTIFIED_RUN_STEM]
            recording = Recording.read(paths[0])
            assert recording.schema_version == 1
            assert isinstance(recording.run_id, Unidentified)
