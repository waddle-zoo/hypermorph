"""Adapter-based context ingestion (epic #283).

The landed pieces are the closed transform whitelist (hy-qswh) and the mapping
file parser+validator (hy-9keh); the apply/ingest step, the loss-disclosure
contract, and the eval-scorer seam land as later sub-beads. Nothing here is a
served contract -- the adapter is a customer-side file this package reads, never a
Hyperset store of meaning (ADR 0028).
"""

from hyperset.context.adapter.apply import (
    AdapterApplyError,
    apply_adapter,
    has_adapter,
)
from hyperset.context.adapter.schema import (
    AdapterSpec,
    AdapterValidationError,
    Definitions,
    Mapping,
    TransformStep,
    parse_adapter,
)
from hyperset.context.adapter.transforms import (
    TRANSFORM_NAMES,
    TRANSFORMS,
    TransformError,
    UnknownTransform,
    apply_transform,
    catalog_urn_to_source_ref,
    default,
    one_line,
    prefix,
    slug,
)

__all__ = [
    "TRANSFORMS",
    "TRANSFORM_NAMES",
    "AdapterApplyError",
    "AdapterSpec",
    "AdapterValidationError",
    "Definitions",
    "Mapping",
    "TransformError",
    "TransformStep",
    "UnknownTransform",
    "apply_adapter",
    "apply_transform",
    "catalog_urn_to_source_ref",
    "default",
    "has_adapter",
    "one_line",
    "parse_adapter",
    "prefix",
    "slug",
]
