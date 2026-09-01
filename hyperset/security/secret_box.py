"""AES-256-GCM encryption at rest for the write-back token (hy-up4k, ADR 0026).

The key-encryption key (KEK) is read from the environment, NEVER stored in the
database: a database dump alone cannot decrypt a stored token -- the blast radius
is "DB dump AND the KEK", and the KEK lives only in the deployment's environment.

If `HYPERSET_SECRET_KEY` is unset, an EPHEMERAL in-memory key is generated once
with a LOUD warning. Tokens encrypted under it do not survive a restart, which is
correct for demo/local use and never acceptable for a real deployment -- the
ephemeral key is never persisted, so no production key is ever silently written.
"""

from __future__ import annotations

import base64
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEK_ENV = "HYPERSET_SECRET_KEY"
_NONCE_BYTES = 12
_KEY_BYTES = 32

_ephemeral_kek: bytes | None = None
_warned = False


class SecretBoxError(Exception):
    """Encryption or decryption failed. The caller must FAIL CLOSED -- refuse the
    operation, never fall back to an unauthenticated path."""


def _kek() -> bytes:
    """The 32-byte key-encryption key: from the environment, or an ephemeral dev
    key generated once with a loud warning."""
    global _ephemeral_kek, _warned
    raw = os.environ.get(KEK_ENV, "").strip()
    if raw:
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as exc:  # noqa: BLE001 -- any decode failure is a config error
            raise SecretBoxError(f"{KEK_ENV} is not valid base64") from exc
        if len(key) != _KEY_BYTES:
            raise SecretBoxError(
                f"{KEK_ENV} must be base64 of {_KEY_BYTES} bytes; decoded {len(key)}"
            )
        return key
    if _ephemeral_kek is None:
        _ephemeral_kek = AESGCM.generate_key(bit_length=256)
    if not _warned:
        print(
            f"WARNING: {KEK_ENV} is unset; using an EPHEMERAL in-memory key to encrypt the "
            "write-back token. Encrypted tokens will NOT survive a restart. Set "
            f"{KEK_ENV} (base64 of 32 random bytes) for any real deployment.",
            file=sys.stderr,
        )
        _warned = True
    return _ephemeral_kek


def key_is_configured() -> bool:
    """True when a durable KEK is set in the environment (not the ephemeral one)."""
    return bool(os.environ.get(KEK_ENV, "").strip())


def encrypt(plaintext: str) -> tuple[bytes, bytes]:
    """Encrypt `plaintext`, returning (ciphertext, nonce). A fresh random nonce
    per call -- AES-GCM must never reuse a nonce under one key."""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_kek()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ciphertext, nonce


def decrypt(ciphertext: bytes, nonce: bytes) -> str:
    """Decrypt to the plaintext token, or raise `SecretBoxError` (wrong/missing
    key, or tampered ciphertext -- GCM authenticates, so tampering fails here)."""
    try:
        return AESGCM(_kek()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except SecretBoxError:
        raise
    except Exception as exc:  # noqa: BLE001 -- InvalidTag or malformed input, both fail closed
        raise SecretBoxError(
            "could not decrypt the stored write-back token (wrong or missing key, or "
            "tampered ciphertext)"
        ) from exc
