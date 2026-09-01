"""Deterministic current-vs-proposed diff of a governed-context definition (hy-z6zv).

A review task carries a PROPOSED definition (the UNAPPROVED draft). At task detail a
reviewer needs to see it BESIDE the governed CURRENT meaning and the EXACT diff between
them -- today that diff only materialises inside the PR the proposal opens (the git writer
lets GitHub render current-manifest vs merged-manifest). This computes the same
section-level delta IN PROCESS, by the SAME entry identity the PR merge deduplicates on, so
what a reviewer sees at detail is the change the PR will carry.

PURE: dict-vs-dict only. It reads nothing external, runs no SQL, and opens no git -- so it
is safe to import from `hyperset.transport.operations`, which is pinned free of subprocess.
"""

from __future__ import annotations

from collections.abc import Callable

# The list sections a definition may carry, each paired with the key an entry is identified
# by. This MIRRORS `hyperset.flywheel.git_pr._MERGE_KEYS` (the add-only merge the proposal PR
# performs): an entry the PR merge would treat as "already present" is not reported as added
# here either. Kept in sync by test_meaning_diff.py, which asserts the section+primary-key
# identities agree with git_pr rather than trusting two hand-maintained copies.
MERGE_KEYS: dict[str, Callable[[dict], object]] = {
    "definitions": lambda entry: entry.get("term"),
    "approved_sources": lambda entry: entry.get("ref"),
    "prohibited_sources": lambda entry: entry.get("ref"),
    "fields": lambda entry: entry.get("name"),
    "joins": lambda entry: (entry.get("from"), entry.get("to")),
    "filters": lambda entry: _hashable(entry),
    "checks": lambda entry: _hashable(entry),
    "caveats": lambda entry: _hashable(entry),
}

# The scalar (non-list) definition keys a proposal may set; a change to one is a
# before/after on that key.
_SCALAR_KEYS = ("grain",)


def _hashable(value):
    """A stable, hashable, order-independent form of an entry, so entries that carry the
    same meaning compare equal regardless of dict key order."""
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(inner)) for key, inner in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value


def _identity(section: str, entry: object):
    identify = MERGE_KEYS[section]
    if not isinstance(entry, dict):
        # A section whose entries are not mappings (e.g. a bare filter string) is identified
        # by its own hashable form.
        return _hashable(entry)
    return identify(entry)


def _section_entries(definition: dict, section: str) -> list:
    entries = definition.get(section)
    return list(entries) if isinstance(entries, list) else []


def diff_definition(current: dict | None, proposed: dict | None) -> dict:
    """The section-level delta from `current` to `proposed`, by MERGE_KEYS identity.

    Returns `{"sections": {section: {"added": [...], "changed": [...], "removed": [...]}}, ...}`
    carrying ONLY the sections and scalar keys that actually changed, each `changed` entry a
    `{"identity", "before", "after"}` record -- so an add-only proposal shows mostly `added`
    and an unchanged proposal returns `{"sections": {}}`. Deterministic: sections in
    MERGE_KEYS order, entries ordered by their identity's string form.
    """
    current = current or {}
    proposed = proposed or {}
    sections: dict[str, dict] = {}
    for section in MERGE_KEYS:
        before = {_identity(section, entry): entry for entry in _section_entries(current, section)}
        after = {_identity(section, entry): entry for entry in _section_entries(proposed, section)}
        added = [after[key] for key in _ordered(after.keys() - before.keys())]
        removed = [before[key] for key in _ordered(before.keys() - after.keys())]
        changed = [
            {"identity": _label(key), "before": before[key], "after": after[key]}
            for key in _ordered(before.keys() & after.keys())
            if before[key] != after[key]
        ]
        if added or removed or changed:
            sections[section] = {"added": added, "changed": changed, "removed": removed}

    diff: dict = {"sections": sections}
    for key in _SCALAR_KEYS:
        if current.get(key) != proposed.get(key):
            diff[key] = {"before": current.get(key), "after": proposed.get(key)}
    return diff


def merge_definitions(definitions: list[dict]) -> dict:
    """Union several governed definitions into one manifest-shaped definition, by MERGE_KEYS
    identity (last wins on a duplicate id), so a domain's whole current governed meaning --
    spread across several governed-context rows -- can be shown and diffed as one. A scalar
    key takes the first non-null. Deterministic: sections in MERGE_KEYS order, entries in
    first-seen order per section."""
    merged: dict = {}
    for section in MERGE_KEYS:
        seen: dict = {}
        for definition in definitions:
            for entry in _section_entries(definition or {}, section):
                seen[_identity(section, entry)] = entry
        if seen:
            merged[section] = list(seen.values())
    for key in _SCALAR_KEYS:
        for definition in definitions:
            if (definition or {}).get(key) is not None:
                merged[key] = definition[key]
                break
    return merged


def _ordered(keys) -> list:
    return sorted(keys, key=_label)


def _label(key) -> str:
    return str(key)
