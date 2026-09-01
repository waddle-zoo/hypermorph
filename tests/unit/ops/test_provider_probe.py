"""Bounded live provider reachability probes (hy-ng8o7, V1 gap Admin/5).

The network is faked by injecting `http`/`tcp`, so reachable / unreachable / timeout are
driven without a real Ollama, OpenAI endpoint, or database -- the same fake-the-boundary
pattern as test_connection_probe.py.
"""

from __future__ import annotations

import json

import pytest

from hyperset.ops.provider_probe import (
    PROVIDER_COMPONENTS,
    ProviderProbe,
    probe_provider,
    probe_providers,
)

CONFIGURED_ENV = {
    "OPENAI_API_KEY": "sk-supersecret",
    "HYPERSET_OPENAI_BASE_URL": "https://gateway/v1",
    "HYPERSET_ANALYTICS_DB_URL": "postgresql://u:pw@warehouse:5433/analytics",
    "HYPERSET_EMBEDDING_PROVIDER": "openai",
    "HYPERSET_EMBEDDING_BASE_URL": "https://embed/v1",
    "HYPERSET_MODEL_PROVIDER": "openai",
}


def _http_ok(url, headers=None):
    return True, "HTTP 200"


def _http_bad(url, headers=None):
    return False, "the read operation timed out"


def _tcp_ok(host, port):
    return True, f"connected to {host}:{port}"


def _tcp_bad(host, port):
    return False, "connection refused"


def test_every_component_is_unknown_and_makes_no_network_call_when_unconfigured():
    def _boom(*a, **k):
        raise AssertionError("an unconfigured component must make NO network call")

    probes = probe_providers(env={}, http=_boom, tcp=_boom)
    assert [p.component for p in probes] == list(PROVIDER_COMPONENTS)
    for probe in probes:
        assert probe.status == "unknown"
        assert probe.configured is False
        assert probe.reachable is False


def test_all_configured_and_reachable_report_ready():
    probes = {
        p.component: p for p in probe_providers(env=CONFIGURED_ENV, http=_http_ok, tcp=_tcp_ok)
    }
    for component in ("openai", "analytics_db", "embedding", "runtime"):
        assert probes[component].status == "ready", component
        assert probes[component].configured is True


def test_a_configured_but_unreachable_component_is_blocked():
    probes = {
        p.component: p for p in probe_providers(env=CONFIGURED_ENV, http=_http_bad, tcp=_tcp_bad)
    }
    for component in ("openai", "analytics_db", "embedding", "runtime"):
        assert probes[component].status == "blocked", component
        assert probes[component].reachable is False


def test_a_secret_key_never_appears_in_a_probe_result():
    """The key authenticates the reachability check and is dropped -- it never enters the
    ProviderProbe (which is what the admin route serves)."""
    for probe in probe_providers(env=CONFIGURED_ENV, http=_http_ok, tcp=_tcp_ok):
        assert "supersecret" not in json.dumps(probe.as_dict()), probe.component


def test_the_deterministic_embedding_provider_is_local_and_needs_no_network():
    probe = probe_provider("embedding", env={"HYPERSET_EMBEDDING_PROVIDER": "deterministic"})
    assert probe.status == "ready"
    assert probe.configured is True
    assert probe.reachable is None  # in-process, no endpoint to probe
    assert "deterministic" in probe.reason


def test_openai_configured_with_a_key_but_no_base_url_is_blocked_not_crashing():
    probe = probe_provider("openai", env={"OPENAI_API_KEY": "sk-x"}, http=_http_ok)
    assert probe.status == "blocked"
    assert probe.configured is True and probe.reachable is False


def test_analytics_db_is_a_connect_only_probe_reusing_host_and_port():
    seen = {}

    def _tcp(host, port):
        seen["host"], seen["port"] = host, port
        return True, "ok"

    probe = probe_provider(
        "analytics_db",
        env={"HYPERSET_ANALYTICS_DB_URL": "postgresql://u:pw@warehouse:5433/analytics"},
        tcp=_tcp,
    )
    assert seen == {"host": "warehouse", "port": 5433}  # CONNECT only, no query (ADR 0012)
    assert probe.status == "ready"


def test_probe_never_raises_even_if_the_injected_primitive_returns_a_failure_string():
    # A dead endpoint surfaces as a typed blocked result, never an exception out of the probe.
    probe = probe_provider(
        "openai",
        env={"OPENAI_API_KEY": "sk-x", "HYPERSET_OPENAI_BASE_URL": "https://x/v1"},
        http=lambda u, headers=None: (False, "boom"),
    )
    assert isinstance(probe, ProviderProbe)
    assert probe.status == "blocked" and probe.reason == "boom"


def test_openai_probe_uses_the_same_runtime_env_contract():
    seen = {}

    def _http(url, headers=None):
        seen["url"], seen["headers"] = url, headers
        return True, "ok"

    probe = probe_provider(
        "openai",
        env={
            "OPENAI_API_KEY": "sk-runtime",
            "HYPERSET_OPENAI_BASE_URL": "https://gateway.example/v1",
        },
        http=_http,
    )
    assert probe.status == "ready"
    assert seen == {
        "url": "https://gateway.example/v1/models",
        "headers": {"Authorization": "Bearer sk-runtime"},
    }


def test_the_deployed_runtime_refuses_an_ollama_selection_without_probing():
    probe = probe_provider(
        "runtime",
        env={"HYPERSET_MODEL_PROVIDER": "ollama"},
        http=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not probe")),
    )
    assert probe.status == "blocked"
    assert probe.reachable is False
    assert "requires 'openai'" in probe.reason


def test_an_unknown_component_name_is_a_usage_error():
    with pytest.raises(ValueError, match="unknown provider component"):
        probe_provider("banana", env={})
