"""Secret-reference resolution and PATH-ONLY redaction (hy-ogg5, config slice 2, ADR-0035
decision 3; mayor ruling hy-c5p7).

Slice 1 validated that every secret-typed (`Ref`) field holds exactly ONE well-formed
reference and never a plaintext value. This slice resolves those references, AFTER the
merge, into write-only in-memory settings, and redacts them so a resolved value can never
be serialized, logged, echoed, or interpolated into an error.

Two DISTINCT, non-interchangeable reference forms:

    ${env:NAME}     reads the environment variable NAME.
    ${secret:NAME}  reads a pluggable secret PROVIDER; the default reads the file NAME
                    under the mounted secrets dir HYPERSET_SECRETS_DIR (default
                    /run/secrets). An ${env:...} ref NEVER falls back to the provider, and
                    a ${secret:...} ref NEVER falls back to the environment.

An unresolved reference is FATAL (`ConfigError`) -- there is no plaintext fallback. The
error names the config PATH and the reference (a variable/secret NAME is not itself a
secret value); the RESOLVED VALUE is never interpolated anywhere.

Redaction is PATH-ONLY: the schema knows which paths are secret-typed (`Ref`), so
`redact_settings` replaces exactly those leaves with `REDACTED` and never string-matches a
value. `resolve_secrets` and `redact_settings` share the one schema walk (`map_secrets`).

At rest, a resolved value is held ENCRYPTED (`security.secret_box`, reused) and revealed
only through `Secret.reveal()`; `repr`/`str` render the reference, never the value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from hyperset.config.loader import ConfigError
from hyperset.config.schema import SCHEMA
from hyperset.security.secret_box import decrypt, encrypt

REDACTED = "***"
SECRETS_DIR_ENV = "HYPERSET_SECRETS_DIR"
_DEFAULT_SECRETS_DIR = "/run/secrets"
# kind + NAME (the schema already guaranteed the overall shape; this splits it).
_REF_RE = re.compile(r"^\$\{(env|secret):([A-Za-z_][A-Za-z0-9_]*)\}$")


class Secret:
    """A resolved secret value, WRITE-ONLY in memory: held encrypted at rest (secret_box)
    and revealed only through `reveal()`. `repr`/`str` render the REFERENCE, not the value,
    so an accidental log, f-string, or `repr(settings)` cannot leak it."""

    __slots__ = ("_reference", "_ciphertext", "_nonce")

    def __init__(self, reference: str, value: str):
        self._reference = reference
        self._ciphertext, self._nonce = encrypt(value)

    @property
    def reference(self) -> str:
        return self._reference

    def reveal(self) -> str:
        """The plaintext secret. The ONLY way out -- called by the consumer that needs it,
        never by serialization/logging."""
        return decrypt(self._ciphertext, self._nonce)

    def __repr__(self) -> str:
        return f"Secret({self._reference})"

    __str__ = __repr__


def reveal_secret(value):
    """The plaintext of a resolved `Secret`, or the value unchanged when it is already a plain
    string (or None). The single choke point a migrated secret-bearing read reveals through, so
    a `Secret` is unwrapped exactly where the value is used and nowhere else (hy-2562h)."""
    return value.reveal() if isinstance(value, Secret) else value


class SecretProvider(Protocol):
    """A pluggable `${secret:NAME}` backend. `get` returns the secret, or None when this
    provider does not hold it (the caller then FAILS CLOSED -- never a plaintext fallback)."""

    def get(self, name: str) -> str | None: ...


class DirSecretProvider:
    """The default `${secret:NAME}` provider: the file NAME under the mounted secrets dir
    `HYPERSET_SECRETS_DIR` (default `/run/secrets`) -- the Docker/Kubernetes secret mount.
    A missing or unreadable file is None (unresolved, which the caller fatals on); the
    trailing newline a secret file conventionally carries is stripped."""

    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        raw = env.get(SECRETS_DIR_ENV, "").strip()
        self._dir = Path(raw or _DEFAULT_SECRETS_DIR)

    def get(self, name: str) -> str | None:
        try:
            return self._dir.joinpath(name).read_text(encoding="utf-8").rstrip("\n")
        except OSError:
            return None


def resolve_secrets(
    settings: dict,
    *,
    env: dict | None = None,
    provider: SecretProvider | None = None,
) -> dict:
    """The validated settings with every secret-typed reference resolved to a `Secret`
    (write-only, encrypted at rest). `${env:NAME}` reads `env`; `${secret:NAME}` reads
    `provider` (default `DirSecretProvider`). The two forms never cross. An unresolved
    reference is a fatal `ConfigError` naming the path -- no plaintext fallback."""
    env = os.environ if env is None else env
    provider = DirSecretProvider(env) if provider is None else provider

    def _resolve(reference, path):
        match = _REF_RE.fullmatch(reference) if isinstance(reference, str) else None
        if match is None:
            # The schema's `Ref` already guaranteed the shape; this is defensive.
            raise ConfigError(f"{path}: not a resolvable secret reference")
        kind, name = match.group(1), match.group(2)
        value = env.get(name) if kind == "env" else provider.get(name)
        if value is None:
            source = "environment variable" if kind == "env" else "secret"
            raise ConfigError(
                f"{path}: unresolved reference {reference} -- no {source} named {name!r} "
                "(a secret reference must resolve; there is no plaintext fallback)"
            )
        return Secret(reference, value)

    return SCHEMA.map_secrets(settings, _resolve)


def redact_settings(settings: dict) -> dict:
    """A serialization-safe copy of `settings` with every secret-typed path replaced by
    `REDACTED`. PATH-ONLY (mayor ruling hy-c5p7): the schema decides which paths are
    secret, so this redacts a raw reference AND a resolved `Secret` alike and never
    string-matches a value. Use this for any config dump / log / error / served echo."""
    return SCHEMA.map_secrets(settings, lambda value, path: REDACTED)
