"""The resolver contract the MCP setup wizard's test flow must satisfy (hy-xz83).

The wizard bounces a first discover -> resolve. The bug (#409 @ 1e41f75): it built
`{domains:[d], concepts:[]}`, and the resolver REJECTS a domain named without the
concept terms it declares -- so every non-empty discovery failed at resolve. There is
no JS test runner in this repo, so this pins the BACKEND contract the fixed wizard now
targets: a domain named with its concepts resolves to a mappable status, and a domain
named with EMPTY concepts is refused. If that contract ever changes, the wizard's
directive-building must change with it, and this reddens.
"""

from __future__ import annotations

import pytest

from hyperset.transport.operations import INVALID_PARAMS, OperationError, run_operation

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
# The four statuses the wizard maps (MCP_RESOLVE_STATES); a resolve must land in one.
MAPPED_STATUSES = ("governed", "mixed", "observed_only", "no_match")


def _resolve(session_factory, directive):
    return run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": directive},
        session_factory=session_factory,
        principal=None,
    )


def test_a_domain_named_without_concepts_is_refused(session_factory, revenue_slice):
    # The exact failure the wizard bug produced on EVERY non-empty discovery result.
    with pytest.raises(OperationError) as caught:
        _resolve(session_factory, {"domains": ["revenue"], "concepts": []})
    assert caught.value.code == INVALID_PARAMS
    text = f"{caught.value.message} {caught.value.recovery}".lower()
    assert "concept" in text


def test_a_domain_named_with_its_concepts_resolves_to_a_mappable_status(
    session_factory, revenue_slice
):
    # The shape the fixed wizard builds from the discovered concept candidate(s): a
    # domain WITH a concept term it declares. It resolves, and the status is one the
    # wizard classifies (rather than "Unrecognized status").
    bundle = _resolve(session_factory, {"domains": ["revenue"], "concepts": ["recognized_revenue"]})
    assert bundle["resolution"]["status"] in MAPPED_STATUSES
    assert bundle["bundle_id"]
