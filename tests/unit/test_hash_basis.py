"""What a stored `content_hash` covers must be replayable (hy-y8g finding 2).

The connector-specific rules are asserted against real pinned payloads in
`tests/unit/connectors`; this module covers the replay contract itself.
"""

from __future__ import annotations

import pytest

from hyperset.repositories.hash_basis import EMPTY, apply_hash_basis, validate_hash_basis

_PAYLOAD = {
    "uuid": "u",
    "changed_on_humanized": "now",
    "metrics": [{"metric_name": "revenue", "changed_on_humanized": "10 minutes ago"}],
    "props": [{"key": "b", "value": "2"}, {"key": "a", "value": "1"}],
}


def test_an_empty_basis_leaves_the_payload_exactly_as_observed():
    assert apply_hash_basis(_PAYLOAD, EMPTY) == _PAYLOAD


def test_a_rule_this_module_cannot_replay_is_rejected_not_ignored():
    # Silently ignoring it would store a basis that claims a narrowing the
    # replay never applies -- the unverifiable hash this column exists to end.
    with pytest.raises(ValueError, match="unknown hash basis rule"):
        apply_hash_basis(_PAYLOAD, {"drop_paths": ["metrics"]})

    with pytest.raises(ValueError, match="drop_keys_maybe, sort_keys"):
        validate_hash_basis({"sort_keys": [], "drop_keys_maybe": []})


def test_every_rule_narrows_at_any_depth_and_leaves_the_rest_untouched():
    projected = apply_hash_basis(
        _PAYLOAD,
        {
            "drop_key_suffixes": ["_humanized"],
            "drop_keys": ["value"],
            "sort_list_keys_by": {"props": "key"},
        },
    )

    assert projected == {
        "uuid": "u",
        "metrics": [{"metric_name": "revenue"}],
        "props": [{"key": "a"}, {"key": "b"}],
    }
    assert _PAYLOAD["props"][0]["key"] == "b"  # the input is not mutated


def test_sorting_tolerates_an_entry_the_declared_field_does_not_describe():
    # A basis describes what the source usually serves; one odd entry must
    # order deterministically rather than fail a whole sync.
    served = {"props": ["scalar", {"key": "a"}, {}]}

    assert apply_hash_basis(served, {"sort_list_keys_by": {"props": "key"}}) == {
        "props": ["scalar", {}, {"key": "a"}]
    }
