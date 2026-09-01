from __future__ import annotations

from hyperset.transport import review_runtime


def test_authoring_uses_the_pinned_openai_luna_contract(monkeypatch):
    captured = {}

    class _Runtime:
        def __init__(self, config, **kwargs):
            captured.update(config=config, **kwargs)

    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "openai-author")
    monkeypatch.setenv("OPENAI_API_KEY", "server-side-key")
    monkeypatch.setattr("hyperset.planner.openai_runtime.OpenAIAgentsRuntime", _Runtime)

    review_runtime.authoring_runtime()

    config = captured["config"]
    assert config.model == "gpt-5.6-luna"
    assert config.base_url == "https://gateway.example/v1"
    assert config.api_key == "server-side-key"
    assert config.allocated_context_window is None
    assert captured["responses_api"] is False
    assert captured["reasoning_effort"] == "medium"
    assert captured["enforce_context_window"] is False


def test_authoring_never_reads_the_ollama_contract(monkeypatch):
    captured = {}

    class _Runtime:
        def __init__(self, config, **kwargs):
            captured["config"] = config
            captured.update(kwargs)

    monkeypatch.setenv("HYPERSET_OLLAMA_BASE_URL", "http://forbidden-ollama:11434")
    monkeypatch.setenv("HYPERSET_OLLAMA_MODEL", "forbidden-model")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("HYPERSET_OPENAI_MODEL", raising=False)
    monkeypatch.setattr("hyperset.planner.openai_runtime.OpenAIAgentsRuntime", _Runtime)

    review_runtime.authoring_runtime()

    assert captured["config"].base_url == "https://api.openai.com/v1"
    assert captured["config"].model == "gpt-5.6-luna"
    assert captured["responses_api"] is True


def test_official_openai_authoring_uses_the_responses_api(monkeypatch):
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runtime = review_runtime.authoring_runtime()

    assert runtime._model_type.__name__ == "OpenAIResponsesModel"
    assert runtime._settings.reasoning.effort == "high"
    assert runtime.provenance()["allocated_context_window"] is None
    assert runtime.provenance()["context_window_enforced"] is False


def test_authoring_manifest_tools_use_executor_validation_not_openai_strict_mode(monkeypatch):
    captured = {}

    class _Result:
        final_output = None

    async def _run(agent, _question, *, max_turns):
        captured["tools"] = agent.tools
        return _Result()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("agents.Runner.run", _run)
    runtime = review_runtime.authoring_runtime()

    runtime.run("draft", on_message=lambda _text: None, call_tool=lambda _call: {})

    assert captured["tools"]
    assert all(tool.strict_json_schema is False for tool in captured["tools"])
