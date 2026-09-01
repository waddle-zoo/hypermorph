"""Compose authoring runtime contract (hy-aw2j8).

Every served HTTP/MCP process can execute ``refine_review_draft``. Each must receive
the same hosted OpenAI endpoint/model/key contract; an omitted base URL would fall back
inside the container and silently split authoring behavior by transport.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_EXPECTED_BASE = "${HYPERSET_OPENAI_BASE_URL:-https://api.openai.com/v1}"
_EXPECTED_MODEL = "${HYPERSET_OPENAI_MODEL:-gpt-5.6-luna}"
_EXPECTED_PROVIDER = "${HYPERSET_MODEL_PROVIDER:-openai}"
_EXPECTED_KEY = "${OPENAI_API_KEY:-}"


def _serve_services() -> dict[str, dict]:
    services = yaml.safe_load(COMPOSE.read_text()).get("services", {})
    return {
        name: spec
        for name, spec in services.items()
        if isinstance(spec.get("command"), list) and spec["command"][:1] == ["serve"]
    }


def test_the_serve_services_are_exactly_the_expected_three():
    assert set(_serve_services()) == {"api", "mcp", "mcp-http"}


def test_every_serve_service_uses_the_hosted_openai_authoring_contract():
    wrong = {}
    for name, spec in _serve_services().items():
        environment = spec.get("environment") or {}
        actual = {
            "provider": environment.get("HYPERSET_MODEL_PROVIDER"),
            "base_url": environment.get("HYPERSET_OPENAI_BASE_URL"),
            "model": environment.get("HYPERSET_OPENAI_MODEL"),
            "api_key": environment.get("OPENAI_API_KEY"),
        }
        expected = {
            "provider": _EXPECTED_PROVIDER,
            "base_url": _EXPECTED_BASE,
            "model": _EXPECTED_MODEL,
            "api_key": _EXPECTED_KEY,
        }
        if actual != expected:
            wrong[name] = actual
    assert wrong == {}, f"serve services without the hosted OpenAI contract: {wrong}"


def test_no_serve_service_carries_an_ollama_runtime_variable():
    found = {
        name: sorted(key for key in (spec.get("environment") or {}) if "OLLAMA" in key)
        for name, spec in _serve_services().items()
    }
    assert not any(found.values()), found
