"""The hardened YAML loader for layered deployment configuration (hy-7h00, ADR-0035).

Bare `yaml.safe_load` gives NONE of the guarantees this config model needs, and
post-processing a parsed tree cannot recover them: by the time `safe_load` returns,
duplicate keys are already collapsed to the last, anchor/alias structure is already
expanded, and implicit scalar types (`no`->bool, `1:30`->int, unquoted versions) are
already applied. So parsing is an EXPLICIT `SafeLoader`-derived loader with three
additions, all before any schema coercion (config-layering-proposal.md section 3):

1. Duplicate mapping keys are REJECTED (stock PyYAML silently keeps the last).
2. Anchors, aliases, and non-core/custom tags are REJECTED (an alias adds a merge
   path the precedence model does not account for; a custom tag is arbitrary
   construction).
3. Implicit scalar typing is DISABLED -- every plain scalar is a STRING (only an
   explicit YAML `null`/`~` stays null, so an overlay can UNSET a base key). The
   typed schema, not YAML, does every coercion.

Never `yaml.load` with an unsafe loader, never a full loader (no arbitrary object
construction), never bare `safe_load`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_STR_TAG = "tag:yaml.org,2002:str"
_NULL_TAG = "tag:yaml.org,2002:null"
_MAP_TAG = "tag:yaml.org,2002:map"
_SEQ_TAG = "tag:yaml.org,2002:seq"


class ConfigError(RuntimeError):
    """A fatal configuration error: a parse-trust violation, a merge problem, a
    schema failure, or a missing overlay. Always fail-closed -- there is no fallback
    to demo defaults. The message names the config PATH, never a secret value."""


class HypersetConfigLoader(yaml.SafeLoader):
    """`SafeLoader` (core tags only) with the three additions above."""

    def construct_mapping(self, node, deep=False):
        # REJECT duplicate keys rather than silently keeping the last.
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ConfigError(f"duplicate key {key!r} in a config mapping")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping

    def compose_node(self, parent, index):
        # REJECT aliases (`*a`) and anchors (`&a`): both are events carrying an
        # `anchor`, and an alias is its own event type checked first.
        if self.check_event(yaml.events.AliasEvent):
            raise ConfigError("YAML aliases (`*a`) are not allowed in config")
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            raise ConfigError("YAML anchors (`&a`) are not allowed in config")
        return super().compose_node(parent, index)


# A CLOSED constructor table: plain scalars become strings (the default scalar tag is
# str once implicit resolvers are cleared), an explicit `null` stays null, and mappings
# / sequences use the strict constructors above. Every OTHER tag -- `!!int`, `!!bool`,
# `!!float`, `!!timestamp`, `!!python/...`, `!custom` -- has no constructor and raises,
# so no explicit tag can smuggle a coerced type or an arbitrary object past the schema.
def _construct_str(loader, node):
    return loader.construct_scalar(node)


def _reject_tag(loader, node):
    raise ConfigError(
        f"unsupported YAML tag {node.tag!r} in config "
        "(only plain scalars, null, maps, and lists are allowed)"
    )


HypersetConfigLoader.yaml_constructors = {
    _STR_TAG: _construct_str,
    _NULL_TAG: yaml.SafeLoader.yaml_constructors[_NULL_TAG],
    _MAP_TAG: yaml.SafeLoader.yaml_constructors[_MAP_TAG],
    _SEQ_TAG: yaml.SafeLoader.yaml_constructors[_SEQ_TAG],
    None: _reject_tag,
}
HypersetConfigLoader.yaml_multi_constructors = {}

# Implicit resolvers decide the tag of a PLAIN (unquoted) scalar. Cleared to just the
# `null` resolver: a plain `null`/`~`/empty stays null (so an overlay can unset a base
# key), and every other plain scalar falls through to the default str tag -- so
# `no`->str, `8080`->str, `1:30`->str. The typed schema does all coercion.
HypersetConfigLoader.yaml_implicit_resolvers = {}
_NULL_RESOLVER = re.compile(r"^(?:~|null|Null|NULL|)$")
for _ch in ["~", "n", "N", ""]:
    HypersetConfigLoader.add_implicit_resolver(_NULL_TAG, _NULL_RESOLVER, [_ch])


def load_config_mapping(text: str, *, source: str) -> dict:
    """Parse ONE config document to a mapping of plain-string scalars, fail-closed.

    A document that is empty, or whose top level is not a mapping, is refused -- a
    config file is a set of sections, never a bare scalar or list. `source` names the
    file in any error.
    """
    try:
        data = yaml.load(text, Loader=HypersetConfigLoader)  # noqa: S506 -- hardened loader, not the unsafe one
    except ConfigError:
        raise
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: invalid YAML: {exc}") from exc
    if data is None:
        raise ConfigError(f"{source}: config file is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{source}: top level must be a mapping of sections, got {type(data).__name__}"
        )
    return data


def read_config_file(path: Path) -> dict:
    """Read and parse a config file, or FAIL CLOSED. A named overlay that is missing or
    unreadable is fatal, never skipped (config-layering-proposal.md section 3)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"config file not found: {path} (a named overlay is never skipped)"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"config file unreadable: {path}: {exc}") from exc
    return load_config_mapping(text, source=str(path))
