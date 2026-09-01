import json

import pytest

from playground.ui import app
from playground.ui.app import _validate_demo_sql


def test_playground_has_no_builtin_demo_agents(monkeypatch):
    monkeypatch.delenv("HYPERSET_PLAYGROUND_AGENTS_JSON", raising=False)
    monkeypatch.delenv("HYPERSET_PLAYGROUND_DEFAULT_AGENT", raising=False)

    config = app._playground_runtime_config()

    assert config["agents"] == []
    assert config["default_agent"] == ""


def test_playground_runtime_config_keeps_the_pinned_model_when_legacy_config_overrides_it(
    monkeypatch,
):
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [
                {
                    "value": "finance",
                    "label": "Finance agent",
                    "description": "Answers finance questions.",
                    "instruction": "Use the finance policy.",
                }
            ]
        ),
    )
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_MODELS_JSON",
        json.dumps([{"value": "custom-model", "label": "Custom model", "provider": "openai"}]),
    )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_AGENT", "finance")
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_MODEL", "custom-model")

    config = app._playground_runtime_config()

    assert config["agents"] == [
        {"value": "finance", "label": "Finance agent", "detail": "Answers finance questions."}
    ]
    assert config["models"] == [
        {
            "value": "gpt-5.6-luna",
            "label": "gpt-5.6-luna · openai",
            "provider": "openai",
        }
    ]
    assert config["default_agent"] == "finance"
    assert config["default_model"] == "gpt-5.6-luna"


def test_playground_can_select_a_generic_default_agent(monkeypatch):
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [
                {
                    "value": "default",
                    "label": "Default",
                    "description": "A general-purpose agent.",
                    "instruction": (
                        "Use every configured capability that helps answer the question."
                    ),
                },
                {
                    "value": "specialist",
                    "label": "Specialist",
                    "description": "A focused agent.",
                    "instruction": "Stay focused.",
                },
            ]
        ),
    )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_AGENT", "default")

    config = app._playground_runtime_config()

    assert config["default_agent"] == "default"
    assert config["agents"][0] == {
        "value": "default",
        "label": "Default",
        "detail": "A general-purpose agent.",
    }


def test_playground_sql_tool_allows_one_read_only_statement():
    assert _validate_demo_sql("SELECT region FROM customer_dim;") == (
        "SELECT region FROM customer_dim"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE customer_dim SET region = 'NA'",
        "SELECT 1; DROP TABLE customer_dim",
        "-- comment\nSELECT 1",
        "DELETE FROM customer_dim",
    ],
)
def test_playground_sql_tool_rejects_writes_and_multiple_statements(sql):
    with pytest.raises(ValueError):
        _validate_demo_sql(sql)


def test_agent_harness_uses_the_sdk_and_its_read_only_tool(monkeypatch):
    captured = {}

    class FakeRunResult:
        final_output = "Recognized revenue is 42."

    async def fake_run(agent, prompt, max_turns):
        captured.update(agent=agent, prompt=prompt, max_turns=max_turns)
        tool = agent.tools[0]
        await tool.on_invoke_tool(None, '{"sql":"SELECT 42 AS revenue"}')
        return FakeRunResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_REASONING_EFFORT", "medium")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "offline"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )
    monkeypatch.setattr(
        app,
        "_run_demo_sql",
        lambda sql: {"sql": sql, "columns": ["revenue"], "rows": [{"revenue": 42}]},
    )
    monkeypatch.setattr("agents.Runner.run", fake_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "question": "What was revenue?",
            "mode": "governed",
            "bundle": {
                "bundle_id": "cb-test",
                "request": {"directive": {"domains": ["revenue"]}},
                "context_authority": {"path": "examples/revenue", "commit_sha": "abc123"},
                "resolution": {"status": "governed", "warnings": []},
                "provenance_refs": ["observed_version:test"],
                "instructions": {
                    "definitions": [{"term": "revenue"}],
                    "approved_sources": [{"ref": "table:orders"}],
                    "filters": ["status = 'completed'"],
                    "joins": [{"from": "orders", "to": "customers"}],
                    "validations": ["revenue >= 0"],
                    "prohibited_sources": [],
                },
                "linked_evidence": {
                    "observed_assets": [{"ref": "table:orders"}],
                    "freshness": [{"ref": "table:orders"}],
                    "conflicts": [],
                    "deprecations": [],
                },
                "execution": {
                    "performed_by_hyperset": False,
                    "result_validated_by_hyperset": False,
                },
            },
        }
    )

    assert result["answer"] == "Recognized revenue is 42."
    assert result["planner"]["runtime"] == "openai-agents-sdk"
    assert result["sql"] == "SELECT 42 AS revenue"
    assert captured["max_turns"] == 20
    assert captured["agent"].tools[0].name == "run_read_only_sql"
    assert captured["agent"].model_settings.tool_choice == "required"
    assert type(captured["agent"].model).__name__ == "OpenAIChatCompletionsModel"
    assert result["context_included"] is True
    assert result["trace"][0]["label"] == "GOVERNED"
    assert result["trace"][-1]["data"]["result_validated_by_hyperset"] is False
    assert "test-secret" not in json.dumps(result)


def test_source_rules_question_keeps_sql_available_but_not_required(monkeypatch):
    captured = {}

    class FakeRunResult:
        final_output = "Use the approved source and completed-order filter from the bundle."

    async def fake_run(agent, prompt, max_turns):
        captured.update(agent=agent, prompt=prompt, max_turns=max_turns)
        return FakeRunResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "offline"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )
    monkeypatch.setattr("agents.Runner.run", fake_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "question": (
                "Which source and rules should an analyst use for recognized revenue by region?"
            ),
            "mode": "governed",
            "bundle": {
                "bundle_id": "cb-test",
                "request": {"directive": {"domains": ["revenue"]}},
                "context_authority": {"path": "examples/revenue", "commit_sha": "abc123"},
                "resolution": {"status": "governed", "warnings": []},
                "instructions": {
                    "approved_sources": [{"ref": "table:orders"}],
                    "filters": ["status = 'completed'"],
                },
                "linked_evidence": {},
                "execution": {
                    "performed_by_hyperset": False,
                    "result_validated_by_hyperset": False,
                },
            },
        }
    )

    assert result["planner"]["sql_requested"] is False
    assert result["planner"]["tool_call_count"] == 0
    assert [tool.name for tool in captured["agent"].tools] == ["run_read_only_sql"]
    assert captured["max_turns"] == 20
    assert captured["agent"].model_settings.tool_choice is None
    assert "available on every run" in captured["agent"].instructions
    assert "do not include SQL syntax" in captured["agent"].instructions


def test_agent_harness_discover_mode_resolves_context_before_answering(monkeypatch):
    captured = {}

    class FakeRunResult:
        final_output = "The discovered governed answer is ready."

    async def fake_run(agent, prompt, max_turns):
        captured.update(agent=agent, prompt=prompt, max_turns=max_turns)
        return FakeRunResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "connected"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )
    monkeypatch.setattr(
        app,
        "_run_context_selector",
        lambda payload: {
            "directive": {"domains": ["revenue"], "concepts": ["recognized_revenue"]},
            "rationale": "Revenue question matched the catalog.",
            "model": payload["model"],
        },
    )
    monkeypatch.setattr(
        app,
        "_resolve_playground_bundle",
        lambda question, directive, **_: {
            "bundle_id": "cb-discovered",
            "request": {"directive": directive},
            "context_authority": {"path": "examples/revenue", "commit_sha": "abc123"},
            "resolution": {"status": "governed", "warnings": []},
            "instructions": {"definitions": [{"term": "revenue"}]},
            "linked_evidence": {},
            "execution": {},
        },
    )
    monkeypatch.setattr("agents.Runner.run", fake_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "question": "Which source defines recognized revenue?",
            "mode": "discover",
            "agent": "analyst",
            "bundle": None,
            "catalog_domains": ["revenue"],
        }
    )

    assert result["context_source"] == "agent_discovered"
    assert result["context_included"] is True
    assert result["bundle_id"] == "cb-discovered"
    assert "Governed Hyperset ContextBundle follows" in captured["prompt"]
    assert any(item["label"] == "ASSIST" for item in result["trace"])


def test_agent_builder_policy_withholds_context_and_tools(monkeypatch):
    captured = {}

    class FakeRunResult:
        final_output = "This draft cannot use the resolved context."

    async def fake_run(agent, prompt, max_turns):
        captured.update(agent=agent, prompt=prompt, max_turns=max_turns)
        return FakeRunResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "offline"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )
    monkeypatch.setattr("agents.Runner.run", fake_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "question": "Which source should I use for recognized revenue?",
            "mode": "governed",
            "catalog_domains": ["revenue"],
            "agent": "analyst",
            "agent_config": {
                "key": "restricted",
                "label": "Restricted agent",
                "system_prompt": "Never make an unsupported claim.",
                "allowed_connections": ["superset"],
                "denied_tools": ["run_read_only_sql"],
                "denied_domains": ["revenue"],
            },
            "bundle": {
                "bundle_id": "cb-test",
                "request": {"directive": {"domains": ["revenue"]}},
                "context_authority": {},
                "resolution": {},
                "instructions": {},
                "linked_evidence": {},
            },
        }
    )

    assert captured["agent"].tools == []
    assert "Never make an unsupported claim." in captured["agent"].instructions
    assert result["agent_label"] == "Restricted agent"
    assert result["context_included"] is False
    assert result["agent_config"]["policy_result"] == "denied_by_agent_context_policy"
    assert result["agent_config"]["effective_connections"] == ["superset"]
    assert result["agent_config"]["effective_tools"] == []


def test_streaming_chat_continues_when_context_resolution_is_unresolved(monkeypatch):
    events = []
    captured = {}

    selection = {
        "directive": {"domains": [], "concepts": [], "asset_refs": []},
        "rationale": "The catalog did not cover the question.",
    }

    def fail_resolution(_question, _directive, **_):
        raise app.PlaygroundResolutionError(
            {
                "code": "directive_required",
                "message": (
                    "the directive names no domains and no asset_refs, so there is nothing "
                    "to retrieve"
                ),
                "recovery": "select a domain from the catalog and resolve again",
            }
        )

    def fake_harness(payload, *, stream_callback=None):
        captured.update(payload)
        return {
            "answer": "This answer is not backed by a governed context bundle.",
            "context_resolution": {"status": "unresolved", "error": payload["resolution_error"]},
        }

    monkeypatch.setattr(app, "_run_context_selector", lambda _payload: selection)
    monkeypatch.setattr(app, "_resolve_playground_bundle", fail_resolution)
    monkeypatch.setattr(app, "_run_agent_harness", fake_harness)

    app._stream_playground_chat(
        {
            "question": "What is a metric the catalog does not cover?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
        },
        events.append,
    )

    resolution = next(event for event in events if event["type"] == "resolution_error")
    stages = [event for event in events if event["type"] == "stage"]
    assert resolution["error"]["code"] == "directive_required"
    assert any(stage.get("status") == "warning" for stage in stages)
    assert stages[-1]["title"] == "Answering without governed context"
    assert captured["bundle"] is None
    assert captured["resolution_error"]["recovery"] == (
        "select a domain from the catalog and resolve again"
    )
    assert events[-1]["type"] == "done"


def test_context_selector_returns_only_exact_catalog_values(monkeypatch):
    class FakeSelection:
        def model_dump(self):
            return {
                "domains": ["revenue", "not-a-real-domain"],
                "concepts": ["recognized_revenue", "invented_concept"],
                "asset_refs": [
                    "superset:dataset:known",
                    "table:postgres:analytics.public.finance_orders_daily",
                ],
                "rationale": "The question asks for revenue by region.",
            }

    class FakeRunResult:
        final_output = FakeSelection()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        app,
        "_context_catalog",
        lambda: [
            {
                "domain": "revenue",
                "title": "Recognized revenue by region",
                "concepts": ["recognized_revenue"],
                "approved_source_refs": ["table:postgres:analytics.public.finance_orders_daily"],
                "prohibited_source_refs": ["table:postgres:analytics.public.raw_payments"],
                "evidence_refs": ["superset:dataset:known"],
            }
        ],
    )
    monkeypatch.setattr(app, "_run_agent_sync", lambda *_args, **_kwargs: FakeRunResult())

    result = app._run_context_selector(
        {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
    )

    assert result["directive"] == {
        "domains": ["revenue"],
        "concepts": ["recognized_revenue"],
        "asset_refs": ["superset:dataset:known"],
    }
    assert result["catalog_domains"] == ["revenue"]
    assert "test-secret" not in json.dumps(result)


def test_openai_status_discloses_configuration_without_the_key(monkeypatch):
    class Authenticated:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(app.request, "urlopen", lambda *_args, **_kwargs: Authenticated())
    status = app._openai_status()
    assert status["status"] == "connected"
    assert status["version"] == "gpt-5.6-luna"
    assert "test-secret" not in json.dumps(status)


# --- A provider/model-call fault on the discovery call must be attributed to the
# --- provider, not the catalog, and must not silently downgrade governance under a
# --- corpus-sounding label (GitHub #344, hy-yw6l).


def _one_domain_catalog():
    return [
        {
            "domain": "revenue",
            "title": "Recognized revenue",
            "concepts": ["recognized_revenue"],
            "approved_source_refs": [],
            "prohibited_source_refs": [],
            "evidence_refs": [],
        }
    ]


def _provider_400():
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(
        "Error code: 400 - Unsupported parameter: 'reasoning'",
        response=response,
        body=None,
    )


def test_context_selector_classifies_a_provider_400_as_a_provider_fault(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    def raise_400(*_args, **_kwargs):
        raise _provider_400()

    monkeypatch.setattr(app, "_run_agent_sync", raise_400)

    with pytest.raises(app.PlaygroundProviderError) as excinfo:
        app._run_context_selector(
            {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
        )

    payload = excinfo.value.error_payload
    # The emitted code names the provider fault, NOT the catalog outcome.
    assert payload["code"] == "context_discovery_provider_error"
    assert payload["code"] != "context_discovery_failed"
    assert payload["provider"] == "openai"
    # Message points at the model/provider config, not the corpus.
    assert "provider" in payload["message"].lower()
    assert (
        "catalog" not in payload["message"].lower() or "not an empty catalog" in payload["message"]
    )
    assert "400" in payload["detail"]
    # The secret never rides in the surfaced error.
    assert "test-secret" not in json.dumps(payload)


def test_a_provider_fault_detail_redacts_a_credential_bearing_url(monkeypatch):
    # hy-yts5j #447: a MISCONFIGURED base_url can make the provider client echo a
    # scheme://user:token@host in its exception text. `_provider_error_payload` builds
    # `detail` from that exception, and the surfaced detail is rendered in BOTH the
    # provider-fault view (index.jsx GovernedBlocked) AND the run-details disclosure --
    # so it must be redacted at the SERVER boundary, before it reaches either site.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    def raise_with_creds(*_args, **_kwargs):
        raise RuntimeError(
            "Connection error to https://user:supersecret@gateway.example/v1/chat/completions"
        )

    monkeypatch.setattr(app, "_run_agent_sync", raise_with_creds)

    with pytest.raises(app.PlaygroundProviderError) as excinfo:
        app._run_context_selector(
            {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
        )
    payload = excinfo.value.error_payload
    # The userinfo credential is stripped from the surfaced detail...
    assert "supersecret" not in payload["detail"]
    assert "user:supersecret@" not in payload["detail"]
    # ...and nowhere in the WHOLE surfaced payload (both render sites read from it).
    assert "supersecret" not in json.dumps(payload)
    # ...while the non-secret host stays, so the operator can still diagnose the fault.
    assert "gateway.example" in payload["detail"]


def test_a_missing_credential_is_a_provider_fault_not_a_catalog_outcome(monkeypatch):
    # No OPENAI_API_KEY: _build_agent_model raises before any model call. That is a
    # config fault, not the catalog failing to yield a directive.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    with pytest.raises(app.PlaygroundProviderError) as excinfo:
        app._run_context_selector(
            {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
        )

    assert excinfo.value.error_payload["code"] == "context_discovery_provider_error"


def test_an_empty_catalog_selection_is_not_a_provider_fault(monkeypatch):
    # A genuine catalog-empty (the model returns no matching values) must NOT raise a
    # provider fault -- it yields an empty directive, keeping the two distinct.
    class EmptySelection:
        def model_dump(self):
            return {"domains": [], "concepts": [], "asset_refs": [], "rationale": "no match"}

    class FakeRunResult:
        final_output = EmptySelection()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)
    monkeypatch.setattr(app, "_run_agent_sync", lambda *_a, **_k: FakeRunResult())

    result = app._run_context_selector(
        {"provider": "openai", "model": "gpt-5.6-luna", "question": "Unrelated question?"}
    )
    assert result["directive"] == {"domains": [], "concepts": [], "asset_refs": []}


def test_discover_mode_attributes_a_provider_fault_to_the_provider(monkeypatch):
    class FakeRunResult:
        final_output = "Answered without a governed bundle."

    async def fake_run(agent, prompt, max_turns):
        return FakeRunResult()

    def raise_provider_fault(_payload):
        raise app.PlaygroundProviderError(
            app._provider_error_payload("openai", "gpt-5.6-luna", _provider_400())
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "connected"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )
    monkeypatch.setattr(app, "_run_context_selector", raise_provider_fault)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "question": "Which source defines recognized revenue?",
            "mode": "discover",
            "agent": "analyst",
            "bundle": None,
            "catalog_domains": ["revenue"],
        }
    )

    # Distinct source, not the generic discovery_failed.
    assert result["context_source"] == "discovery_provider_error"
    error = result["context_resolution"]["error"]
    assert error["code"] == "context_discovery_provider_error"
    assert error["code"] != "context_discovery_failed"
    assert "provider" in error["message"].lower()


def _connected_status():
    return {
        "superset": {"status": "connected"},
        "datahub": {"status": "connected"},
        "analytics_db": {"status": "connected", "tables": []},
    }


def test_streaming_provider_fault_keeps_the_source_through_the_real_harness(monkeypatch):
    # Exercise the REAL streaming harness path (do NOT patch _run_agent_harness):
    # a discovery provider fault must survive the mode="governed" answer call so the
    # terminal done.result.context_source names the provider, not "none" (#344).
    events = []

    def raise_provider_fault(_payload):
        raise app.PlaygroundProviderError(
            app._provider_error_payload("openai", "gpt-5.6-luna", _provider_400())
        )

    class FakeRunResult:
        final_output = "Answer without a governed bundle; not backed by governed context."

    async def fake_run(agent, prompt, max_turns):
        return FakeRunResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(app, "_demo_status", _connected_status)
    monkeypatch.setattr(app, "_run_context_selector", raise_provider_fault)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    app._stream_playground_chat(
        {
            "question": "Which source defines recognized revenue?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
        },
        events.append,
    )

    resolution = next(event for event in events if event["type"] == "resolution_error")
    assert resolution["error"]["code"] == "context_discovery_provider_error"

    warning = next(
        event for event in events if event["type"] == "stage" and event.get("status") == "warning"
    )
    assert warning["title"] == "Model provider failed the discovery call"
    assert "catalog" not in warning["detail"].lower() or "not an empty catalog" in warning["detail"]

    done = events[-1]
    assert done["type"] == "done"
    # The terminal result carries the provider attribution, not "none".
    assert done["result"]["context_source"] == "discovery_provider_error"
    assert done["result"]["context_source"] != "none"
    # The answer phase actually ran (source catalog reached the model prompt).
    assert done["result"]["answer"]


def test_streaming_missing_credential_degrades_without_crashing(monkeypatch):
    # Fully real path with no OPENAI_API_KEY: the discovery call faults, and the
    # answer-phase _build_agent_model ALSO raises for the missing key. The turn must
    # not crash -- it emits a labelled done attributing the fault to the provider.
    events = []

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(app, "_demo_status", _connected_status)
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    app._stream_playground_chat(
        {
            "question": "Which source defines recognized revenue?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
        },
        events.append,
    )

    done = events[-1]
    assert done["type"] == "done"
    result = done["result"]
    # No unhandled crash; the source still attributes the provider fault.
    assert result["context_source"] == "discovery_provider_error"
    assert result["context_resolution"]["error"]["code"] == "context_discovery_provider_error"
    # The degraded answer names the provider fault rather than inventing a result.
    assert "provider" in result["answer"].lower()
    assert "test-secret" not in json.dumps(result)


def test_streaming_governed_only_provider_fault_keeps_the_source(monkeypatch):
    # Governed-only + provider fault takes the EARLY-return refusal branch, which
    # must still carry the provider-specific terminal source, not "governed_blocked"
    # (adversary round 3, GitHub #344).
    events = []

    def raise_provider_fault(_payload):
        raise app.PlaygroundProviderError(
            app._provider_error_payload("openai", "gpt-5.6-luna", _provider_400())
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(app, "_demo_status", _connected_status)
    monkeypatch.setattr(app, "_run_context_selector", raise_provider_fault)

    app._stream_playground_chat(
        {
            "question": "Which source defines recognized revenue?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
            "governed_only": True,
        },
        events.append,
    )

    done = events[-1]
    assert done["type"] == "done"
    result = done["result"]
    # Refusal semantics kept...
    assert result["governed_blocked"] is True
    assert result["context_included"] is False
    # ...but the source names the provider fault, not the generic block.
    assert result["context_source"] == "discovery_provider_error"
    assert result["context_source"] != "governed_blocked"
    assert result["context_resolution"]["error"]["code"] == "context_discovery_provider_error"
    assert "provider" in result["answer"].lower()


def test_streaming_governed_only_missing_credential_keeps_the_source(monkeypatch):
    # Fully real path: no OPENAI_API_KEY, governed-only on. The discovery call faults
    # for the missing key, the refusal branch fires, and the source still attributes
    # the provider.
    events = []

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(app, "_demo_status", _connected_status)
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    app._stream_playground_chat(
        {
            "question": "Which source defines recognized revenue?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
            "governed_only": True,
        },
        events.append,
    )

    done = events[-1]
    assert done["type"] == "done"
    result = done["result"]
    assert result["governed_blocked"] is True
    assert result["context_source"] == "discovery_provider_error"
    assert result["context_resolution"]["error"]["code"] == "context_discovery_provider_error"
    assert "test-secret" not in json.dumps(result)


def test_streaming_governed_only_empty_catalog_still_blocks(monkeypatch):
    # Canary: a GENUINE catalog-empty under governed-only must keep the
    # "governed_blocked" source -- the provider attribution must not leak onto a
    # non-provider refusal.
    events = []

    empty_selection = {
        "directive": {"domains": [], "concepts": [], "asset_refs": []},
        "rationale": "The catalog did not cover the question.",
    }

    def fail_resolution(_question, _directive, **_):
        raise app.PlaygroundResolutionError(
            {
                "code": "directive_required",
                "message": "the directive names no domains and no asset_refs",
                "recovery": "select a domain from the catalog and resolve again",
            }
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(app, "_demo_status", _connected_status)
    monkeypatch.setattr(app, "_run_context_selector", lambda _payload: empty_selection)
    monkeypatch.setattr(app, "_resolve_playground_bundle", fail_resolution)

    app._stream_playground_chat(
        {
            "question": "A metric the catalog does not cover?",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "agent": "analyst",
            "governed_only": True,
        },
        events.append,
    )

    done = events[-1]
    assert done["type"] == "done"
    result = done["result"]
    assert result["governed_blocked"] is True
    assert result["context_source"] == "governed_blocked"
    assert result["context_source"] != "discovery_provider_error"
    assert result["context_resolution"]["error"]["code"] == "directive_required"


# --- hy-yts5j: the gateway-model / vendor-base_url incoherence guard ---


class _FakeModelsResponse:
    # A minimal context-manager stand-in for `urlopen`, so `_vendor_hosted_model_ids`
    # exercises its REAL parse + cache path over a faked socket (not an injected double).
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._body


def test_vendor_hosted_model_ids_parses_and_caches_the_real_models_list(monkeypatch):
    # The REAL probe: GET /models returns a data list; the ids are collected, and a second
    # call is served from the cache WITHOUT a second network call (hy-ubd6t).
    app._VENDOR_MODELS_CACHE.clear()
    calls = {"n": 0}
    body = json.dumps(
        {"data": [{"id": "gpt-5.6-luna"}, {"id": "gpt-4o"}, {"no-id": True}]}
    ).encode()

    def _fake_urlopen(_req, timeout=None):
        calls["n"] += 1
        return _FakeModelsResponse(200, body)

    monkeypatch.setattr(app.request, "urlopen", _fake_urlopen)

    ids = app._vendor_hosted_model_ids("https://api.openai.com/v1", "key")
    assert ids == {"gpt-5.6-luna", "gpt-4o"}
    # Second call hits the cache, not the network.
    again = app._vendor_hosted_model_ids("https://api.openai.com/v1", "key")
    assert again == {"gpt-5.6-luna", "gpt-4o"}
    assert calls["n"] == 1


def test_vendor_hosted_model_ids_returns_none_when_the_probe_is_unreachable(monkeypatch):
    # An unreachable/failed /models is 'could not verify' -- it returns None and is NOT
    # cached, so a first-call blip does not poison a later successful probe (hy-ubd6t).
    app._VENDOR_MODELS_CACHE.clear()

    def _boom(_req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(app.request, "urlopen", _boom)

    assert app._vendor_hosted_model_ids("https://api.openai.com/v1", "key") is None
    assert "https://api.openai.com/v1" not in app._VENDOR_MODELS_CACHE


def test_a_verified_model_stays_hosted_across_a_later_transient_models_failure(monkeypatch):
    # hy-ubd6t r2: caching the positive is load-bearing. After ONE successful /models, a
    # verified-hosted model must stay allowed even if a LATER /models call fails -- the
    # cached list is returned, not None, so a transient blip cannot flip a working setup to
    # could-not-verify.
    app._VENDOR_MODELS_CACHE.clear()
    body = json.dumps({"data": [{"id": "gpt-5.6-luna"}]}).encode()
    state = {"fail": False}

    def _urlopen(_req, timeout=None):
        if state["fail"]:
            raise OSError("transient /models outage")
        return _FakeModelsResponse(200, body)

    monkeypatch.setattr(app.request, "urlopen", _urlopen)

    first = app._vendor_hosted_model_ids("https://api.openai.com/v1", "key")
    assert first == {"gpt-5.6-luna"}
    # /models now fails, but the verified list is served from the cache.
    state["fail"] = True
    again = app._vendor_hosted_model_ids("https://api.openai.com/v1", "key")
    assert again == {"gpt-5.6-luna"}, "a transient failure after a success returns the cache"


def test_is_vendor_openai_base_url_recognizes_every_vendor_equivalent_form():
    # hy-yts5j r2: the vendor origin is matched canonically, so an explicit default port or a
    # case variant is still recognized (a textual `==` let them bypass the guard). A gateway
    # host, a non-default port, and a non-https scheme are NOT the vendor.
    assert app._is_vendor_openai_base_url("https://api.openai.com/v1") is True
    assert app._is_vendor_openai_base_url("https://api.openai.com:443/v1") is True  # explicit 443
    assert app._is_vendor_openai_base_url("https://API.OPENAI.COM/v1") is True  # case variant
    assert app._is_vendor_openai_base_url("https://api.openai.com./v1") is True  # trailing DNS dot
    assert app._is_vendor_openai_base_url("https://api.openai.com") is True  # no path
    assert app._is_vendor_openai_base_url("https://gateway.example/v1") is False  # a gateway
    assert app._is_vendor_openai_base_url("https://api.openai.com:8080/v1") is False  # non-default
    assert app._is_vendor_openai_base_url("http://api.openai.com/v1") is False  # not https


@pytest.mark.parametrize(
    "vendor_equivalent",
    [
        "https://api.openai.com:443/v1",
        "https://API.OPENAI.COM/v1",
        "https://api.openai.com./v1",
        "https://api.openai.com",
    ],
    ids=["explicit-443", "uppercase-host", "trailing-dot", "no-path"],
)
def test_the_guard_is_not_bypassed_by_a_vendor_equivalent_base_url(vendor_equivalent, monkeypatch):
    # The bypass the adversary named: a vendor-equivalent URL that a textual `==` misses would
    # take the gateway branch and let gpt-5.6-luna reach api.openai.com anyway (fail-OPEN,
    # reintroducing the P1). For an account whose vendor /models does NOT list the model, each
    # vendor-equivalent form must still fail closed (hy-ubd6t: the probe drives the reject).
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", vendor_equivalent)
    # A definitive vendor list that omits the model -- a genuine mismatch.
    monkeypatch.setattr(app, "_vendor_hosted_model_ids", lambda _base, _key: {"gpt-4o"})

    with pytest.raises(RuntimeError) as excinfo:
        app._build_agent_model("openai", "gpt-5.6-luna")
    assert "gpt-5.6-luna" in str(excinfo.value)
    assert "HYPERSET_OPENAI_BASE_URL" in str(excinfo.value)


def test_build_agent_model_fails_closed_for_a_model_the_vendor_does_not_host(monkeypatch):
    # A model the account's vendor endpoint does NOT host (its GET /models omits it) must
    # raise BEFORE constructing a client/model, naming the model and base_url so the operator
    # can act -- not a 6-second opaque 401/404 from api.openai.com (hy-yts5j, hy-ubd6t).
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default
    # A definitive vendor list that omits gpt-5.6-luna -- a genuinely gateway-only deployment.
    monkeypatch.setattr(app, "_vendor_hosted_model_ids", lambda _base, _key: {"gpt-4o"})

    with pytest.raises(RuntimeError) as excinfo:
        app._build_agent_model("openai", "gpt-5.6-luna")

    message = str(excinfo.value)
    assert "gpt-5.6-luna" in message
    assert app.DEFAULT_OPENAI_BASE_URL in message
    assert "HYPERSET_OPENAI_BASE_URL" in message


def test_build_agent_model_allows_a_vendor_hosted_luna_on_the_vendor_endpoint(monkeypatch):
    # hy-ubd6t: for an account whose vendor endpoint DOES host gpt-5.6-luna (it appears in
    # GET /models), the model is ALLOWED -- no false reject -- and takes the vendor Responses
    # path (overseer confirmed POST /v1/responses 200 READY for luna on the vendor endpoint).
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default
    monkeypatch.setattr(
        app, "_vendor_hosted_model_ids", lambda _base, _key: {"gpt-5.6-luna", "gpt-4o"}
    )

    sdk_model, settings = app._build_agent_model("openai", "gpt-5.6-luna")
    assert type(sdk_model).__name__ == "OpenAIResponsesModel"
    assert sdk_model.model == "gpt-5.6-luna"
    # Vendor Responses path: reasoning + the fixed 1024 cap, not the gateway wire fields.
    assert settings.reasoning is not None
    assert settings.max_tokens == app.DEFAULT_OPENAI_RESPONSES_MAX_TOKENS


def test_build_agent_model_fails_closed_when_capability_is_unverifiable(monkeypatch):
    # hy-ubd6t r2 (dual-block): a could-not-verify /models result (None) must FAIL CLOSED on
    # the vendor path, not proceed -- proceeding was a fail-OPEN that let an unverified model
    # reach the provider. The message is DISTINCT from the not-hosted reject (says "could not
    # verify") and names the model + base_url so it is actionable, not a 6s opaque 401/404.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default
    monkeypatch.setattr(app, "_vendor_hosted_model_ids", lambda _base, _key: None)

    with pytest.raises(RuntimeError) as excinfo:
        app._build_agent_model("openai", "gpt-5.6-luna")

    message = str(excinfo.value)
    assert "could not verify" in message
    assert "gpt-5.6-luna" in message
    assert app.DEFAULT_OPENAI_BASE_URL in message
    # Distinct from the definitive not-hosted reject.
    assert "does not list it" not in message


def _assert_gateway_wire(sdk_model, settings, *, base_url, key, cap):
    """The actual wire settings the SDK will send on the gateway path (hy-yts5j r2):
    the chat-completions model class (NOT the vendor Responses model), the model id, the
    GATEWAY endpoint + configured key on the client, a BOUNDED connect timeout so a hung
    provider connect fails fast instead of hanging discovery, and the completion-token cap
    in the request body. Mutating the model class, base URL, or settings changes one of
    these and reds the assertion."""
    assert type(sdk_model).__name__ == "OpenAIChatCompletionsModel"
    assert sdk_model.model == "gpt-5.6-luna"
    client = sdk_model._client
    assert str(client.base_url).rstrip("/") == base_url  # the GATEWAY endpoint, not vendor
    assert client.api_key == key
    assert client.timeout.connect is not None and client.timeout.connect > 0  # bounded connect
    assert settings.extra_body["max_completion_tokens"] == cap
    # The vendor-only knobs are absent on the gateway path (a gateway rejects `reasoning=`).
    assert getattr(settings, "reasoning", None) is None
    assert getattr(settings, "max_tokens", None) is None


def test_build_agent_model_builds_the_gateway_wire_settings(monkeypatch):
    # The coherent pairing the deployment supplies: gpt-5.6-luna on a GATEWAY base_url builds
    # the chat-completions model targeting that endpoint with the configured key + token cap.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "4096")

    sdk_model, settings = app._build_agent_model("openai", "gpt-5.6-luna")
    _assert_gateway_wire(
        sdk_model, settings, base_url="https://gateway.example/v1", key="test-secret", cap=4096
    )


def test_the_configured_gateway_chat_answers_with_the_right_wire_settings(monkeypatch):
    # LOAD-BEARING (hy-yts5j r2, adversary): drive the governed luna+openai DISCOVERY call --
    # the exact path that failed -- through the real harness with a configured gateway, and
    # assert BOTH that a non-empty answer comes back AND that the model the Runner was invoked
    # with carries the correct wire settings. Mutating the model class / base URL / settings
    # reds this; a fake-Runner that only returns a value cannot hide a broken invocation
    # because the model actually handed to the run is asserted.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "4096")
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    class _Selection:
        def model_dump(self):
            return {
                "domains": ["revenue"],
                "concepts": ["recognized_revenue"],
                "asset_refs": [],
                "rationale": "revenue matches",
            }

    class _RunResult:
        final_output = _Selection()

    captured: dict = {}

    def _capture_run(agent, prompt, max_turns):
        # The REAL _build_agent_model ran to build agent.model; capture what the run receives.
        captured["model"] = agent.model
        captured["settings"] = agent.model_settings
        return _RunResult()

    monkeypatch.setattr(app, "_run_agent_sync", _capture_run)

    result = app._run_context_selector(
        {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
    )

    # A NON-EMPTY governed answer came back through the harness (not a provider fault).
    assert result["directive"]["domains"] == ["revenue"]
    assert result["directive"]["concepts"] == ["recognized_revenue"]
    # ...and the invocation used the correct gateway wire settings.
    _assert_gateway_wire(
        captured["model"],
        captured["settings"],
        base_url="https://gateway.example/v1",
        key="test-secret",
        cap=4096,
    )


def _governed_bundle():
    return {
        "bundle_id": "cb-test",
        "request": {"directive": {"domains": ["revenue"]}},
        "context_authority": {"path": "examples/revenue", "commit_sha": "abc123"},
        "resolution": {"status": "governed", "warnings": []},
        "provenance_refs": ["observed_version:test"],
        "instructions": {
            "definitions": [{"term": "revenue"}],
            "approved_sources": [{"ref": "table:orders"}],
            "filters": [],
            "joins": [],
            "validations": [],
            "prohibited_sources": [],
        },
        "linked_evidence": {
            "observed_assets": [{"ref": "table:orders"}],
            "freshness": [],
            "conflicts": [],
            "deprecations": [],
        },
        "execution": {"performed_by_hyperset": False, "result_validated_by_hyperset": False},
    }


def test_the_governed_answer_turn_returns_a_non_empty_answer_with_wire_settings(monkeypatch):
    # LOAD-BEARING (hy-yts5j r2 v2, adversary): the P1 failure is the ANSWER phase producing
    # NO answer. Drive the full governed turn through `_run_agent_harness` (the ANSWER phase,
    # NOT discovery) with a configured gateway, and assert result["answer"] is NON-EMPTY AND
    # that the ANSWER-phase Runner invocation used the gateway wire settings. Mutating the
    # answer-phase model class / base URL / settings reds this; a fake-Runner cannot hide a
    # broken answer-phase invocation because the model handed to the run is asserted.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "4096")
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_AGENTS_JSON",
        json.dumps(
            [{"value": "analyst", "label": "Test analyst", "instruction": "Use the test policy."}]
        ),
    )
    monkeypatch.setattr(
        app,
        "_demo_status",
        lambda: {
            "superset": {"status": "connected"},
            "datahub": {"status": "connected"},
            "analytics_db": {"status": "connected", "tables": []},
        },
    )

    captured = {}

    class _RunResult:
        final_output = "Recognized revenue comes from the governed orders source."
        raw_responses = []

    async def _capture_run(agent, prompt, max_turns):
        captured["agent"] = agent
        return _RunResult()

    monkeypatch.setattr("agents.Runner.run", _capture_run)

    result = app._run_agent_harness(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            # A definitional (non-SQL) question so the answer comes straight from the model.
            "question": "Which source defines recognized revenue?",
            "mode": "governed",
            "agent": "analyst",
            "bundle": _governed_bundle(),
        }
    )

    # The ANSWER phase returned a NON-EMPTY governed answer (the P1 regression).
    assert result["answer"] == "Recognized revenue comes from the governed orders source."
    assert result["context_source"] != "answer_provider_error"
    # ...and the ANSWER-phase Runner was invoked with the gateway wire settings.
    _assert_gateway_wire(
        captured["agent"].model,
        captured["agent"].model_settings,
        base_url="https://gateway.example/v1",
        key="test-secret",
        cap=4096,
    )


def test_build_agent_model_rejects_a_non_luna_openai_model(monkeypatch):
    # The customer-facing MVP has one supported OpenAI model. A legacy configured model
    # cannot silently widen the runtime surface.
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default

    with pytest.raises(ValueError, match="gpt-5.6-luna"):
        app._build_agent_model("openai", "gpt-4o")


def test_the_default_config_mismatch_surfaces_a_precise_provider_fault_without_a_call(monkeypatch):
    # End to end: with the shipped default (gpt-5.6-luna + vendor base_url) and a key set,
    # the discovery selector raises a redacted provider-config fault whose detail names the
    # base_url/model mismatch -- BEFORE any provider call (Runner is never reached, so a
    # sabotaged synchronous runner must NOT fire).
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)
    # This account's vendor endpoint does NOT host gpt-5.6-luna (its /models omits it),
    # so the mismatch is detected before any provider call (hy-ubd6t).
    monkeypatch.setattr(app, "_vendor_hosted_model_ids", lambda _base, _key: {"gpt-4o"})

    def _no_call(*_args, **_kwargs):
        raise AssertionError("no provider call may run once the config mismatch is detected")

    monkeypatch.setattr(app, "_run_agent_sync", _no_call)

    with pytest.raises(app.PlaygroundProviderError) as excinfo:
        app._run_context_selector(
            {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
        )

    payload = excinfo.value.error_payload
    assert payload["code"] == "context_discovery_provider_error"
    assert app.DEFAULT_OPENAI_BASE_URL in payload["detail"]
    assert "gpt-5.6-luna" in payload["detail"]


def test_an_unverifiable_models_probe_makes_no_provider_call(monkeypatch):
    # hy-ubd6t r2 (dual-block, the fail-OPEN fix): when the real GET /models probe is
    # unreachable/malformed (could-not-verify), the vendor path must FAIL CLOSED before any
    # provider call. Drive the whole selector with the REAL _vendor_hosted_model_ids over a
    # faked urlopen that raises, and assert (a) a redacted provider fault whose detail says
    # "could not verify", and (b) NO provider call ran -- the synchronous runner is sabotaged to
    # fire an AssertionError if reached. MUTATION-RED: proceeding on None (the old fail-open)
    # reaches Runner and reds this.
    app._VENDOR_MODELS_CACHE.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.delenv("HYPERSET_OPENAI_BASE_URL", raising=False)  # => vendor default
    monkeypatch.setattr(app, "_context_catalog", _one_domain_catalog)

    def _boom(_req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(app.request, "urlopen", _boom)

    def _no_call(*_args, **_kwargs):
        raise AssertionError("no provider call may run when capability could not be verified")

    monkeypatch.setattr(app, "_run_agent_sync", _no_call)

    with pytest.raises(app.PlaygroundProviderError) as excinfo:
        app._run_context_selector(
            {"provider": "openai", "model": "gpt-5.6-luna", "question": "Revenue by region?"}
        )

    payload = excinfo.value.error_payload
    assert payload["code"] == "context_discovery_provider_error"
    assert "could not verify" in payload["detail"]
    assert "gpt-5.6-luna" in payload["detail"]
