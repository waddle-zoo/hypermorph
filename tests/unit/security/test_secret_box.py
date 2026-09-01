"""AES-256-GCM encryption at rest for the write-back token (hy-up4k)."""

from __future__ import annotations

import base64
import os

import pytest

from hyperset.security import secret_box
from hyperset.security.secret_box import SecretBoxError, decrypt, encrypt


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_encrypt_then_decrypt_round_trips_the_token(monkeypatch):
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _key())
    ciphertext, nonce = encrypt("ghp_secret_xyz")
    # The ciphertext is not the plaintext, and it decrypts back exactly.
    assert b"ghp_secret_xyz" not in ciphertext
    assert decrypt(ciphertext, nonce) == "ghp_secret_xyz"


def test_a_fresh_nonce_each_call_makes_ciphertexts_differ(monkeypatch):
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _key())
    first, _ = encrypt("ghp_secret_xyz")
    second, _ = encrypt("ghp_secret_xyz")
    assert first != second  # GCM must never reuse a nonce under one key


def test_a_wrong_key_fails_closed(monkeypatch):
    """The blast-radius property: ciphertext without the KEK is undecryptable.
    A different key (a DB dump moved to another deployment) raises rather than
    returning anything -- the caller must fail closed."""
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _key())
    ciphertext, nonce = encrypt("ghp_secret_xyz")

    monkeypatch.setenv("HYPERSET_SECRET_KEY", _key())  # a different key
    with pytest.raises(SecretBoxError):
        decrypt(ciphertext, nonce)


def test_a_tampered_ciphertext_fails_closed(monkeypatch):
    monkeypatch.setenv("HYPERSET_SECRET_KEY", _key())
    ciphertext, nonce = encrypt("ghp_secret_xyz")
    tampered = bytes([ciphertext[0] ^ 0x01]) + ciphertext[1:]
    with pytest.raises(SecretBoxError):
        decrypt(tampered, nonce)


def test_a_malformed_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("HYPERSET_SECRET_KEY", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(SecretBoxError, match="32 bytes"):
        encrypt("ghp_secret_xyz")


def test_an_unset_key_uses_an_ephemeral_key_with_a_loud_warning(monkeypatch, capsys):
    """Demo/local only: no key configured means an ephemeral in-memory key and a
    loud warning, never a silently persisted production key."""
    monkeypatch.delenv("HYPERSET_SECRET_KEY", raising=False)
    monkeypatch.setattr(secret_box, "_ephemeral_kek", None)
    monkeypatch.setattr(secret_box, "_warned", False)
    assert secret_box.key_is_configured() is False

    ciphertext, nonce = encrypt("ghp_secret_xyz")
    assert decrypt(ciphertext, nonce) == "ghp_secret_xyz"  # stable within the process
    warning = capsys.readouterr().err
    assert "HYPERSET_SECRET_KEY" in warning and "EPHEMERAL" in warning
