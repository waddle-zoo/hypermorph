"""Keep the ADR index complete so design decisions are discoverable."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
INDEX = (ADR_DIR / "README.md").read_text()


def test_every_numbered_adr_is_linked_in_the_complete_index():
    files = sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
    expected = {path.name for path in files}
    linked = set(re.findall(r"\]\((\d{4}-[^)]+\.md)\)", INDEX))

    assert linked == expected
    assert len(files) >= 37


def test_current_shell_adr_has_the_required_decision_boundaries():
    adr = (ADR_DIR / "0038-playground-review-settings-navigation.md").read_text()
    for phrase in (
        "Review and Settings are visible",
        "one compact Playground dropdown",
        "URL-addressable view",
        "Serve product documentation locally",
        "Keep chat answer-first",
        "knowledge graph",
    ):
        assert phrase in adr
