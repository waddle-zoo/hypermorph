"""Offline processor: deterministic rules over persisted evidence (hy-gh-38)."""

from hyperset.processor.engine import ProcessingResult, run_sync_processing
from hyperset.processor.rules import (
    FINDING_TYPES,
    MOVED_SIDES,
    RULE_ID,
    RULE_VERSION,
    RULE_VERSIONS,
    FindingCandidate,
    GitContext,
    ObservedSource,
    approved_expression_drift,
)

__all__ = [
    "FINDING_TYPES",
    "MOVED_SIDES",
    "RULE_ID",
    "RULE_VERSION",
    "RULE_VERSIONS",
    "FindingCandidate",
    "GitContext",
    "ObservedSource",
    "ProcessingResult",
    "approved_expression_drift",
    "run_sync_processing",
]
