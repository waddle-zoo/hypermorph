"""A local PII guard on two operational write-backs (hy-hbtz).

A redaction filter, and ONLY that. It sanitizes text at exactly two boundaries
before it is persisted or committed -- the miss-log query and the proposed
manifest content -- using Microsoft Presidio, LOCALLY, with no external network
call. It never approves, merges, writes governed context, or runs SQL, and it
NEVER sits on a model-input path: the assist model reasons over the operator's
question unchanged (ADR 0019's exclusion, affirmed by the Overseer).

CONFIG (env, off by default so the library default breaks nothing):
- HYPERSET_PII_GUARD -- the master switch. Off unless set to on/1/true/yes.
- HYPERSET_PII_ACTION -- `redact` (default) or `block`.
- HYPERSET_PII_ENTITIES -- comma-separated entity types; empty = Presidio defaults.

FAIL CLOSED, by construction: when the guard is ENGAGED and Presidio cannot be
hosted (the extra absent, or its spaCy model missing), `guard_text` RAISES
rather than return unredacted text. There is no code path where "engaged but
Presidio missing" persists or commits plaintext. Default-off is not fail-open --
it is the absence of a request for the control; a public deployment engages it.

Presidio is an OPTIONAL EXTRA (`pii`). Installing it is necessary but not
sufficient: the English pipeline needs a spaCy NLP model that is a separate
install, so the seat may have the package and still not host the guard -- exactly
the claude-agent-sdk hostability split. `presidio_available()` detects both gaps
LOCALLY, with no network egress: the model's presence is a `spacy.util.is_package`
check, and the engine is pinned to that model so Presidio's own auto-download
branch (`spacy.cli.download`) is never reached. A model-absent seat fails closed
WITHOUT phoning home, so the "runs locally, no external call" premise holds on a
seat with outbound access too (hy-wp8e).
"""

from __future__ import annotations

from hyperset.config.feature_settings import (
    pii_action,
    pii_entities,
    pii_guard,
    pii_spacy_model,
)

# The loaded (analyzer, anonymizer) pair, or False when Presidio cannot be
# hosted, or None before the one-time probe. Cached because constructing the
# analyzer loads an NLP model. Tests that stage absence reset it to None.
_engines: tuple | bool | None = None


class PiiError(Exception):
    """Base for the guard's fail-closed refusals."""


class PiiGuardUnavailable(PiiError):
    """The guard is engaged but Presidio cannot be hosted, so the boundary fails
    closed rather than persist or commit unredacted text."""


class PiiBlocked(PiiError):
    """The guard is configured to block on PII and found some."""


def _engaged() -> bool:
    # Through the settings object (loaded config, else the lenient legacy env), so the guard's
    # master switch resolves one way on both write-back boundaries (hy-e16vx).
    return pii_guard()


def _action() -> str:
    return "block" if (pii_action() or "").strip().lower() == "block" else "redact"


def _configured_entities() -> list[str] | None:
    raw = pii_entities() or ""
    entities = [item.strip() for item in raw.split(",") if item.strip()]
    return entities or None


# The spaCy model Presidio's default English pipeline expects. Pinned explicitly
# (rather than left to Presidio's default provider) so the capability probe is a
# LOCAL `is_package` check and Presidio never reaches its auto-download branch.
_DEFAULT_SPACY_MODEL = "en_core_web_lg"


def _spacy_model() -> str:
    return (pii_spacy_model() or "").strip() or _DEFAULT_SPACY_MODEL


def _load_engines():
    """The analyzer/anonymizer pair, or False when unhostable. Probed once, with
    ZERO network egress -- a PURELY LOCAL capability check.

    `find_spec` is wrapped because the extras-guard's blocking finder RAISES
    `ModuleNotFoundError` rather than returning None.

    The spaCy model is a SEPARATE install, and Presidio's default `AnalyzerEngine`
    silently DOWNLOADS it when absent (`spacy.cli.download`, a ~400MB network
    fetch) -- which would contradict this guard's "runs locally, no external
    call" premise (hy-wp8e). So the model's presence is checked here with a local
    `spacy.util.is_package`; if it is absent the guard fails closed WITHOUT any
    download, and the NLP engine is pinned to that exact model so Presidio's own
    auto-download branch is never reached (`is_package` is True by the time it
    loads).
    """
    global _engines
    if _engines is not None:
        return _engines
    try:
        import importlib.util

        try:
            if importlib.util.find_spec("presidio_analyzer") is None:
                _engines = False
                return _engines
        except ModuleNotFoundError:
            _engines = False
            return _engines

        import spacy.util

        model = _spacy_model()
        if not spacy.util.is_package(model):
            # Model absent: fail closed here, before constructing anything that
            # could phone home. No `spacy.cli.download`, no network.
            _engines = False
            return _engines

        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": model}],
            }
        ).create_engine()
        _engines = (AnalyzerEngine(nlp_engine=nlp_engine), AnonymizerEngine())
    except Exception:  # noqa: BLE001 -- any hosting gap fails closed, not open
        _engines = False
    return _engines


def presidio_available() -> bool:
    """True only where Presidio imports AND its NLP model constructs. A seat with
    the package but no model returns False, so the guard fails closed there."""
    return _load_engines() is not False


def guard_text(text: str, *, boundary: str) -> str:
    """The one boundary filter: redact (or block) PII in `text`, or fail closed.

    Returns `text` unchanged when the guard is not engaged. When engaged: raises
    `PiiGuardUnavailable` if Presidio cannot be hosted; returns the redacted text
    (default); or raises `PiiBlocked` when action=block and PII is present.
    `boundary` labels the error so an operator can tell the miss-log from the
    proposal.
    """
    if not _engaged():
        return text
    engines = _load_engines()
    if engines is False:
        raise PiiGuardUnavailable(
            f"the PII guard is engaged (HYPERSET_PII_GUARD) but Presidio is not hostable on this "
            f"seat, so the {boundary} boundary fails closed rather than write unredacted text: "
            "install the 'pii' extra and its spaCy model, or set HYPERSET_PII_GUARD=off for "
            "non-public use"
        )
    analyzer, anonymizer = engines
    results = analyzer.analyze(text=text, language="en", entities=_configured_entities())
    if not results:
        return text
    if _action() == "block":
        found = ", ".join(sorted({result.entity_type for result in results}))
        raise PiiBlocked(f"the {boundary} boundary is configured to block on PII and found {found}")
    return anonymizer.anonymize(text=text, analyzer_results=results).text
