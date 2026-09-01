"""The explicit-typed config schema and its fail-closed validation (hy-7h00, ADR-0035
section 4). Every scalar reaches here as a STRING (the loader disables implicit YAML
typing), so the schema does ALL coercion -- `no`/`yes` never silently become bool, an
unquoted version never becomes a float, `1:30` never becomes an int. Validation is
CLOSED: an unknown key, a wrong type, a missing required field, or a plaintext value in
a secret-`<ref>` field is a fatal error naming the exact config PATH.

Secret VALUES are NOT resolved here (that is slice 2, hy-ogg5): a `<ref>` field is
validated to HOLD a well-formed `${env:...}`/`${secret:...}` reference and nothing else,
so a secret can never be half-inlined, but the reference is left as-is.
"""

from __future__ import annotations

import re

from hyperset.config.loader import ConfigError

_REF_RE = re.compile(r"^\$\{(env|secret):[A-Za-z_][A-Za-z0-9_]*\}$")
_BOOLS = {"true": True, "false": False}


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class Field:
    def validate(self, value, path: str):
        raise NotImplementedError

    def map_secrets(self, value, fn, path: str = ""):
        """Return a copy of `value` with every SECRET-TYPED (`Ref`) leaf replaced by
        `fn(reference, path)`. The SCHEMA -- not a value string-match -- decides which
        paths are secret (hy-ogg5, mayor ruling hy-c5p7: PATH-ONLY), so both secret
        RESOLUTION and secret REDACTION share this one walk and neither inspects the
        value at a non-secret path. A non-container, non-secret field is returned as-is."""
        return value


class Str(Field):
    def validate(self, value, path):
        if not isinstance(value, str):
            raise ConfigError(f"{path}: expected a string, got {type(value).__name__}")
        return value


class Bool(Field):
    def validate(self, value, path):
        if isinstance(value, str) and value.strip().lower() in _BOOLS:
            return _BOOLS[value.strip().lower()]
        raise ConfigError(f"{path}: expected a boolean 'true' or 'false', got {value!r}")


class Int(Field):
    def __init__(self, *, minimum: int | None = None, maximum: int | None = None):
        self.minimum, self.maximum = minimum, maximum

    def validate(self, value, path):
        if not isinstance(value, str) or not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
            raise ConfigError(f"{path}: expected an integer, got {value!r}")
        number = int(value)
        if self.minimum is not None and number < self.minimum:
            raise ConfigError(f"{path}: {number} is below the minimum {self.minimum}")
        if self.maximum is not None and number > self.maximum:
            raise ConfigError(f"{path}: {number} is above the maximum {self.maximum}")
        return number


class Enum(Field):
    def __init__(self, choices: tuple[str, ...]):
        self.choices = choices

    def validate(self, value, path):
        if value in self.choices:
            return value
        raise ConfigError(f"{path}: expected one of {list(self.choices)}, got {value!r}")


class Ref(Field):
    """A value that MUST be a single secret reference `${env:NAME}` or `${secret:NAME}`
    and nothing else -- a plaintext secret, or a string that merely CONTAINS a reference,
    is a validation error, so a secret can never be half-inlined."""

    def validate(self, value, path):
        if isinstance(value, str) and _REF_RE.fullmatch(value):
            return value
        raise ConfigError(
            f"{path}: a secret field must hold exactly one ${{env:NAME}} or ${{secret:NAME}} "
            "reference, never a plaintext value"
        )

    def map_secrets(self, value, fn, path=""):
        # THE secret-typed leaf: hand it to `fn` (a resolver or the redactor).
        return fn(value, path)


class Raw(Field):
    """Pass-through for an opaque/extensible value (e.g. a playground agents blob or an
    integrations entry). Not coerced, but its presence and JSON-ness are the wiring
    slice's concern; here it is accepted as-is."""

    def validate(self, value, path):
        return value


class ListOf(Field):
    def __init__(self, item: Field):
        self.item = item

    def validate(self, value, path):
        if not isinstance(value, list):
            raise ConfigError(f"{path}: expected a list, got {type(value).__name__}")
        return [self.item.validate(entry, f"{path}[{index}]") for index, entry in enumerate(value)]

    def map_secrets(self, value, fn, path=""):
        if not isinstance(value, list):
            return value
        return [
            self.item.map_secrets(item, fn, f"{path}[{index}]") for index, item in enumerate(value)
        ]


class Section(Field):
    """A CLOSED mapping: only the declared fields are allowed (an unknown key is fatal),
    and every name in `required` must be present."""

    def __init__(self, fields: dict[str, Field], *, required: frozenset[str] = frozenset()):
        self.fields = fields
        self.required = required

    def validate(self, value, path):
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
        unknown = sorted(set(value) - set(self.fields))
        if unknown:
            raise ConfigError(f"{path or '<root>'}: unknown config key(s): {', '.join(unknown)}")
        missing = sorted(name for name in self.required if value.get(name) is None)
        if missing:
            raise ConfigError(f"{path or '<root>'}: missing required key(s): {', '.join(missing)}")
        result: dict = {}
        for name, field in self.fields.items():
            if name in value and value[name] is not None:
                result[name] = field.validate(value[name], _join(path, name))
        return result

    def map_secrets(self, value, fn, path=""):
        if not isinstance(value, dict):
            return value
        return {
            key: (
                self.fields[key].map_secrets(inner, fn, _join(path, key))
                if key in self.fields
                else inner
            )
            for key, inner in value.items()
        }


class OpenMap(Field):
    """A mapping with arbitrary keys (e.g. `integrations`), each value of one type."""

    def __init__(self, value_field: Field):
        self.value_field = value_field

    def validate(self, value, path):
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
        return {
            key: self.value_field.validate(entry, _join(path, str(key)))
            for key, entry in value.items()
        }

    def map_secrets(self, value, fn, path=""):
        if not isinstance(value, dict):
            return value
        return {
            key: self.value_field.map_secrets(inner, fn, _join(path, str(key)))
            for key, inner in value.items()
        }


_OIDC = Section(
    {"issuer": Str(), "audience": Str(), "jwks_url": Str(), "roles_claim": Str()},
)

SCHEMA = Section(
    {
        "server": Section(
            {
                "bind": Enum(("loopback", "all")),
                "api_port": Int(minimum=1, maximum=65535),
                "mcp_http_port": Int(minimum=1, maximum=65535),
                "ui_bind": Enum(("loopback",)),  # loopback-only (proposal section 5)
                "ui_port": Int(minimum=1, maximum=65535),
                "log_level": Str(),
            },
            required=frozenset({"bind"}),
        ),
        "auth": Section({"enabled": Bool(), "oidc": _OIDC}),
        "models": Section(
            # `provider` is the model-provider SELECTION (hy-7m5yg): the legacy
            # HYPERSET_MODEL_PROVIDER's config home. Str, not Enum, so it accepts exactly what
            # the old presence read accepted (no new validation, behaviour-preserving).
            {
                "provider": Str(),
                "planner": Str(),
                "embedding": Str(),
                "generator": Str(),
                "judge": Str(),
            }
        ),
        "providers": Section(
            {
                "ollama": Section(
                    {"base_url": Str(), "model": Str(), "max_tokens": Int(minimum=1)}
                ),
                "openai": Section(
                    {
                        "base_url": Str(),
                        "model": Str(),
                        "api_key": Ref(),
                        "reasoning_effort": Str(),
                        "max_completion_tokens": Int(minimum=1),
                    }
                ),
                "embedding": Section(
                    {
                        "provider": Str(),
                        "base_url": Str(),
                        "model": Str(),
                        "api_key": Ref(),
                        # The embedding width the adapter asks the provider for and pins the
                        # space under. Int(minimum=1) -- a brand-new var (no prior env read), so
                        # a bad value is a loud startup error, not a silent fallback. OpenAI's
                        # text-embedding-3-* honor a `dimensions` request (768 matches the local
                        # index); the served adapter requires this value explicitly.
                        "dimensions": Int(minimum=1),
                    }
                ),
            }
        ),
        "connections": Section(
            {
                "superset": Section(
                    {"base_url": Str(), "version": Str(), "username": Ref(), "password": Ref()}
                ),
                "datahub": Section({"base_url": Str(), "version": Str(), "token": Ref()}),
                "analytics_db": Section({"url": Ref()}),
                "system_db": Section({"url": Ref()}),
            }
        ),
        "playground": Section(
            {
                "enabled": Bool(),
                "agents": Raw(),
                "models": Raw(),
                "default_agent": Str(),
                "default_model": Str(),
                "upstream_base_url": Str(),
                "writeback": Section({"repo": Str(), "token": Ref()}),
            }
        ),
        "features": Section(
            {
                "pii_guard": Bool(),
                "discover": Bool(),
                "expand": Bool(),
                "review": Bool(),
                "pii": Section({"action": Str(), "entities": Str(), "spacy_model": Str()}),
            }
        ),
        # `max_files` is Int(minimum=1), IDENTICAL to the legacy `_max_files()` bound
        # (context/git.py: HYPERSET_CONTEXT_MAX_FILES must be >= 1). A `0` is rejected on the
        # config path exactly as the env path rejected it, so wiring the read through settings
        # is behaviour-preserving -- a cap of 0 stays a loud error, never a silent zero cap
        # (hy-tc4o critic; hy-gh-288's named intent).
        "context": Section({"max_files": Int(minimum=1), "cache_dir": Str()}),
        "integrations": OpenMap(Raw()),
    },
    required=frozenset({"server"}),
)


def validate(merged: dict) -> dict:
    """The merged pre-resolution config validated against `SCHEMA`, coerced to typed
    values, or a fatal `ConfigError` naming the config PATH. Secret refs are validated
    for shape but NOT resolved (slice 2)."""
    return SCHEMA.validate(merged, "")
