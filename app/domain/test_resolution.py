"""
Compatibility shim for legacy imports.

Bridges old resolve_test_for_bom_item() to the new services-level resolver
(app/services/test_resolution.py). Remove once all references are migrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from ..models import Assembly, BOMItem, Part, PartTestMap, TestMode, TestProfile
from ..services.test_resolution import BOMTestResolver


class ResolutionReason(str, Enum):
    default = "default"
    fallback_equivalent = "fallback"
    unresolved = "unresolved"


@dataclass(slots=True)
class ResolvedTest:
    test_id: int | None
    profile_used: TestProfile | None
    reason: ResolutionReason
    message: str | None = None


def resolve_test_for_bom_item(
    assembly: Assembly | None,
    bom_item: BOMItem,
    part: Part | None,
    mappings: Sequence[PartTestMap] | Iterable[PartTestMap],
) -> ResolvedTest:
    """Legacy resolver wrapper using the new BOMTestResolver."""

    mode = getattr(assembly, "test_mode", TestMode.unpowered)
    if not isinstance(mode, TestMode):
        try:
            mode = TestMode(str(mode))
        except Exception:
            mode = TestMode.unpowered

    item_key = getattr(bom_item, "id", None)
    if item_key is None:
        item_key = id(bom_item)

    prepared_mappings = list(mappings) if mappings is not None else []

    resolver = BOMTestResolver(
        assembly_id=getattr(assembly, "id", 0) or 0,
        bom_items={int(item_key): bom_item},
        parts={int(item_key): part},
        part_mappings=prepared_mappings,
        overrides=[],
    )
    resolved = resolver.resolve_effective_test(int(item_key), mode)

    test_id = resolved.test_macro_id or resolved.python_test_id
    profile_used: TestProfile | None = None
    if part is not None:
        if mode is TestMode.powered:
            profile_used = TestProfile.active
        elif mode is TestMode.unpowered:
            profile_used = TestProfile.passive

    if resolved.source in {"unresolved", "missing_part", "missing_item", "unknown_part_type"}:
        reason = ResolutionReason.unresolved
    elif resolved.source == "fallback":
        reason = ResolutionReason.fallback_equivalent
    else:
        reason = ResolutionReason.default

    return ResolvedTest(
        test_id=test_id,
        profile_used=profile_used,
        reason=reason,
        message=resolved.message,
    )
