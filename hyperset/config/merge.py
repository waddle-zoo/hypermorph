"""Deterministic deep-merge for the config layers (hy-7h00, ADR-0035 section 3).

Mappings deep-merge; a scalar or a LIST in an overlay REPLACES the base value
wholesale (lists are never concatenated, so the effective value is predictable); an
explicit `null` in an overlay UNSETS the base key. Precedence is strictly source
order: base first, then each `HYPERSET_CONFIG` overlay over the previous.
"""

from __future__ import annotations

import copy


def deep_merge(base: dict, overlay: dict) -> dict:
    """`overlay` layered over `base`, per the semantics above. Neither input is
    mutated; replaced containers are deep-copied so no layer aliases another."""
    result = dict(base)
    for key, value in overlay.items():
        if value is None:
            # Explicit null UNSETS a base key (so a customer can remove a default).
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            # A scalar or list (or a dict replacing a non-dict) REPLACES wholesale.
            result[key] = copy.deepcopy(value)
    return result
