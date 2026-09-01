"""The playground model settings must match the endpoint (GitHub #325, hy-5nzs).

`_build_agent_model` branches the model CLASS on `base_url`
(`OpenAIResponsesModel` for the real OpenAI endpoint, `OpenAIChatCompletionsModel`
for an OpenAI-compatible gateway). The SETTINGS must branch the same way, wire
field included: the Responses model maps `max_tokens` to `max_output_tokens`,
while the chat-completions gateway must send `max_completion_tokens` (plain
`max_tokens` is rejected for a reasoning model there, and a Responses-API
`reasoning=` param is rejected on any model). Sending either wrong field made the
model return no answer. These pin that the settings track the branch, use the
right wire field, and that a budget cutoff is named rather than reported as a
bare "no answer".
"""

import asyncio
import json

import pytest
from agents import (
    ModelSettings,
    ModelTracing,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
)
from openai import Omit

from playground.ui import app


def _build(monkeypatch, base_url: str | None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_MODELS_JSON",
        json.dumps(
            [
                {
                    "value": app.DEFAULT_OPENAI_MODEL,
                    "label": app.DEFAULT_OPENAI_MODEL,
                    "provider": "openai",
                }
            ]
        ),
    )
    if base_url is None:
        monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", base_url)
    # The vendor path confirms the one supported model against the provider's GET /models
    # (hy-ubd6t). Pin a deterministic hosted list so the unit stays offline.
    monkeypatch.setattr(
        app,
        "_vendor_hosted_model_ids",
        lambda _base, _key: {app.DEFAULT_OPENAI_MODEL},
    )
    return app._build_agent_model("openai", app.DEFAULT_OPENAI_MODEL)


def test_a_compatible_gateway_gets_settings_without_reasoning(monkeypatch):
    monkeypatch.delenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raising=False)
    sdk_model, settings = _build(monkeypatch, "https://gateway.example/v1")

    assert isinstance(sdk_model, OpenAIChatCompletionsModel)
    assert isinstance(settings, ModelSettings)
    # The bug: a Responses-API `reasoning=` param sent to a chat-completions gateway.
    assert settings.reasoning is None
    # And the wire field: this endpoint takes `max_completion_tokens`, not
    # `max_tokens`, which it rejects for a reasoning model.
    assert settings.max_tokens is None
    assert settings.extra_body == {
        "max_completion_tokens": app.DEFAULT_OPENAI_MAX_COMPLETION_TOKENS
    }


def test_the_default_openai_endpoint_keeps_reasoning(monkeypatch):
    monkeypatch.delenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raising=False)
    sdk_model, settings = _build(monkeypatch, None)

    assert isinstance(sdk_model, OpenAIResponsesModel)
    assert settings.reasoning is not None
    assert settings.reasoning.effort == "medium"
    # The vendor-default Responses path stays byte-identical to the pre-#325 code:
    # the cap here is 1024, NOT the larger configurable gateway budget. The
    # Responses model maps `max_tokens` to `max_output_tokens`.
    assert settings.max_tokens == 1024
    assert settings.max_tokens == app.DEFAULT_OPENAI_RESPONSES_MAX_TOKENS


def test_the_vendor_cap_ignores_the_gateway_override(monkeypatch):
    # Raising the gateway budget must not move the vendor Responses path.
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "9999")
    _, settings = _build(monkeypatch, None)
    assert settings.max_tokens == 1024


def test_the_default_base_url_stated_explicitly_also_keeps_reasoning(monkeypatch):
    # The default endpoint named in full is the same endpoint, not a gateway.
    _, settings = _build(monkeypatch, app.DEFAULT_OPENAI_BASE_URL)

    assert settings.reasoning is not None


def test_the_gateway_cap_is_larger_than_the_old_1024():
    # The old 1024 let a reasoning model spend the whole budget thinking and
    # return empty; the gateway default must clear it (GitHub #325).
    assert app.DEFAULT_OPENAI_MAX_COMPLETION_TOKENS > 1024


def test_the_gateway_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "8192")
    assert app._openai_max_completion_tokens() == 8192


@pytest.mark.parametrize("raw", ["", "  ", "not-an-int", "0", "-5"])
def test_a_bad_completion_cap_override_falls_back(monkeypatch, raw):
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raw)
    assert app._openai_max_completion_tokens() == app.DEFAULT_OPENAI_MAX_COMPLETION_TOKENS


def test_the_retired_ollama_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="OpenAI only"):
        app._build_agent_model("ollama", "llama3")


def test_an_ollama_compatible_local_endpoint_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    with pytest.raises(RuntimeError, match="local or Ollama-compatible"):
        app._build_agent_model("openai", app.DEFAULT_OPENAI_MODEL)


# --- Request-level wire tests: prove the actual create() payload, not just the
# --- ModelSettings object, carries the right field per branch (adversary #4).


def _openai_omitted(value) -> bool:
    """True when the OpenAI client would drop this field from the wire payload."""
    return isinstance(value, Omit)


def _capture_chat_create_kwargs(monkeypatch, sdk_model, settings):
    """Run the chat-completions model far enough to build its create() kwargs, then
    capture them by intercepting the client call."""

    class _Captured(Exception):
        def __init__(self, kwargs):
            self.kwargs = kwargs

    async def fake_create(**kwargs):
        raise _Captured(kwargs)

    monkeypatch.setattr(sdk_model._client.chat.completions, "create", fake_create)
    with pytest.raises(_Captured) as excinfo:
        asyncio.run(
            sdk_model.get_response(
                system_instructions=None,
                input="hello",
                model_settings=settings,
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
            )
        )
    return excinfo.value.kwargs


def _capture_responses_create_kwargs(monkeypatch, sdk_model, settings):
    class _Captured(Exception):
        def __init__(self, kwargs):
            self.kwargs = kwargs

    async def fake_create(**kwargs):
        raise _Captured(kwargs)

    monkeypatch.setattr(sdk_model._client.responses, "create", fake_create)
    with pytest.raises(_Captured) as excinfo:
        asyncio.run(
            sdk_model.get_response(
                system_instructions=None,
                input="hello",
                model_settings=settings,
                tools=[],
                output_schema=None,
                handoffs=[],
                tracing=ModelTracing.DISABLED,
            )
        )
    return excinfo.value.kwargs


def test_the_gateway_wire_omits_reasoning_and_sends_max_completion_tokens(monkeypatch):
    monkeypatch.delenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raising=False)
    sdk_model, settings = _build(monkeypatch, "https://gateway.example/v1")
    kwargs = _capture_chat_create_kwargs(monkeypatch, sdk_model, settings)

    # `reasoning_effort` and `max_tokens` are dropped from the wire payload...
    assert _openai_omitted(kwargs["reasoning_effort"])
    assert _openai_omitted(kwargs["max_tokens"])
    # ...and the cap rides as `max_completion_tokens` in the request body instead.
    assert kwargs["extra_body"] == {
        "max_completion_tokens": app.DEFAULT_OPENAI_MAX_COMPLETION_TOKENS
    }


def test_the_responses_wire_sends_max_output_tokens_and_reasoning(monkeypatch):
    monkeypatch.delenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raising=False)
    sdk_model, settings = _build(monkeypatch, None)
    kwargs = _capture_responses_create_kwargs(monkeypatch, sdk_model, settings)

    # The vendor path caps via `max_output_tokens` at the byte-identical 1024,
    # carries `reasoning`, and never sends `max_completion_tokens`.
    assert kwargs["max_output_tokens"] == 1024
    assert kwargs["reasoning"] is not None and kwargs["reasoning"].effort == "medium"
    assert not _openai_omitted(kwargs["reasoning"])


# --- Budget-exhaustion detection: the FINAL completion request's usage, not a
# --- sum across turns (adversary #2), and provider-specific remediation (#3).


class _Usage:
    def __init__(self, output_tokens):
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, output_tokens):
        self.usage = _Usage(output_tokens)


class _Final:
    def __init__(self, *output_tokens):
        self.raw_responses = [_Response(n) for n in output_tokens]


def test_a_spent_final_response_with_no_text_is_flagged_exhausted():
    # Reasoning ate the whole cap on the last request -- distinguishable from a
    # plain empty answer.
    assert app._budget_exhausted(_Final(4096), 4096) is True


def test_an_underspent_final_response_is_not_exhausted():
    assert app._budget_exhausted(_Final(100), 4096) is False


def test_earlier_turns_near_the_cap_do_not_falsely_flag_exhaustion():
    # A multi-turn run whose earlier responses each neared the per-request cap but
    # whose FINAL response spent little must NOT be reported exhausted -- summing
    # across turns (the bounced behavior) would wrongly flag it.
    assert app._budget_exhausted(_Final(4000, 4000, 12), 4096) is False


def test_the_final_turn_hitting_the_cap_is_flagged_even_after_small_turns():
    assert app._budget_exhausted(_Final(10, 20, 4096), 4096) is True


def test_no_responses_is_not_exhausted():
    assert app._budget_exhausted(_Final(), 4096) is False


def test_gateway_remediation_names_the_configurable_openai_cap(monkeypatch):
    # A compatible gateway's cap IS configurable, so raising it is offered.
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    hint = app._budget_remediation()
    assert "HYPERSET_OPENAI_MAX_COMPLETION_TOKENS" in hint
    assert "HYPERSET_OLLAMA_MAX_TOKENS" not in hint


def test_vendor_remediation_does_not_name_the_uncontrollable_cap_var(monkeypatch):
    # The vendor Responses cap is a fixed 1024 that HYPERSET_OPENAI_MAX_COMPLETION_TOKENS
    # does NOT raise, so the hint must point only at lowering reasoning effort
    # (the adversary's remaining blocker).
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)
    hint = app._budget_remediation()
    assert "HYPERSET_OPENAI_MAX_COMPLETION_TOKENS" not in hint
    assert "HYPERSET_OPENAI_REASONING_EFFORT" in hint


# --- The effective cap is read off the built settings, so budget detection uses
# --- the vendor path's real 1024, not the larger gateway cap (adversary #6).


def test_the_effective_cap_of_the_vendor_path_is_its_real_1024(monkeypatch):
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "9999")
    _, settings = _build(monkeypatch, None)
    # Even with the gateway cap raised, the vendor run's effective cap is 1024.
    assert app._effective_output_cap(settings) == 1024


def test_the_effective_cap_of_the_gateway_path_is_the_configurable_cap(monkeypatch):
    monkeypatch.delenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", raising=False)
    _, settings = _build(monkeypatch, "https://gateway.example/v1")
    assert app._effective_output_cap(settings) == app.DEFAULT_OPENAI_MAX_COMPLETION_TOKENS


def test_a_vendor_responses_run_that_spends_its_1024_is_flagged_exhausted(monkeypatch):
    # The whole point: a vendor completion that exhausts its real 1024 cap must be
    # detected even though _openai_max_completion_tokens() defaults to 4096.
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "9999")
    _, settings = _build(monkeypatch, None)
    budget = app._effective_output_cap(settings)
    assert budget == 1024
    assert app._budget_exhausted(_Final(1024), budget) is True
    # A gateway-sized spend that would look fine under the 4096 default is caught.
    assert app._budget_exhausted(_Final(1024), budget) is True
