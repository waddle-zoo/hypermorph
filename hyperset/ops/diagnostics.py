"""Maintainer failure-diagnostics classifier (hy-bue7r, V1 gap Integrator/3).

The building blocks already report health -- the readiness overview, the connection probe /
observed-status rollup, a bundle's `resolution.warnings`, and the `invalid_params` error. What
was missing is a single classifier that maps those signals onto the FIVE named maintainer
failure classes so an operator sees WHAT KIND of failure this is and what to do:

    regression        -- something that worked now doesn't: a governed ref the estate no longer
                         observes, or a governed source deleted / prohibited-but-referenced
                         (an error-severity conflict). The governed meaning and the observed
                         world have diverged.
    missing_model     -- a model / embedding / runtime provider is unconfigured or unreachable.
    stale_context     -- context is not synced or not fresh: git-context stale, a source
                         awaiting sync, a reachable-but-stale connection.
    connector_outage  -- an evidence connector (Superset/DataHub), the analytics DB, or the
                         app database is unreachable.
    invalid_input     -- a caller-fixable request: `invalid_params`, or a warning the caller can
                         fix by editing/qualifying (malformed/ambiguous ref, unknown domain).

Pure: it reads already-computed signals (dicts) and returns typed `Diagnosis` rows. It runs no
probe and no SQL of its own -- the admin view gathers the live signals and passes them in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

REGRESSION = "regression"
MISSING_MODEL = "missing_model"
STALE_CONTEXT = "stale_context"
CONNECTOR_OUTAGE = "connector_outage"
INVALID_INPUT = "invalid_input"

# In severity order (worst first) so a caller can render/roll up deterministically.
DIAGNOSTIC_CLASSES = (REGRESSION, CONNECTOR_OUTAGE, MISSING_MODEL, STALE_CONTEXT, INVALID_INPUT)
_CLASS_RANK = {klass: rank for rank, klass in enumerate(DIAGNOSTIC_CLASSES)}

# Which readiness/provider component names are model/runtime vs external-dependency.
_MODEL_COMPONENTS = frozenset({"model", "embeddings", "ollama", "runtime", "openai"})
_DEPENDENCY_COMPONENTS = frozenset({"superset", "datahub", "database", "analytics_db"})

# A component in one of these statuses is a proven failure; `unknown` (never probed) and
# `disabled`/`not_configured` are NOT failures (mirrors ops/readiness.py).
_FAILED_STATUSES = frozenset({"blocked", "degraded"})

# Resolution warning code -> class. Codes absent here are informational, not failures.
_WARNING_CLASS = {
    "ref_awaiting_sync": STALE_CONTEXT,
    "ref_corroborated_late": STALE_CONTEXT,
    "no_context_source": STALE_CONTEXT,
    "ref_not_observed": REGRESSION,
    "evidence_ref_unresolved": REGRESSION,
    "ref_malformed": INVALID_INPUT,
    "ref_ambiguous": INVALID_INPUT,
    "ref_outside_context": INVALID_INPUT,
    "unknown_domain": INVALID_INPUT,
    "domain_ambiguous": INVALID_INPUT,
    "domain_does_not_declare": INVALID_INPUT,
    "multiple_domains": INVALID_INPUT,
    "plan_first_required": INVALID_INPUT,
}


@dataclass(frozen=True)
class Diagnosis:
    """One classified failure. `diagnostic_class` is one of the five; `subject` is what failed
    (a component, source, ref, or the request); `signal` names which building block produced
    it; `detail`/`recovery` are the operator's 'what' and 'what to do' (non-secret, redacted at
    the serving boundary)."""

    diagnostic_class: str
    subject: str
    signal: str
    detail: str
    recovery: str

    def as_dict(self) -> dict:
        return asdict(self)


def _component_class(component: str) -> str:
    if component in _MODEL_COMPONENTS:
        return MISSING_MODEL
    if component == "git_context":
        return STALE_CONTEXT
    return CONNECTOR_OUTAGE  # superset/datahub/database/analytics_db and other dependencies


def _classify_readiness(components) -> list[Diagnosis]:
    rows = []
    for component in components or []:
        status = component.get("status")
        if status not in _FAILED_STATUSES:
            continue
        name = component.get("component", "?")
        rows.append(
            Diagnosis(
                diagnostic_class=_component_class(name),
                subject=name,
                signal="admin_readiness",
                detail=component.get("detail") or f"{name} is {status}",
                recovery=component.get("recovery") or "check this component's configuration",
            )
        )
    return rows


def _classify_observed(sources) -> list[Diagnosis]:
    rows = []
    for source in sources or []:
        status = source.get("status")
        if status not in _FAILED_STATUSES:
            continue
        subject = source.get("display_name") or source.get("connection_id") or "source"
        # Reachable-but-stale is a context-freshness failure; unreachable is an outage.
        stale = source.get("reachable") is True and source.get("fresh") is False
        rows.append(
            Diagnosis(
                diagnostic_class=STALE_CONTEXT if stale else CONNECTOR_OUTAGE,
                subject=subject,
                signal="observed_status",
                detail=source.get("reason") or f"{subject} is {status}",
                recovery=source.get("recovery") or "check the connection's reachability and sync",
            )
        )
    return rows


def _classify_providers(probes) -> list[Diagnosis]:
    rows = []
    for probe in probes or []:
        # Only a CONFIGURED-but-unreachable provider is a failure; unconfigured is not.
        if probe.get("status") != "blocked" or not probe.get("configured"):
            continue
        component = probe.get("component", "?")
        klass = MISSING_MODEL if component in _MODEL_COMPONENTS else CONNECTOR_OUTAGE
        rows.append(
            Diagnosis(
                diagnostic_class=klass,
                subject=component,
                signal="provider_probe",
                detail=probe.get("reason") or f"{component} is unreachable",
                recovery=probe.get("recovery") or "check the provider endpoint and credential",
            )
        )
    return rows


def _classify_warnings(warnings) -> list[Diagnosis]:
    rows = []
    for warning in warnings or []:
        code = warning.get("code")
        klass = _WARNING_CLASS.get(code)
        if klass is None:
            continue  # informational warning, not a failure
        rows.append(
            Diagnosis(
                diagnostic_class=klass,
                subject=code,
                signal="resolution.warnings",
                detail=warning.get("message") or code,
                recovery=(
                    "edit or qualify the request"
                    if klass == INVALID_INPUT
                    else "sync the context source, then re-resolve"
                    if klass == STALE_CONTEXT
                    else "the governed context references something no longer observed; "
                    "reconcile the governed source with the estate"
                ),
            )
        )
    return rows


def _classify_conflicts(conflicts) -> list[Diagnosis]:
    rows = []
    for conflict in conflicts or []:
        if conflict.get("severity") != "error":
            continue  # a warning-severity conflict is disclosed, not a maintainer failure
        kind = conflict.get("kind", "conflict")
        rows.append(
            Diagnosis(
                diagnostic_class=REGRESSION,
                subject=kind,
                signal="linked_evidence.conflicts",
                detail=f"error-severity conflict: {kind}",
                recovery="the governed meaning diverged from the observed source; reconcile it",
            )
        )
    return rows


def _classify_error(error_code) -> list[Diagnosis]:
    if error_code == "invalid_params":
        return [
            Diagnosis(
                diagnostic_class=INVALID_INPUT,
                subject="request",
                signal="invalid_params",
                detail="the request was rejected as invalid input",
                recovery="fix the named parameter and retry",
            )
        ]
    return []


def diagnose(
    *,
    readiness_components=(),
    observed_sources=(),
    provider_probes=(),
    warnings=(),
    conflicts=(),
    error_code=None,
) -> list[Diagnosis]:
    """Classify every failure signal into the five maintainer classes. Deterministic: rows are
    ordered by class severity (regression worst) then subject, so the same signals always
    render the same view."""
    rows = [
        *_classify_readiness(readiness_components),
        *_classify_observed(observed_sources),
        *_classify_providers(provider_probes),
        *_classify_warnings(warnings),
        *_classify_conflicts(conflicts),
        *_classify_error(error_code),
    ]
    return sorted(
        rows, key=lambda row: (_CLASS_RANK[row.diagnostic_class], row.subject, row.signal)
    )
