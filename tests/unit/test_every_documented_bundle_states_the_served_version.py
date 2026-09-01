"""The `schema_version` inside `docs/v0-foundation.md`'s bundle examples,
against the version the server actually serves.

Section 6 opens with the canonical `ContextBundle`, and its first line is a
`schema_version`. Nothing compared that line to the constant. The section 7
guards cannot: both are scoped by `_section_7()` and this is section 6.
`scripts/check_docs.py` cannot either -- it compares phrases to phrases and
imports nothing from `hyperset` on purpose -- and its own closing line points
at the section 7 test as the thing that catches a document contradicting the
code. So a bump could move the constant, move section 7's stated number, pass
31 doc-facing unit tests and the docs gate, and leave the example every reader
copies from stating the previous version (hy-gh-124 slice 2).

Scoped by SHAPE rather than by section, because a per-section guard is what
left the hole: a bundle example added to a section nobody thought to check
would be invisible again. ADR 0018 is the line -- the number versions the
ANSWER, not the request -- so what this reads is every fenced block that shows
an answer, and a block carrying a `bundle_id` is an answer. A request example
stating `schema_version: 1` is correct and permanent under that ADR, and this
guard is silent about it rather than reddening on legitimate work.
"""

from __future__ import annotations

import re
from pathlib import Path

from hyperset.bundle import SCHEMA_VERSION

DOC = Path(__file__).resolve().parents[2] / "docs" / "v0-foundation.md"


def _served_examples(doc: str) -> list[str]:
    """Every fenced block that shows a bundle the server hands back."""
    blocks = re.findall(r"```[a-z]*\n(.*?)```", doc, re.S)
    return [block for block in blocks if re.search(r"[\"']?bundle_id[\"']?:", block)]


def _stated_versions(doc: str) -> list[int | None]:
    """One entry per served block, `None` where that block states no version.

    A served example omitting `schema_version` used to be dropped from this
    list, which made the guard below compare the versions it happened to find
    and call that every example. Dropping the block is the same defect as
    finding no blocks at all, one level down, so the entry stays and carries
    the omission.
    """
    versions: list[int | None] = []
    for block in _served_examples(doc):
        stated = re.search(r"^\s*[\"']?schema_version[\"']?: (\d+)", block, re.M)
        versions.append(int(stated.group(1)) if stated else None)
    return versions


def test_every_bundle_example_states_the_version_the_server_serves():
    stated = _stated_versions(DOC.read_text())
    # Not vacuous: the canonical example is one of these, and a guard that
    # passes because it found nothing to check is the failure being fixed.
    assert stated, "no bundle example in the document states a schema_version"
    # Nor vacuous per block: a `None` is a served example stating no version,
    # which is precisely what this test's name says cannot exist.
    assert None not in stated, (
        f"{stated.count(None)} of {len(stated)} bundle examples state no schema_version"
    )
    assert set(stated) == {SCHEMA_VERSION}


def test_a_served_example_that_states_no_version_is_not_skipped():
    """Critic's reproduction, kept as an arm. A second answer block carrying a
    `bundle_id` and no `schema_version` used to leave the list as `[6]` -- two
    served examples, one version, guard green -- so the test above claimed a
    coverage it did not have."""
    doc = DOC.read_text()
    undocumented = "```yaml\nbundle_id: cb-undocumented\ndomain_graph: {}\n```\n"

    stated = _stated_versions(doc + undocumented)

    assert len(stated) == len(_served_examples(doc)) + 1
    assert None in stated


def test_a_stale_example_fails_the_check():
    doc = DOC.read_text()
    stale = doc.replace(
        f"schema_version: {SCHEMA_VERSION}\nbundle_id:", "schema_version: 0\nbundle_id:", 1
    )
    assert stale != doc, "the anchor this negative injects at is gone"
    assert set(_stated_versions(stale)) != {SCHEMA_VERSION}


def test_a_request_example_is_not_read_as_an_answer():
    """The line ADR 0018 draws, asserted rather than assumed: a plan request
    pins `schema_version` to 1 forever, and a guard that read it would red on
    a correct document the first time somebody adds that example."""
    request = (
        "```yaml\nschema_version: 1\nquery: revenue by region\n"
        "directive:\n  domains: [revenue]\n```\n"
    )
    assert _stated_versions(request) == []
    assert set(_stated_versions(DOC.read_text() + request)) == {SCHEMA_VERSION}
