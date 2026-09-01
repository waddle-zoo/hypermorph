"""`make up-demo` demonstrates a REAL reconciliation conflict AND a real
processor finding, deterministically (hy-u26p conflict half, hy-y1ng8 finding half).

A clean `make up-demo` used to run no connector sync, so `linked_evidence.conflicts` was
always empty and an adopter saw none of the reconciliation value the docs describe. hy-u26p
wired a HERMETIC observed sync (a checked-in Superset export bundle -- no live Superset, no
keys) into up-demo so the revenue domain's `prohibited_but_referenced` conflict fires through
the genuine resolve path. hy-y1ng8 adds the producer end: the SAME estate re-exported with one
metric drifted, re-observed on the same connection, run through the REAL offline processor so a
real `Finding` and a human `ReviewTask` appear -- not a direct-seeded row.

The resolve BEHAVIOUR is proven end-to-end by
`tests/postgres/test_context_bundle.py::test_a_prohibited_source_the_estate_still_references_travels_as_a_conflict`
and the processor BEHAVIOUR by
`tests/postgres/test_demo_processor_finding.py`, both against these same fixtures. THIS gate
test binds the demo WIRING to fixtures whose observed content actually satisfies each
precondition, so repointing the demo at a silent fixture (a prohibited dataset nothing
references, or a "drift" bundle that does not drift the approved expression) or dropping a step
turns it red.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from hyperset.connectors.superset.bundle import load_export_bundle

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text()
REVENUE_MANIFEST = yaml.safe_load(
    (ROOT / "playground" / "examples" / "revenue" / "manifest.yaml").read_text()
)

_DATASET_REF = re.compile(r"^superset:dataset:(?P<uuid>[0-9a-fA-F-]+)$")


def _prohibited_bi_override_dataset_uuids() -> set[str]:
    """The Superset dataset uuids the revenue manifest PROHIBITS (via a prohibited source's
    bi_override) -- the observed identities that, if the estate still references them, are a
    `prohibited_but_referenced` conflict."""
    uuids = set()
    for entry in REVENUE_MANIFEST.get("prohibited_sources", []):
        override = (entry or {}).get("bi_override") or {}
        match = _DATASET_REF.match(str(override.get("ref", "")))
        if match:
            uuids.add(match.group("uuid"))
    return uuids


def _approved_bi_override_field() -> tuple[str, str, str]:
    """The (dataset uuid, field name, approved expression) for a manifest FIELD whose
    approved source carries a bi_override to a Superset dataset -- the drift processor
    compares Git's approved expression against what that observed dataset computes."""
    override_by_source = {}
    for entry in REVENUE_MANIFEST.get("approved_sources", []):
        override = (entry or {}).get("bi_override") or {}
        match = _DATASET_REF.match(str(override.get("ref", "")))
        if match:
            override_by_source[entry["ref"]] = match.group("uuid")
    for field in REVENUE_MANIFEST.get("fields", []):
        uuid = override_by_source.get(field.get("source_ref"))
        if uuid:
            return uuid, field["name"], field["expression"]
    raise AssertionError("the revenue manifest declares no field over a bi_override dataset")


def _bundle_metric_expression(bundle_path: Path, dataset_uuid: str, metric_name: str) -> str:
    bundle = load_export_bundle(bundle_path)
    dataset = next(d for d in bundle["datasets"] if str(d.get("uuid")) == dataset_uuid)
    metric = next(m for m in dataset["metrics"] if m.get("metric_name") == metric_name)
    return str(metric["expression"])


def _staged_baseline_bundle() -> Path:
    """The Superset export bundle up-demo STAGES for `playground-observed` (the baseline
    the conflict is proven against), resolved from its checked-in fixture source."""
    observed = re.search(r"^playground-observed:\n(?P<body>(?:\t.*\n?)+)", MAKEFILE, re.MULTILINE)
    assert observed, "a `playground-observed` target must exist"
    match = re.search(
        r"cp\s+(\S+official-export\.zip)\s+\.runtime/observed-bundle\.zip", observed.group("body")
    )
    assert match, "playground-observed must stage the baseline official-export.zip bundle"
    return ROOT / match.group(1)


def _staged_drift_bundle() -> Path:
    """The DRIFTED re-export `playground-finding` observes on the same connection."""
    finding = re.search(r"^playground-finding:\n(?P<body>(?:\t.*\n?)+)", MAKEFILE, re.MULTILINE)
    assert finding, "a `playground-finding` target must exist"
    match = re.search(
        r"cp\s+(\S+official-export-drift\.zip)\s+\.runtime/observed-bundle\.zip",
        finding.group("body"),
    )
    assert match, "playground-finding must stage the drifted official-export-drift.zip bundle"
    return ROOT / match.group(1)


def test_up_demo_observes_before_it_syncs_the_context_then_finds():
    # The ordering IS the fixture: the bi_override only corroborates (the evidence ref the
    # drift rule reads) when the dataset is observed BEFORE the context sync. And the finding
    # is the drift re-export, which must come after both. A reorder that breaks either
    # relationship makes the processor find nothing -- so the order is asserted, not assumed.
    up_demo = re.search(r"^up-demo:.*\n(?P<body>(?:\t.*\n|\t.*$|#.*\n)+)", MAKEFILE, re.MULTILINE)
    assert up_demo, "up-demo target must exist"
    body = up_demo.group("body")
    observed = body.index("$(MAKE) playground-observed")
    contexts = body.index("$(MAKE) playground-contexts")
    finding = body.index("$(MAKE) playground-finding")
    assert observed < contexts < finding, (
        "up-demo must observe, THEN sync the context (so the bi_override corroborates), "
        "THEN drift+process -- observed the drift processor cannot see otherwise"
    )


def test_up_demo_waits_for_the_real_superset_dependency_chain():
    up_demo = re.search(r"^up-demo:.*\n(?P<body>(?:\t.*\n|\t.*$|#.*\n)+)", MAKEFILE, re.MULTILINE)
    assert up_demo, "up-demo target must exist"
    assert "docker compose --profile demo up -d --wait superset" in up_demo.group("body")


def test_the_finding_step_runs_the_real_processor_over_the_drift_sync():
    finding = re.search(r"^playground-finding:\n(?P<body>(?:\t.*\n?)+)", MAKEFILE, re.MULTILINE)
    assert finding, "a `playground-finding` target must exist"
    body = finding.group("body")
    # It re-observes on the SAME connection (looked up by the observed display name) and
    # runs the REAL processor over that sync -- not a seeded row.
    assert "connections list" in body
    assert "Playground: observed (Superset bundle)" in body
    assert "sync run" in body
    assert "process sync" in body


def test_the_demo_bundle_makes_a_prohibited_source_referenced():
    # The baseline wiring must point at a fixture whose observed content DISAGREES with the
    # governed manifest: a prohibited bi_override dataset is present AND a chart queries it
    # (the two conditions `prohibited_but_referenced` joins on). A silent fixture (no referring
    # chart) would make up-demo show an empty conflicts list again -- so this is load-bearing.
    prohibited = _prohibited_bi_override_dataset_uuids()
    assert prohibited, "the revenue manifest must prohibit at least one bi_override dataset"

    fixture = _staged_baseline_bundle()
    assert fixture.is_file(), f"the demo observed-sync fixture is missing: {fixture}"
    bundle = load_export_bundle(fixture)
    observed_datasets = {str(d.get("uuid")) for d in bundle["datasets"]}
    charted_datasets = {str(c.get("dataset_uuid")) for c in bundle["charts"]}

    # A prohibited dataset that the fixture both OBSERVES and CHARTS -> the conflict fires.
    referenced_prohibited = prohibited & observed_datasets & charted_datasets
    assert referenced_prohibited, (
        "the up-demo observed bundle must OBSERVE a prohibited bi_override dataset AND carry a "
        "chart that queries it, so prohibited_but_referenced fires; "
        f"prohibited={sorted(prohibited)} observed={sorted(observed_datasets)} "
        f"charted={sorted(charted_datasets)}"
    )


def test_the_drift_bundle_actually_drifts_the_approved_expression():
    # The finding is only real if the drift bundle DISAGREES with the approved expression
    # while the baseline AGREES: repointing playground-finding at a non-drift fixture must
    # red, or the demo would show a finding the source change did not produce.
    dataset_uuid, field_name, approved = _approved_bi_override_field()

    baseline = _staged_baseline_bundle()
    drift = _staged_drift_bundle()
    assert drift.is_file(), f"the demo drift fixture is missing: {drift}"

    baseline_expr = _bundle_metric_expression(baseline, dataset_uuid, field_name)
    drift_expr = _bundle_metric_expression(drift, dataset_uuid, field_name)

    # Baseline matches Git: observing it first corroborates at the approved expression, so
    # `moved.side` is `observed` (the source, not Git, left the link point).
    assert baseline_expr == approved, (
        f"the baseline bundle must compute {field_name!r} as the manifest approves "
        f"({approved!r}), got {baseline_expr!r}"
    )
    # The drift bundle moves it, so the processor produces the finding.
    assert drift_expr != approved, (
        f"the drift bundle must change {field_name!r} away from the approved expression "
        f"{approved!r}; got {drift_expr!r} (no drift = no finding)"
    )
    assert drift_expr != baseline_expr
