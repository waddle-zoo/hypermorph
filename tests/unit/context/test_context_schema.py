"""The narrow v0 Git context format (hy-gh-43).

Parsing is pure, so these run without Git or Postgres. The fixture under
`tests/fixtures/git_context/revenue` is the same directory the integration
and Postgres suites commit into a real repository, so a schema change that
breaks the customer-owned example fails here first.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hyperset.bundle.instructions import git_instructions
from hyperset.context.errors import ContextValidationError
from hyperset.context.schema import is_unevaluated, parse_context, to_manifest_document

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "git_context" / "revenue"


def fixture_files() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(FIXTURE_DIR.iterdir())
        if path.is_file()
    }


@pytest.mark.unit
def test_the_revenue_fixture_parses_into_the_benchmark_fields():
    document = parse_context(fixture_files())

    assert document.domain == "revenue"
    assert document.owner_refs == ["team:finance-data"]
    normalized = document.normalized
    assert [source["ref"] for source in normalized["approved_sources"]] == [
        "table:postgres:analytics.public.finance_orders_daily",
        "table:postgres:analytics.public.customer_dim",
    ]
    assert normalized["prohibited_sources"][0]["ref"] == (
        "table:postgres:analytics.public.raw_payments"
    )
    assert normalized["grain"] == "order_date by customer_dim.region"
    assert normalized["filters"] == [
        "finance_orders_daily.status = 'completed'",
        "customer_dim.is_test = false",
    ]
    assert normalized["evals"]["cases"][0]["critical"] is True


@pytest.mark.unit
def test_only_explicit_bi_overrides_are_collected_as_connector_evidence():
    document = parse_context(fixture_files())

    refs = {ref.ref for ref in document.evidence_refs}
    assert "superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2" in refs
    assert "superset:dataset:6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4" in refs
    assert len(refs) == 3
    assert {ref.governed_source_ref for ref in document.evidence_refs} == {
        "table:postgres:analytics.public.finance_orders_daily",
        "table:postgres:analytics.public.customer_dim",
        "table:postgres:analytics.public.raw_payments",
    }


@pytest.mark.unit
def test_normalized_form_round_trips_back_to_the_committed_manifest():
    files = fixture_files()
    document = parse_context(files)

    rebuilt = to_manifest_document(document.normalized)
    committed = yaml.safe_load(files["manifest.yaml"])
    assert rebuilt == committed

    # Deterministic: re-parsing the rebuilt manifest produces the same
    # normalized representation, so the runtime projection cannot drift from
    # what Git says.
    round_tripped = dict(files)
    round_tripped["manifest.yaml"] = yaml.safe_dump(rebuilt, sort_keys=False)
    assert parse_context(round_tripped).normalized == document.normalized


@pytest.mark.unit
def test_parsing_is_deterministic_for_identical_input():
    assert parse_context(fixture_files()).normalized == parse_context(fixture_files()).normalized


def _invalid(mutate) -> list[str]:
    files = fixture_files()
    manifest = yaml.safe_load(files["manifest.yaml"])
    mutate(manifest, files)
    files["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False)
    with pytest.raises(ContextValidationError) as excinfo:
        parse_context(files)
    return excinfo.value.reasons


def _valid(mutate):
    files = fixture_files()
    manifest = yaml.safe_load(files["manifest.yaml"])
    mutate(manifest, files)
    files["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False)
    return parse_context(files)


# --- hy-gh-287: a domain may declare no eval bank, honestly ---


@pytest.mark.unit
def test_the_revenue_fixture_is_not_unevaluated():
    """The byte-identical anchor (hy-gh-287): a domain WITH a real bank is
    evaluated, its eval shape is exactly `{path, cases}` with no marker key, and
    `is_unevaluated` is False. The none-path must not touch this."""
    normalized = parse_context(fixture_files()).normalized

    assert is_unevaluated(normalized) is False
    assert set(normalized["evals"]) == {"path", "cases"}
    assert normalized["evals"]["path"] == "evals.yaml"
    assert len(normalized["evals"]["cases"]) >= 1


@pytest.mark.unit
def test_a_domain_declaring_evals_none_validates_and_is_unevaluated():
    """`evals: none` is an honest declaration, not a failure (hy-gh-287): it
    validates, and normalizes to the no-bank shape the draft path already uses."""
    document = _valid(lambda manifest, files: manifest.update(evals="none"))

    assert is_unevaluated(document.normalized) is True
    assert document.normalized["evals"] == {"path": None, "cases": []}


@pytest.mark.unit
def test_omitting_evals_entirely_validates_and_is_unevaluated():
    """Omitting the key reads the same as declaring `none` (hy-gh-287): evals is
    no longer required, and its absence is unevaluated, not invalid."""

    def mutate(manifest, files):
        del manifest["evals"]
        del files["evals.yaml"]

    document = _valid(mutate)

    assert is_unevaluated(document.normalized) is True
    assert document.normalized["evals"] == {"path": None, "cases": []}


@pytest.mark.unit
def test_evals_none_is_case_insensitive_and_tolerates_a_null_value():
    """The sentinel is not whitespace- or case-fragile, and a bare `evals:`
    (YAML null) is the same declaration as `none` (hy-gh-287)."""
    assert is_unevaluated(_valid(lambda m, f: m.update(evals="None")).normalized)
    assert is_unevaluated(_valid(lambda m, f: m.update(evals="  none  ")).normalized)
    assert is_unevaluated(_valid(lambda m, f: m.update(evals=None)).normalized)


@pytest.mark.unit
def test_an_eval_file_with_zero_cases_still_fails():
    """The crux (hy-gh-287): declaring `none` is honest, but POINTING at a real
    file that holds zero cases is a mistake and stays a hard error. The two must
    not collapse -- an empty bank someone authored is not a declaration that
    there is none."""

    def mutate(manifest, files):
        files["evals.yaml"] = "schema_version: 1\ncases: []\n"

    assert any("declares no evaluation cases" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_evals_naming_a_file_absent_from_the_path_still_fails():
    """A named-but-missing eval file is not silently read as `none` (hy-gh-287):
    only the sentinel or an omitted key declares no bank; a real filename that is
    not present is the same mistake it always was."""

    def mutate(manifest, files):
        manifest["evals"] = "missing.yaml"

    assert any("not in the context path" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_evals_none_round_trips_as_an_omitted_key():
    """`evals: none` and an omitted key are equivalent, so the round-trip drops
    the key rather than inventing a literal to preserve (hy-gh-287); re-parsing
    is stable and still unevaluated."""
    document = _valid(lambda manifest, files: manifest.update(evals="none"))

    rebuilt = to_manifest_document(document.normalized)
    assert "evals" not in rebuilt


@pytest.mark.unit
def test_a_missing_manifest_is_rejected():
    with pytest.raises(ContextValidationError) as excinfo:
        parse_context({"context.md": "text"})
    assert excinfo.value.reasons == ["manifest.yaml is missing"]


@pytest.mark.unit
def test_an_unsupported_schema_version_is_rejected():
    reasons = _invalid(lambda manifest, files: manifest.update(schema_version=2))
    assert reasons == ["manifest.yaml schema_version must be 1, got 2"]


@pytest.mark.unit
def test_an_unsupported_field_is_rejected_rather_than_silently_dropped():
    reasons = _invalid(lambda manifest, files: manifest.update(approval_state="approved"))
    assert any("unsupported field 'approval_state'" in reason for reason in reasons)


@pytest.mark.unit
def test_connector_evidence_cannot_hide_inside_a_definition():
    def mutate(manifest, files):
        manifest["definitions"][0]["evidence_refs"] = [
            "datahub:glossary_term:urn:li:glossaryTerm:recognized_revenue"
        ]

    assert any(
        "definitions entry has unsupported field 'evidence_refs'" in reason
        for reason in _invalid(mutate)
    )


@pytest.mark.unit
def test_a_malformed_ref_is_rejected():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["ref"] = "finance_orders_daily"

    assert any("must be '<table|pipeline>:<platform>:" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_connector_ref_cannot_be_the_governed_source_identity():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["ref"] = (
            "superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2"
        )

    assert any("unsupported kind 'superset'" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_bi_override_must_be_an_explicit_superset_dataset_with_a_reason():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["bi_override"] = {
            "ref": "datahub:dataset:urn:li:dataset:orders"
        }

    reasons = _invalid(mutate)
    assert any("reason is required" in reason for reason in reasons)
    assert any("must identify a Superset dataset" in reason for reason in reasons)


@pytest.mark.unit
def test_the_same_source_cannot_be_approved_and_prohibited():
    def mutate(manifest, files):
        manifest["prohibited_sources"][0]["ref"] = manifest["approved_sources"][0]["ref"]

    assert any("both approved and prohibited" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_field_reading_an_unapproved_source_is_rejected():
    def mutate(manifest, files):
        manifest["fields"][0]["source_ref"] = "table:postgres:analytics.public.unknown"

    assert any("which is not an approved source" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_referenced_document_missing_from_the_path_is_rejected():
    def mutate(manifest, files):
        manifest["context_doc"] = "guidance.md"

    assert any("not in the context path" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_every_reason_is_reported_from_one_parse():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["ref"] = "nonsense"
        manifest["prohibited_sources"][0].pop("reason")

    reasons = _invalid(mutate)
    assert len(reasons) >= 2


# --- hy-gh-284 slice 1: parse per-source facets.grain only; unmapped is an error ---


@pytest.mark.unit
def test_a_source_may_carry_facets_grain_and_it_is_stored():
    """The concrete #284 bug: a source aggregated at a different grain than the
    domain assumes. `facets.grain` is a bare string, parsed and kept on the
    stored snapshot for a later bead to surface."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "fx_rate_date"}

    document = _valid(mutate)
    assert document.normalized["approved_sources"][0]["facets"] == {"grain": "fx_rate_date"}


@pytest.mark.unit
@pytest.mark.parametrize("subkey", ["quality", "severity", "owner", "grainn"])
def test_every_unrecognized_facet_subkey_is_an_error(subkey):
    """The invariant carried down one level (hy-gh-284): only `grain`,
    `classification`, `freshness`, `lineage`, and `checks` are recognized, and
    EVERY other facet sub-key is a hard error, not a silent drop -- a facet outside
    the surface vocabulary (a `quality` block and the like) must not slip through."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {subkey: "whatever"}

    reasons = _invalid(mutate)
    assert any(f"facets entry has unsupported field {subkey!r}" in reason for reason in reasons), (
        reasons
    )


@pytest.mark.unit
def test_a_grain_facet_beside_an_unknown_one_still_errors_on_the_unknown():
    """Recognizing `grain` does not launder an unknown sibling: a facets mapping
    that carries a valid grain AND an unmapped key is still refused."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "day", "quality": "non-negative"}

    assert any("unsupported field 'quality'" in reason for reason in _invalid(mutate))


# --- hy-c89s: a grain may not carry the node-id delimiter ':' ---


@pytest.mark.unit
def test_a_domain_grain_carrying_the_id_delimiter_is_rejected():
    """The domain grain is served verbatim in a `grain:{value}` node id, so a ':'
    in it -- the id's own delimiter -- is refused at parse (hy-c89s)."""

    def mutate(manifest, files):
        manifest["grain"] = "order_date:region"

    assert any("grain must not contain ':'" in reason for reason in _invalid(mutate)), _invalid(
        mutate
    )


@pytest.mark.unit
def test_a_source_grain_facet_carrying_the_id_delimiter_is_rejected():
    """A per-source grain is served in `grain:{ref}:{value}`; a ':' in the value
    would let it cross into the ref segment, so it is refused (hy-c89s)."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "order:date"}

    assert any(
        "approved_sources facets grain must not contain ':'" in reason
        for reason in _invalid(mutate)
    )


@pytest.mark.unit
def test_the_two_grains_that_would_alias_one_id_are_both_rejected():
    """The concrete collision the guard closes: source ref `t:a` at grain `b` keys
    `grain:t:a:b`, and source ref `t` at grain `a:b` keys the SAME `grain:t:a:b`.
    The second is rejected because its grain carries ':', so the pair can never
    coexist and the id is unambiguous."""

    def colliding(manifest, files):
        manifest["approved_sources"][0]["ref"] = "table:t"
        manifest["approved_sources"][0]["facets"] = {"grain": "a:b"}

    assert any(
        "approved_sources facets grain must not contain ':'" in reason
        for reason in _invalid(colliding)
    )


@pytest.mark.unit
def test_a_current_colon_free_grain_is_unchanged():
    """Byte-identity anchor (hy-c89s): the shipped fixture's colon-free grains parse
    exactly as before -- the domain grain and a colon-free per-source grain both
    survive, so the guard is a non-event for all current data."""
    document = _valid(
        lambda manifest, files: manifest["approved_sources"][0].update(
            facets={"grain": "order_date"}
        )
    )
    assert document.normalized["grain"] == "order_date by customer_dim.region"
    assert document.normalized["approved_sources"][0]["facets"]["grain"] == "order_date"


# --- 284-6a (hy-4giv): parse per-source facets.classification (closed value set) ---


@pytest.mark.unit
@pytest.mark.parametrize("value", ["restricted", "pii", "internal", "public"])
def test_a_source_may_carry_a_classification_from_the_closed_set(value):
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"classification": value}

    document = _valid(mutate)
    assert document.normalized["approved_sources"][0]["facets"] == {"classification": value}


@pytest.mark.unit
def test_a_classification_outside_the_closed_set_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"classification": "top-secret"}

    reasons = _invalid(mutate)
    assert any(
        "classification 'top-secret' is not one of" in reason
        and "restricted, pii, internal, public" in reason
        for reason in reasons
    ), reasons


@pytest.mark.unit
def test_a_blank_classification_is_an_error_not_a_stored_value():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"classification": "   "}

    assert any("must be a non-empty string" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_grain_and_classification_coexist_on_one_source():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "grain": "fx_rate_date",
            "classification": "restricted",
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {"grain": "fx_rate_date", "classification": "restricted"}


# --- 284-6b (hy-6c8z): parse per-source facets.freshness (structured contract) ---


@pytest.mark.unit
def test_a_source_may_carry_a_freshness_contract():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "freshness": {"cadence": "daily", "max_staleness": "24h"}
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {"freshness": {"cadence": "daily", "max_staleness": "24h"}}


@pytest.mark.unit
def test_a_freshness_contract_may_carry_just_one_of_the_two_fields():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"freshness": {"cadence": "hourly"}}

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {"freshness": {"cadence": "hourly"}}


@pytest.mark.unit
def test_a_freshness_that_declares_neither_field_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"freshness": {}}

    assert any(
        "freshness declares neither cadence nor max_staleness" in reason
        for reason in _invalid(mutate)
    )


@pytest.mark.unit
def test_an_unknown_freshness_subkey_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "freshness": {"cadence": "daily", "sla": "24h"}
        }

    assert any("unsupported field 'sla'" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_non_mapping_freshness_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"freshness": "daily"}

    assert any("freshness must be a mapping" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_blank_freshness_field_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"freshness": {"cadence": "   "}}

    assert any("must be a non-empty string" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_all_three_facets_coexist_on_one_source():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "grain": "fx_rate_date",
            "classification": "internal",
            "freshness": {"cadence": "daily", "max_staleness": "24h"},
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {
        "grain": "fx_rate_date",
        "classification": "internal",
        "freshness": {"cadence": "daily", "max_staleness": "24h"},
    }


# --- 284-7 (hy-sr7w): parse per-source facets.lineage (structured provenance) ---


@pytest.mark.unit
def test_a_source_may_carry_a_lineage_contract():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "lineage": {
                "produced_by": "dbt:finance.orders",
                "upstream": ["table:raw.orders", "table:raw.tax"],
            }
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {
        "lineage": {
            "produced_by": "dbt:finance.orders",
            "upstream": ["table:raw.orders", "table:raw.tax"],
        }
    }


@pytest.mark.unit
def test_a_lineage_may_carry_just_produced_by_or_just_upstream():
    def only_produced(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"produced_by": "airflow:job"}}

    assert _valid(only_produced).normalized["approved_sources"][0]["facets"] == {
        "lineage": {"produced_by": "airflow:job"}
    }

    def only_upstream(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"upstream": ["table:raw.x"]}}

    assert _valid(only_upstream).normalized["approved_sources"][0]["facets"] == {
        "lineage": {"upstream": ["table:raw.x"]}
    }


@pytest.mark.unit
def test_a_lineage_that_declares_neither_field_is_an_error():
    def empty_block(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {}}

    assert any(
        "lineage declares neither produced_by nor upstream" in reason
        for reason in _invalid(empty_block)
    )

    def empty_upstream(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"upstream": []}}

    assert any(
        "lineage declares neither produced_by nor upstream" in reason
        for reason in _invalid(empty_upstream)
    )


@pytest.mark.unit
def test_an_unknown_lineage_subkey_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "lineage": {"produced_by": "x", "derived_from": "y"}
        }

    assert any("unsupported field 'derived_from'" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_non_mapping_lineage_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": "dbt:finance.orders"}

    assert any("lineage must be a mapping" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_malformed_lineage_upstream_is_an_error():
    def not_a_list(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"upstream": "table:raw.x"}}

    assert any("upstream must be a list" in reason for reason in _invalid(not_a_list))

    def blank_entry(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"upstream": ["   "]}}

    assert any("must be non-empty strings" in reason for reason in _invalid(blank_entry))


@pytest.mark.unit
def test_a_blank_produced_by_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"lineage": {"produced_by": "   "}}

    reasons = _invalid(mutate)
    assert any("must be a non-empty string" in reason for reason in reasons)
    # A stated-but-blank field is a malformed field, not a missing one: its own
    # error names it, and the vaguer "declares neither" must NOT also fire.
    assert not any("declares neither" in reason for reason in reasons)


@pytest.mark.unit
def test_all_five_facets_coexist_on_one_source():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "grain": "fx_rate_date",
            "classification": "internal",
            "freshness": {"cadence": "daily"},
            "lineage": {"produced_by": "dbt:finance.orders", "upstream": ["table:raw.orders"]},
            "checks": [
                {"name": "not_null", "description": "id is present", "severity": "error"},
                {"name": "unique"},
            ],
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {
        "grain": "fx_rate_date",
        "classification": "internal",
        "freshness": {"cadence": "daily"},
        "lineage": {"produced_by": "dbt:finance.orders", "upstream": ["table:raw.orders"]},
        "checks": [
            {"name": "not_null", "description": "id is present", "severity": "error"},
            {"name": "unique"},
        ],
    }


# --- 284-8 (hy-w16y): parse per-source facets.checks (owned data-quality checks) ---


@pytest.mark.unit
def test_a_source_may_carry_a_checks_contract():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "checks": [
                {
                    "name": "row_count_positive",
                    "description": "at least one row",
                    "severity": "warn",
                }
            ]
        }

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {
        "checks": [
            {"name": "row_count_positive", "description": "at least one row", "severity": "warn"}
        ]
    }


@pytest.mark.unit
def test_a_check_may_carry_just_its_required_name():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": [{"name": "not_null"}]}

    facets = _valid(mutate).normalized["approved_sources"][0]["facets"]
    assert facets == {"checks": [{"name": "not_null"}]}


@pytest.mark.unit
def test_a_checks_that_lists_nothing_is_an_error():
    def empty_list(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": []}

    assert any("checks lists no checks" in reason for reason in _invalid(empty_list))


@pytest.mark.unit
def test_a_check_missing_its_name_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": [{"description": "no name here"}]}

    reasons = _invalid(mutate)
    assert any("name is required" in reason for reason in reasons)
    # A named-but-missing-field entry is malformed, not an empty declaration: the
    # specific "name is required" fires and the vaguer "lists no checks" must not.
    assert not any("lists no checks" in reason for reason in reasons)


@pytest.mark.unit
def test_an_unknown_check_subkey_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "checks": [{"name": "not_null", "threshold": "0.99"}]
        }

    assert any("unsupported field 'threshold'" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_non_list_checks_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": {"name": "not_null"}}

    assert any("checks must be a list of checks" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_non_mapping_check_entry_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": ["not_null"]}

    assert any("checks entries must be mappings" in reason for reason in _invalid(mutate))


@pytest.mark.unit
def test_a_blank_check_name_is_an_error():
    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"checks": [{"name": "   "}]}

    reasons = _invalid(mutate)
    assert any("must be a non-empty string" in reason for reason in reasons)
    # A stated-but-blank name is a malformed field, not a missing declaration.
    assert not any("lists no checks" in reason for reason in reasons)


@pytest.mark.unit
def test_a_blank_check_description_or_severity_is_an_error():
    def blank_description(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "checks": [{"name": "not_null", "description": "  "}]
        }

    assert any("must be a non-empty string" in reason for reason in _invalid(blank_description))

    def blank_severity(manifest, files):
        manifest["approved_sources"][0]["facets"] = {
            "checks": [{"name": "not_null", "severity": ""}]
        }

    assert any("must be a non-empty string" in reason for reason in _invalid(blank_severity))


@pytest.mark.unit
def test_facets_must_be_a_mapping_and_grain_a_non_empty_string():
    def not_a_mapping(manifest, files):
        manifest["approved_sources"][0]["facets"] = "order_date"

    assert any("facets must be a mapping" in reason for reason in _invalid(not_a_mapping))

    def blank_grain(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "   "}

    assert any("must be a non-empty string" in reason for reason in _invalid(blank_grain))


@pytest.mark.unit
def test_a_stored_grain_facet_round_trips_through_the_manifest_projection():
    """ "Store" means durable: a parsed facet survives `to_manifest_document` and
    re-parses to the same normalized form, so a later bead reads what Git said."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "fx_rate_date"}

    files = fixture_files()
    manifest = yaml.safe_load(files["manifest.yaml"])
    mutate(manifest, files)
    files["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False)
    document = parse_context(files)

    rebuilt = to_manifest_document(document.normalized)
    assert rebuilt["approved_sources"][0]["facets"] == {"grain": "fx_rate_date"}
    round_tripped = dict(files)
    round_tripped["manifest.yaml"] = yaml.safe_dump(rebuilt, sort_keys=False)
    assert parse_context(round_tripped).normalized == document.normalized


@pytest.mark.unit
def test_a_source_without_facets_is_unchanged():
    """Back-compat anchor (hy-gh-284): absent `facets` is today's behaviour, so
    no source in the shipped revenue fixture grows a `facets` key -- the stored
    shape is byte-identical to before this slice."""
    normalized = parse_context(fixture_files()).normalized
    assert normalized["approved_sources"]  # the fixture has approved sources
    for source in normalized["approved_sources"]:
        assert "facets" not in source


@pytest.mark.unit
def test_facets_are_surfaced_in_the_served_instructions():
    """284-3 (hy-gp99) removed the slice-1 strip: a source that declared a
    `facets.grain` now carries it into the served instructions, so a caller sees
    the grain that source is aggregated at. This EXPOSES the stored facet and
    decides nothing (refine-vs-replace is fork 2, the 284-4 check)."""

    def mutate(manifest, files):
        manifest["approved_sources"][0]["facets"] = {"grain": "fx_rate_date"}

    normalized = _valid(mutate).normalized
    # stored
    assert normalized["approved_sources"][0]["facets"] == {"grain": "fx_rate_date"}
    # and now surfaced, on exactly the source that declared it
    served = git_instructions(normalized)["approved_sources"]
    assert served[0]["facets"] == {"grain": "fx_rate_date"}
    # and for the fixture with NO facets, the served sources are byte-identical:
    # a source that declared no grain grows no `facets` key, so the surfacing is
    # additive and the shipped revenue bundle is unchanged.
    plain = git_instructions(parse_context(fixture_files()).normalized)["approved_sources"]
    assert plain == parse_context(fixture_files()).normalized["approved_sources"]
    assert all("facets" not in source for source in plain)


# --- hy-2zoy / ADR-0031: the governed hierarchy parent (validation-only, no emit) ---


@pytest.mark.unit
def test_a_declared_parent_is_parsed_and_round_trips():
    document = _valid(lambda m, f: m.__setitem__("parent", "finance"))
    assert document.parent == "finance"
    assert document.normalized["parent"] == "finance"
    # A non-None parent survives the projection (round-trips into a proposed manifest).
    assert to_manifest_document(document.normalized)["parent"] == "finance"


@pytest.mark.unit
def test_a_declared_parent_is_casefolded_like_the_domain():
    document = _valid(lambda m, f: m.__setitem__("parent", "Finance"))
    assert document.parent == "finance"


@pytest.mark.unit
def test_a_root_domain_has_no_parent_and_omits_it_from_the_projection():
    # The shipped revenue fixture declares no parent -> a root. Parsed as None, and
    # to_manifest_document drops it, so a root manifest round-trips unchanged (it does
    # not gain a spurious `parent: null`).
    document = parse_context(fixture_files())
    assert document.parent is None
    assert document.normalized["parent"] is None
    assert "parent" not in to_manifest_document(document.normalized)


@pytest.mark.unit
def test_a_domain_naming_itself_as_parent_is_rejected():
    reasons = _invalid(lambda m, f: m.__setitem__("parent", m["domain"]))
    assert any("its parent" in reason for reason in reasons)


@pytest.mark.unit
def test_a_self_parent_is_rejected_case_insensitively():
    reasons = _invalid(lambda m, f: m.__setitem__("parent", m["domain"].upper()))
    assert any("its parent" in reason for reason in reasons)


@pytest.mark.unit
def test_a_declared_parent_is_not_surfaced_into_the_served_instructions():
    # SV-16 safety: parent is manifest INPUT, validated at sync, never served. The
    # served instruction projection must not carry it, so the bundle stays
    # byte-identical and SCHEMA_VERSION does not move.
    from hyperset.bundle.instructions import git_instructions

    document = _valid(lambda m, f: m.__setitem__("parent", "finance"))
    assert "parent" not in git_instructions(document.normalized)
