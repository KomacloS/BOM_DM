"""Test resolution helpers for selecting board test profiles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from ..models import (
    Assembly,
    BOMItem,
    Part,
    PartTestMap,
    PartType,
    TestMode,
    TestProfile,
)


class ResolutionReason(str, Enum):
    """Reasons explaining how a test profile was chosen."""

    default = "default"
    fallback_equivalent = "fallback_equivalent"
    unresolved = "unresolved"


@dataclass(slots=True)
class ResolvedTest:
    """Resolved test selection for a BOM item."""

    test_id: int | None
    profile_used: TestProfile | None
    reason: ResolutionReason
    message: str | None = None


def _as_part_type(value: PartType | str | None) -> PartType | None:
    if isinstance(value, PartType):
        return value
    if isinstance(value, str):
        try:
            return PartType(value)
        except ValueError:
            return None
    return None


def _as_profile(value: TestProfile | str | None) -> TestProfile | None:
    if isinstance(value, TestProfile):
        return value
    if isinstance(value, str):
        try:
            return TestProfile(value.upper())
        except ValueError:
            return None
    return None


def _assembly_mode(assembly: Assembly | None) -> TestMode:
    mode = getattr(assembly, "test_mode", None)
    if isinstance(mode, TestMode):
        return mode
    if isinstance(mode, str):
        try:
            return TestMode(mode)
        except ValueError:
            pass
    return TestMode.powered


def resolve_test_for_bom_item(
    assembly: Assembly | None,
    bom_item: BOMItem,
    part: Part | None,
    mappings: Sequence[PartTestMap] | Iterable[PartTestMap],
) -> ResolvedTest:
    """Resolve the test profile for a BOM item.

    Parameters
    ----------
    assembly:
        Assembly instance owning the BOM item; the ``test_mode`` determines
        which profile to prefer for active parts.
    bom_item:
        The BOM item being evaluated. Currently used only for diagnostic
        messages.
    part:
        Part referenced by the BOM item. If ``None`` the item is treated as
        unresolved.
    mappings:
        Part↔test mappings available for this part.
    """

    if part is None:
        return ResolvedTest(
            test_id=None,
            profile_used=None,
            reason=ResolutionReason.unresolved,
            message=f"Part missing for BOM item {bom_item.reference}",
        )

    part_type = _as_part_type(getattr(part, "active_passive", None))
    desired_profile = TestProfile.PASSIVE
    if part_type == PartType.passive:
        desired_profile = TestProfile.PASSIVE
    elif part_type == PartType.active:
        mode = _assembly_mode(assembly)
        desired_profile = TestProfile.ACTIVE if mode == TestMode.powered else TestProfile.PASSIVE
    else:
        return ResolvedTest(
            test_id=None,
            profile_used=None,
            reason=ResolutionReason.unresolved,
            message=f"Unknown active/passive classification for part {getattr(part, 'part_number', 'unknown')}",
        )

    normalized: list[tuple[PartTestMap, TestProfile]] = []
    for mapping in mappings:
        profile = _as_profile(getattr(mapping, "profile", None))
        if profile is None:
            continue
        normalized.append((mapping, profile))

    if not normalized:
        msg = f"No mappings configured for part {getattr(part, 'part_number', 'unknown')}"
        return ResolvedTest(None, None, ResolutionReason.unresolved, msg)

    for mapping, profile in normalized:
        if profile == desired_profile:
            return ResolvedTest(mapping.test_id, profile, ResolutionReason.default)

    # Fallback: use another profile if every available mapping points to the same test
    other_candidates = [mp for mp in normalized if mp[1] != desired_profile]
    if other_candidates:
        unique_test_ids = {mp[0].test_id for mp in normalized if mp[0].test_id is not None}
        if len(unique_test_ids) == 1 and unique_test_ids:
            mapping, profile = other_candidates[0]
            return ResolvedTest(
                mapping.test_id,
                profile,
                ResolutionReason.fallback_equivalent,
                message=(
                    f"Using {profile.value} profile for part "
                    f"{getattr(part, 'part_number', 'unknown')}"
                ),
            )

    desired_name = desired_profile.value
    part_label = getattr(part, "part_number", None) or str(getattr(part, "id", "unknown"))
    message = f"Missing {desired_name} mapping for part {part_label}"
    return ResolvedTest(None, None, ResolutionReason.unresolved, message)
