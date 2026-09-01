"""Secret-reference resolution and PATH-ONLY redaction (hy-ogg5, config slice 2, ADR-0035
decision 3; mayor ruling hy-c5p7).

Resolve ${env:NAME}/${secret:NAME} AFTER the merge into write-only in-memory settings; the
two forms are distinct and non-interchangeable; an unresolved reference is fatal with no
plaintext fallback; and a resolved value is NEVER serialized/logged/echoed -- redaction is
PATH-ONLY, driven by the schema, never a value string-match.
"""

from __future__ import annotations

import json

import pytest

from hyperset.config import (
    REDACTED,
    ConfigError,
    DirSecretProvider,
    Secret,
    redact_settings,
    resolve_secrets,
)
from hyperset.config.schema import validate


class DictProvider:
    """A pluggable ${secret:NAME} provider backed by a dict (get(name)->str|None)."""

    def __init__(self, values):
        self._values = values

    def get(self, name):
        return self._values.get(name)


def _settings(**overrides):
    """A validated settings tree with two secret-typed paths (openai api_key via env,
    superset password via secret) plus a NON-secret Str path that merely LOOKS like a
    reference -- to prove redaction/resolution are path-driven, not value-matched."""
    raw = {
        "server": {"bind": "loopback", "log_level": "${env:NOT_A_SECRET}"},
        "providers": {"openai": {"api_key": "${env:OPENAI_KEY}"}},
        "connections": {"superset": {"password": "${secret:SUPERSET_PW}"}},
    }
    raw.update(overrides)
    return validate(raw)


def test_env_and_secret_refs_resolve_to_write_only_secrets():
    settings = _settings()
    resolved = resolve_secrets(
        settings,
        env={"OPENAI_KEY": "sk-ENVVALUE", "NOT_A_SECRET": "info"},
        provider=DictProvider({"SUPERSET_PW": "pw-SECRETVALUE"}),
    )
    api_key = resolved["providers"]["openai"]["api_key"]
    password = resolved["connections"]["superset"]["password"]
    assert isinstance(api_key, Secret) and isinstance(password, Secret)
    # The resolved value is reachable ONLY through reveal().
    assert api_key.reveal() == "sk-ENVVALUE"
    assert password.reveal() == "pw-SECRETVALUE"
    # A non-secret Str path that looks like a ref is left ALONE (path-only, not value-match).
    assert resolved["server"]["log_level"] == "${env:NOT_A_SECRET}"


def test_secret_is_write_only_repr_and_str_never_leak_the_value():
    secret = Secret("${env:OPENAI_KEY}", "sk-LEAKME")
    assert "sk-LEAKME" not in repr(secret)
    assert "sk-LEAKME" not in str(secret)
    assert secret.reference in repr(secret)  # the reference is safe to show
    assert secret.reveal() == "sk-LEAKME"


def test_the_two_forms_are_distinct_and_never_cross():
    # ${env:X} must NOT fall back to the secret provider, even when the provider holds X.
    with pytest.raises(ConfigError) as env_only:
        resolve_secrets(
            _settings(providers={"openai": {"api_key": "${env:ONLY_IN_PROVIDER}"}}),
            env={"NOT_A_SECRET": "info"},
            provider=DictProvider({"ONLY_IN_PROVIDER": "should-not-be-used", "SUPERSET_PW": "x"}),
        )
    assert "providers.openai.api_key" in str(env_only.value)
    assert "${env:ONLY_IN_PROVIDER}" in str(env_only.value)
    assert "should-not-be-used" not in str(env_only.value)  # the other source's value never leaks

    # ${secret:Y} must NOT fall back to the environment, even when the env holds Y.
    with pytest.raises(ConfigError) as secret_only:
        resolve_secrets(
            _settings(connections={"superset": {"password": "${secret:ONLY_IN_ENV}"}}),
            env={"ONLY_IN_ENV": "should-not-be-used", "OPENAI_KEY": "k"},
            provider=DictProvider({}),
        )
    assert "connections.superset.password" in str(secret_only.value)
    assert "${secret:ONLY_IN_ENV}" in str(secret_only.value)
    assert "should-not-be-used" not in str(secret_only.value)


def test_an_unresolved_reference_is_fatal_naming_the_path():
    with pytest.raises(ConfigError) as raised:
        resolve_secrets(_settings(), env={}, provider=DictProvider({}))
    message = str(raised.value)
    assert "providers.openai.api_key" in message
    assert "no plaintext fallback" in message


def test_redaction_is_path_only_before_and_after_resolution():
    settings = _settings()
    # BEFORE resolution: redact the raw refs -- path-driven, so it works pre-resolution.
    redacted_raw = redact_settings(settings)
    assert redacted_raw["providers"]["openai"]["api_key"] == REDACTED
    assert redacted_raw["connections"]["superset"]["password"] == REDACTED
    # A non-secret path is untouched (redaction never string-matches a ref-looking value).
    assert redacted_raw["server"]["log_level"] == "${env:NOT_A_SECRET}"

    # AFTER resolution: the resolved Secret leaves are ALSO redacted by path, and the
    # resolved VALUE never appears in the serialized dump.
    resolved = resolve_secrets(
        settings,
        env={"OPENAI_KEY": "sk-ENVVALUE", "NOT_A_SECRET": "info"},
        provider=DictProvider({"SUPERSET_PW": "pw-SECRETVALUE"}),
    )
    redacted = redact_settings(resolved)
    assert redacted["providers"]["openai"]["api_key"] == REDACTED
    assert redacted["connections"]["superset"]["password"] == REDACTED
    dumped = json.dumps(redacted)
    assert "sk-ENVVALUE" not in dumped and "pw-SECRETVALUE" not in dumped
    assert dumped.count(REDACTED) == 2


def test_a_resolved_settings_tree_cannot_be_serialized_without_redaction():
    # Defense in depth: a Secret is not JSON-serializable, so a naive dump of the RESOLVED
    # (unredacted) tree fails closed rather than silently leaking the value.
    resolved = resolve_secrets(
        _settings(),
        env={"OPENAI_KEY": "sk-ENVVALUE", "NOT_A_SECRET": "info"},
        provider=DictProvider({"SUPERSET_PW": "pw-SECRETVALUE"}),
    )
    with pytest.raises(TypeError):
        json.dumps(resolved)


def test_a_plaintext_secret_field_is_a_validation_error_before_resolution():
    # The slice-1 boundary this slice depends on: a secret-typed field holding plaintext
    # never reaches resolution -- it is a fatal validation error naming the path.
    with pytest.raises(ConfigError) as raised:
        validate({"server": {"bind": "loopback"}, "providers": {"openai": {"api_key": "plain"}}})
    assert "providers.openai.api_key" in str(raised.value)
    assert "never a plaintext value" in str(raised.value)


def test_dir_secret_provider_reads_the_mounted_secret_file(tmp_path):
    (tmp_path / "SUPERSET_PW").write_text("pw-from-file\n", encoding="utf-8")
    provider = DirSecretProvider(env={"HYPERSET_SECRETS_DIR": str(tmp_path)})
    # The conventional trailing newline of a mounted secret file is stripped.
    assert provider.get("SUPERSET_PW") == "pw-from-file"
    # A secret the mount does not carry is None -> the caller fails closed.
    assert provider.get("ABSENT") is None


def test_dir_secret_provider_end_to_end_default_is_run_secrets(tmp_path):
    (tmp_path / "SUPERSET_PW").write_text("pw-mounted", encoding="utf-8")
    resolved = resolve_secrets(
        _settings(),
        env={
            "OPENAI_KEY": "sk-ENVVALUE",
            "NOT_A_SECRET": "info",
            "HYPERSET_SECRETS_DIR": str(tmp_path),
        },
    )
    assert resolved["connections"]["superset"]["password"].reveal() == "pw-mounted"
