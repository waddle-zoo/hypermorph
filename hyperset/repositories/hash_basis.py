"""How a stored `content_hash` was derived from a stored `raw_payload`.

A connector may not hash everything it observed: Superset's REST bodies
carry `*_humanized` relative times ("now", "10 minutes ago") and DataHub
re-serializes `customProperties` in a different order after an identical
rewrite, so hashing the served payload whole would report a new immutable
version on every resync of an unchanged asset.

Narrowing the hash input that way used to make `content_hash` unverifiable:
the connector applied a projection in memory, the stored row kept only the
whole payload, and nothing recorded which of the two the hash covered
(hy-y8g finding 2). So the projection is not passed around as data here --
it is a *declared rule*, stored on the version row it produced, and applied
by the repository. Every stored hash is therefore recomputable from stored
state alone:

    content_hash == sha256(canonical(apply_hash_basis(raw_payload, hash_basis)))

Kept deliberately small: three rules, all of them observed to be needed by a
real pinned source, and an unknown rule is rejected rather than ignored --
a basis a later reader cannot replay is the failure this module exists to
prevent.
"""

from __future__ import annotations

from typing import Any

DROP_KEYS = "drop_keys"
"""Exact key names to exclude, at any depth."""

DROP_KEY_SUFFIXES = "drop_key_suffixes"
"""Key-name suffixes to exclude, at any depth."""

SORT_LIST_KEYS_BY = "sort_list_keys_by"
"""`{key: entry_field}` -- list values under `key` are sorted by each
entry's `entry_field`, for upstream maps whose serialized order is not
stable. Only for keys observed to reorder: sorting a list the source orders
meaningfully (a schema's columns) would hide a real change."""

RULES = (DROP_KEYS, DROP_KEY_SUFFIXES, SORT_LIST_KEYS_BY)

EMPTY: dict = {}
"""No narrowing -- the hash covers the payload exactly as observed."""


def validate_hash_basis(basis: dict) -> dict:
    """Return `basis` if every rule in it is one this module can replay."""
    unknown = sorted(set(basis) - set(RULES))
    if unknown:
        raise ValueError(f"unknown hash basis rule(s): {', '.join(unknown)}")
    return basis


def apply_hash_basis(payload: Any, basis: dict) -> Any:
    """`payload` narrowed to what a hash computed under `basis` covers.

    Pure and deterministic: the same payload and basis always yield the same
    projection, which is what makes a stored `content_hash` re-verifiable.
    """
    validate_hash_basis(basis)
    drop_keys = frozenset(basis.get(DROP_KEYS, ()))
    drop_suffixes = tuple(basis.get(DROP_KEY_SUFFIXES, ()))
    sort_by = dict(basis.get(SORT_LIST_KEYS_BY, {}))
    return _project(payload, drop_keys, drop_suffixes, sort_by)


def _project(value: Any, drop_keys: frozenset[str], drop_suffixes: tuple, sort_by: dict) -> Any:
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if key in drop_keys or (drop_suffixes and key.endswith(drop_suffixes)):
                continue
            item = _project(item, drop_keys, drop_suffixes, sort_by)
            if key in sort_by and isinstance(item, list):
                item = sorted(item, key=lambda entry: _sort_key(entry, sort_by[key]))
            projected[key] = item
        return projected
    if isinstance(value, list):
        return [_project(item, drop_keys, drop_suffixes, sort_by) for item in value]
    return value


def _sort_key(entry: Any, field: str) -> str:
    """A total order over serialized map entries. An entry that is not a dict
    or lacks `field` sorts first rather than raising: the basis describes what
    the source usually serves, and one odd entry must not fail a sync."""
    return str(entry.get(field, "")) if isinstance(entry, dict) else ""
