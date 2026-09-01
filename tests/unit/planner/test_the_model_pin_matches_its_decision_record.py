"""`PINNED_MODEL` against the two places that repeat it (hy-yx7y).

The pin is a product decision, so it is written down in prose as well as in
code: ADR 0013 states it as the decision, and `runtime.py` carries a Modelfile
someone will paste to build the tag the benchmark runs against. Three copies
of one tag drift silently, and the failure is quiet in the worst way -- an ADR
that names the model we no longer run reads exactly like an ADR that is right,
and a Modelfile that builds the wrong base tag produces a benchmark whose pins
look asserted and are not.

Prose against code, so this cannot be a `scripts/check_docs.py` section: that
script imports nothing from `hyperset` by design, which is the same reason
`tests/unit/test_section_7_matches_the_served_contract.py` exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from hyperset.planner.runtime import PINNED_MODEL

ROOT = Path(__file__).resolve().parents[3]
ADR = ROOT / "docs" / "adr" / "0013-split-benchmark-gate.md"
RUNTIME = ROOT / "hyperset" / "planner" / "runtime.py"


def test_adr_0013_states_the_model_that_is_pinned():
    """The decision record names the tag, not a family, and names this one."""
    stated = re.findall(r"The pinned model is `([^`]+)`", ADR.read_text())

    assert stated == [PINNED_MODEL]


def test_the_modelfile_builds_the_pinned_tag():
    """The window is baked into a served tag because Ollama's OpenAI-compatible
    endpoint ignores a requested one, so the snippet's `FROM` is the model the
    benchmark actually loads."""
    bases = re.findall(r"^\s*FROM (\S+)$", RUNTIME.read_text(), re.MULTILINE)

    assert bases == [PINNED_MODEL]
