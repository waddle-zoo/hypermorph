"""`docs/v0-foundation.md` section 8's manifest register against the schema.

The pack manifest is a customer-owned Git contract, so its field list is a
customer-facing surface: a pack author writes YAML against what this document
says and finds out at sync time whether Hyperset agrees. Until hy-5dsm the
register existed only as `SUPPORTED_MANIFEST_FIELDS`, which `git grep` found
in three places, all inside `schema.py` itself -- nothing in `tests/`, `docs/`
or `scripts/` named it, and section 8 showed three filenames without naming a
single field.

This is `test_section_7_matches_the_served_contract.py`'s guard one surface
over, and it exists for the reason that file gives: a document listing a
vocabulary drifts from the code the moment the vocabulary moves, and an
undocumented constant is not a contract a customer can write against. Item 1
of hy-5dsm is accurate the day it is written; this is what keeps it accurate.

Not `scripts/check_docs.py`, for that file's stated reason: it deliberately
imports nothing from `hyperset`, so it cannot detect a document that
contradicts the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from hyperset.context.schema import (
    SUPPORTED_MANIFEST_FIELDS,
    parse_context,
    to_manifest_document,
)

SECTION = "### Context format"
NEXT_SECTION = "### Domain graph"
DOC = Path(__file__).resolve().parents[2] / "docs" / "v0-foundation.md"
# The all-fields EXEMPLAR, not the benchmark `revenue` fixture: `revenue` is a
# realistic ROOT domain and legitimately omits the optional `parent` (ADR-0031), so it
# cannot exercise every field. `nested` declares a `parent` and every other supported
# field, so the round-trip projection is proven over the FULL register.
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "git_context" / "nested"


def _section() -> str:
    text = DOC.read_text()
    start = text.index(SECTION)
    return text[start : text.index(NEXT_SECTION, start)]


def _documented_fields(section: str) -> list[str]:
    """The register's first column, from the table that follows its heading.

    Parsed from a delimited home rather than by collecting every backticked
    token, because the section legitimately backticks `1`, `ref`, `role`,
    `term`, `ObservedAsset` and the entry shapes. Deliberately NOT intersected
    with `SUPPORTED_MANIFEST_FIELDS`: that discards exactly the rows that
    would have failed the comparison, so a fabricated field passes -- the
    collapse `test_a_code_the_contract_does_not_have_fails_the_vocabulary_check`
    was written to pin.

    A list rather than a set, because a field documented twice with two
    different descriptions is a defect and a set hides it.
    """
    heading = section.index("supports exactly these fields")
    table = section[heading:]
    return re.findall(r"^\| `([a-z_]+)` \|", table, re.M)


def test_section_8_names_exactly_the_manifest_fields_the_schema_accepts():
    """Equality, both directions. A supported field the document omits is one
    a pack author never learns they may write. A field the document invents is
    one `_load_manifest` rejects as unsupported -- the document would be
    telling a customer to write YAML that fails their own sync."""
    documented = _documented_fields(_section())

    assert documented == sorted(documented, key=SUPPORTED_MANIFEST_FIELDS.index), (
        "the register should read in schema order"
    )
    assert set(documented) == set(SUPPORTED_MANIFEST_FIELDS)
    assert len(documented) == len(SUPPORTED_MANIFEST_FIELDS)


def test_a_field_the_schema_does_not_have_fails_the_check():
    """The guard's own guard. An invented row must move the documented set
    rather than being intersected away."""
    invented = _section().replace(
        "| `caveats` |",
        "| `approval_state` | Invented. |\n| `caveats` |",
        1,
    )
    documented = _documented_fields(invented)

    assert "approval_state" in documented
    assert set(documented) != set(SUPPORTED_MANIFEST_FIELDS)


def test_a_field_the_document_omits_fails_the_check():
    """The other direction, which is the one that actually rots: a field added
    to the schema and not to the register reads to a pack author as
    unsupported."""
    dropped = _section().replace("| `grain` | The grain", "| ~~grain~~ | The grain", 1)
    documented = _documented_fields(dropped)

    assert "grain" not in documented
    assert set(documented) != set(SUPPORTED_MANIFEST_FIELDS)


def test_the_round_trip_projection_carries_every_documented_field():
    """The third copy of the register, and the reason the round-trip test in
    `tests/unit/context/test_context_schema.py` means anything.

    `to_manifest_document` rebuilds the manifest from the normalized form and
    drops empty values, so its key set proves two things at once: that the
    fixture exercises every supported field -- without which
    `test_normalized_form_round_trips_back_to_the_committed_manifest` compares
    a manifest that never had the field to a rebuild that never emits it --
    and that the projection has not fallen behind a field added to the schema.
    """
    files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(FIXTURE_DIR.iterdir())
        if path.is_file()
    }
    rebuilt = to_manifest_document(parse_context(files).normalized)

    assert set(rebuilt) == set(SUPPORTED_MANIFEST_FIELDS)
    assert set(yaml.safe_load(files["manifest.yaml"])) == set(SUPPORTED_MANIFEST_FIELDS)
