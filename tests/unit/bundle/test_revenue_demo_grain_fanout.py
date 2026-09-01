"""The SHIPPED revenue demo catches the fx_rates_daily grain fan-out (hy-gh-284 slice a).

A literal acceptance criterion, not a synthetic one: it loads the human-owned
`playground/examples/revenue/` context exactly as shipped, projects its governed
instructions the way a served bundle does, and validates a plan that reads the
`fx_rates_daily` source at order grain WITHOUT aggregating it -- the daily-rate
fan-out the per-source `facets.grain` (ADR-0029) exists to catch. If the demo
manifest loses fx_rates_daily's grain facet, or the fan-out check stops firing on
the shipped example, this reddens.

Distinct from tests/unit/bundle/test_plan.py's fan-out unit tests, which state the
instructions inline: those prove the RULE, this proves the SHIPPED DEMO carries the
facet that arms the rule end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hyperset.bundle import AnalyticsPlan, ContextBundle, validate_analytics_plan
from hyperset.bundle.instructions import git_instructions
from hyperset.context.schema import parse_context

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "playground" / "examples" / "revenue"

FX = "table:postgres:analytics.public.fx_rates_daily"
PRIMARY = "table:postgres:analytics.public.finance_orders_daily"
DIMENSION = "table:postgres:analytics.public.customer_dim"


def _demo_instructions() -> dict:
    files = {path.name: path.read_text() for path in DEMO.iterdir() if path.is_file()}
    return git_instructions(parse_context(files).normalized)


def _demo_bundle(instructions: dict) -> ContextBundle:
    return ContextBundle(
        request={"query": "recognized revenue by region"},
        resolution={"status": "governed", "summary": "", "warnings": []},
        context_authority={
            "type": "git",
            "commit_sha": "demo",
            "context_snapshot_id": "ctxsnap-demo",
        },
        instructions=instructions,
        linked_evidence={"observed_assets": [], "findings": [], "conflicts": []},
        domain_graph={"nodes": [], "edges": []},
        provenance_refs=["git_context:ctxsnap-demo@demo"],
        resolved_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def test_the_shipped_demo_manifest_declares_the_fx_grain_facet():
    # The precondition the acceptance criterion rests on: the human-owned demo
    # actually carries fx_rates_daily's per-source grain (ADR-0029), stated as the
    # grain the daily rates are at -- not the order grain the plan runs at.
    instructions = _demo_instructions()
    fx = next(s for s in instructions["approved_sources"] if s["ref"] == FX)
    assert fx.get("facets", {}).get("grain") == "currency by rate_date"


def test_the_shipped_demo_catches_the_fx_rates_daily_fanout():
    instructions = _demo_instructions()
    bundle = _demo_bundle(instructions)
    # A plan that reads fx_rates_daily as a source at the order grain and selects no
    # aggregate over it -- its per-currency daily rows fan out and multiply.
    plan = AnalyticsPlan(
        source_refs=[PRIMARY, DIMENSION, FX],
        fields=["recognized_revenue", "region", "usd_rate"],
        joins=[],
        filters=["finance_orders_daily.status = 'completed'"],
        grain="order_date by customer_dim.region",
        checks=["recognized_revenue is non-negative"],
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    (fanout,) = [v for v in result.violations if v.code == "grain_fanout"]
    assert fanout.severity == "error"
    assert fanout.subject == FX
    assert "fan out" in fanout.message
    assert result.status == "invalid"


def test_aggregating_the_fx_source_clears_the_shipped_demo_fanout():
    # REFINE's way out on the shipped demo: aggregate fx to the plan grain -> no fan-out.
    instructions = _demo_instructions()
    bundle = _demo_bundle(instructions)
    plan = AnalyticsPlan(
        source_refs=[PRIMARY, DIMENSION, FX],
        fields=[
            "recognized_revenue",
            "region",
            {"name": "usd_rate", "source_ref": FX, "expression": "SUM(fx_rates_daily.usd_rate)"},
        ],
        joins=[],
        filters=["finance_orders_daily.status = 'completed'"],
        grain="order_date by customer_dim.region",
        checks=["recognized_revenue is non-negative"],
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    assert "grain_fanout" not in [v.code for v in result.violations]
