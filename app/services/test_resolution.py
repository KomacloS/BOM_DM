from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from sqlmodel import Session, select

from ..models import (
    BOMItem,
    BOMItemTestOverride,
    Part,
    PartTestMap,
    PartType,
    TestMode,
    TestProfile,
)


def method_label_to_enum(label: str | None) -> str | None:
    """Convert a human-readable method label to its enum identifier."""
    if not label:
        return None
    m = label.strip().lower()
    if m == "macro":
        return "macro"
    if m == "python code":
        return "python"
    if m in ("quick test", "quick test (qt)"):
        return "quick_test"
    if m == "complex":
        return "complex"
    return None


def method_enum_to_label(enum_name: str | None) -> str | None:
    """Map stored enum identifiers to human readable labels."""
    if not enum_name:
        return None
    e = enum_name.strip().lower()
    return {
        "macro": "Macro",
        "python": "Python code",
        "quick_test": "Quick test (QT)",
        "complex": "Complex",
    }.get(e)


@dataclass(slots=True)
class ResolvedTest:
    method: str | None
    detail: str | None
    power_mode: TestMode | None
    source: str
    message: str | None
    test_macro_id: int | None = None
    python_test_id: int | None = None
    powered_method: str | None = None
    powered_detail: str | None = None


class BOMTestResolver:
    """Resolve effective test selections for BOM items across power modes."""

    def __init__(
        self,
        assembly_id: int | None = None,
        bom_items: Optional[Mapping[int, BOMItem]] = None,
        parts: Optional[Mapping[int, Optional[Part]]] = None,
        part_mappings: Optional[Iterable[PartTestMap]] = None,
        overrides: Optional[Iterable[BOMItemTestOverride]] = None,
    ) -> None:
        self._assembly_id = assembly_id or 0
        self._items: Dict[int, BOMItem] = {}
        self._parts_by_item: Dict[int, Optional[Part]] = {}
        self._overrides: Dict[Tuple[int, TestMode], BOMItemTestOverride] = {}
        self._mappings: Dict[Tuple[int, TestMode, TestProfile], PartTestMap] = {}

        if bom_items:
            for key, item in bom_items.items():
                if key is None:
                    continue
                norm_key = self._normalized_key(key)
                self._items[norm_key] = item
        if parts:
            for key, part in parts.items():
                if key is None:
                    continue
                norm_key = self._normalized_key(key)
                self._parts_by_item[norm_key] = part
        if part_mappings:
            for mapping in part_mappings:
                self._register_mapping(mapping)
        if overrides:
            for override in overrides:
                self._register_override(override)

    # ------------------------------------------------------------------
    @classmethod
    def from_session(
        cls,
        session: Session,
        assembly_id: int,
        bom_rows: Iterable[Tuple[BOMItem, Optional[Part]]],
    ) -> "BOMTestResolver":
        resolver = cls()
        resolver._load(session, bom_rows)
        return resolver

    # ------------------------------------------------------------------
    def resolve_effective_test(
        self,
        bom_item_id: int,
        assembly_mode: TestMode,
    ) -> ResolvedTest:
        key = self._normalized_key(bom_item_id)
        item = self._items.get(key)
        if item is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="missing_item",
                message="BOM item not found",
            )

        part = self._parts_by_item.get(key)
        if part is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="missing_part",
                message="Part not linked to BOM item",
            )

        if isinstance(part.active_passive, PartType):
            part_type = part.active_passive
        else:
            try:
                part_type = PartType(str(part.active_passive))
            except ValueError:
                part_type = None

        if part_type is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="unknown_part_type",
                message="Part active/passive classification unavailable",
            )

        powered_preview = self._resolve_for_mode(
            key,
            part,
            TestMode.powered,
            profiles=self._preferred_profiles(part_type, powered=True),
        )

        # Passive parts always resolve unpowered irrespective of assembly mode.
        if part_type is PartType.passive:
            resolved = self._resolve_for_mode(
                bom_item_id,
                part,
                TestMode.unpowered,
                profiles=self._preferred_profiles(part_type, powered=False),
            )
            return self._build_result(
                resolved=resolved,
                fallback_used=False,
                default_mode=TestMode.unpowered,
                powered_preview=powered_preview,
                unresolved_message="No unpowered test mapping for passive part",
            )

        # Active part resolution depends on assembly mode with fallback.
        if assembly_mode is TestMode.powered:
            preferred_modes = (
                (TestMode.powered, self._preferred_profiles(part_type, powered=True)),
                (TestMode.unpowered, self._preferred_profiles(part_type, powered=False)),
            )
        else:
            preferred_modes = (
                (TestMode.unpowered, self._preferred_profiles(part_type, powered=False)),
            )

        for idx, (mode, profiles) in enumerate(preferred_modes):
            resolved = self._resolve_for_mode(key, part, mode, profiles)
            if resolved.record is not None:
                fallback_used = idx > 0
                return self._build_result(
                    resolved=resolved,
                    fallback_used=fallback_used,
                    default_mode=mode,
                    powered_preview=powered_preview,
                    unresolved_message="No test mapping found for active part",
                )

        return ResolvedTest(
            method=None,
            detail=None,
            power_mode=None,
            source="unresolved",
            message="No test mapping found for active part",
            powered_method=powered_preview.method if powered_preview else None,
            powered_detail=powered_preview.detail if powered_preview else None,
        )

    # ------------------------------------------------------------------
    def _load(
        self,
        session: Session,
        bom_rows: Iterable[Tuple[BOMItem, Optional[Part]]],
    ) -> None:
        bom_rows = list(bom_rows)
        bom_ids = []
        part_ids = set()
        for item, maybe_part in bom_rows:
            if item.id is None:
                continue
            bom_ids.append(item.id)
            self._items[item.id] = item
            self._parts_by_item[item.id] = maybe_part
            if maybe_part and maybe_part.id is not None:
                part_ids.add(maybe_part.id)

        if bom_ids:
            stmt = select(BOMItemTestOverride).where(BOMItemTestOverride.bom_item_id.in_(bom_ids))
            for override in session.exec(stmt):
                self._register_override(override)

        if part_ids:
            stmt = select(PartTestMap).where(PartTestMap.part_id.in_(part_ids))
            for mapping in session.exec(stmt):
                self._register_mapping(mapping)

    # ------------------------------------------------------------------
    @staticmethod
    def _preferred_profiles(part_type: PartType, powered: bool) -> Sequence[TestProfile]:
        if part_type is PartType.passive:
            return (TestProfile.passive, TestProfile.active)
        if powered:
            return (TestProfile.active, TestProfile.passive)
        return (TestProfile.passive, TestProfile.active)

    # ------------------------------------------------------------------
    def _register_override(self, override: BOMItemTestOverride) -> None:
        if override.bom_item_id is None:
            return
        mode = self._coerce_mode(override.power_mode or TestMode.unpowered)
        key = self._normalized_key(override.bom_item_id)
        self._overrides[(key, mode)] = override

    def _register_mapping(self, mapping: PartTestMap) -> None:
        if mapping.part_id is None:
            return
        mode = self._coerce_mode(mapping.power_mode or TestMode.unpowered)
        profile = self._coerce_profile(mapping.profile)
        key = self._normalized_key(mapping.part_id)
        self._mappings[(key, mode, profile)] = mapping

    @staticmethod
    def _normalized_key(key: object) -> object:
        try:
            return int(key)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return key

    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_mode(value: TestMode | str) -> TestMode:
        if isinstance(value, TestMode):
            return value
        return TestMode(str(value))

    @staticmethod
    def _coerce_profile(value: TestProfile | str) -> TestProfile:
        if isinstance(value, TestProfile):
            return value
        return TestProfile(str(value))

    # ------------------------------------------------------------------
    class _ResolvedRecord:
        __slots__ = ("record", "mode", "source", "method", "detail", "macro_id", "python_id")

        def __init__(
            self,
            record: PartTestMap | BOMItemTestOverride | None,
            mode: TestMode | None,
            source: str,
            method: Optional[str],
            detail: Optional[str],
            macro_id: Optional[int],
            python_id: Optional[int],
        ) -> None:
            self.record = record
            self.mode = mode
            self.source = source
            self.method = method
            self.detail = detail
            self.macro_id = macro_id
            self.python_id = python_id

    # ------------------------------------------------------------------
    def _resolve_for_mode(
        self,
        bom_item_id: int,
        part: Part,
        mode: TestMode,
        profiles: Sequence[TestProfile],
    ) -> "BOMTestResolver._ResolvedRecord":
        override = self._overrides.get((bom_item_id, mode))
        if override is not None:
            method, detail, macro_id, python_id = self._describe_source(override)
            return self._ResolvedRecord(override, mode, "override", method, detail, macro_id, python_id)

        if part.id is None:
            return self._ResolvedRecord(None, None, "unresolved", None, None, None, None)

        for profile in profiles:
            mapping = self._mappings.get((part.id, mode, profile))
            if mapping is None:
                continue
            method, detail, macro_id, python_id = self._describe_source(mapping)
            return self._ResolvedRecord(mapping, mode, "mapping", method, detail, macro_id, python_id)

        return self._ResolvedRecord(None, None, "unresolved", None, None, None, None)

    # ------------------------------------------------------------------
    @staticmethod
    def _describe_source(
        record: PartTestMap | BOMItemTestOverride,
    ) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[int]]:
        macro_id = getattr(record, "test_macro_id", None)
        python_id = getattr(record, "python_test_id", None)
        detail = getattr(record, "detail", None)
        if macro_id is not None and python_id is None:
            return "Macro", detail, macro_id, None
        if python_id is not None and macro_id is None:
            return "Python code", detail, None, python_id
        return None, detail, macro_id, python_id

    # ------------------------------------------------------------------
    def _build_result(
        self,
        resolved: "BOMTestResolver._ResolvedRecord",
        fallback_used: bool,
        default_mode: TestMode,
        powered_preview: Optional["BOMTestResolver._ResolvedRecord"],
        unresolved_message: str,
    ) -> ResolvedTest:
        if resolved.record is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="unresolved",
                message=unresolved_message,
                powered_method=powered_preview.method if powered_preview else None,
                powered_detail=powered_preview.detail if powered_preview else None,
            )

        source = "fallback" if fallback_used else resolved.source
        message = None
        if fallback_used:
            message = (
                "Used unpowered mapping because powered mapping was unavailable."
                if default_mode is TestMode.unpowered
                else "Used alternate profile for mapping."
            )

        return ResolvedTest(
            method=resolved.method,
            detail=resolved.detail,
            power_mode=resolved.mode or default_mode,
            source=source,
            message=message,
            test_macro_id=resolved.macro_id,
            python_test_id=resolved.python_id,
            powered_method=powered_preview.method if powered_preview else None,
            powered_detail=powered_preview.detail if powered_preview else None,
        )
