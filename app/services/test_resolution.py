"""Test resolution helpers for BOM rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

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
from ..domain.complex_linker import ComplexLink


@dataclass(slots=True)
class TestSelection:
    """Single mode test assignment."""

    method: str | None
    detail: str | None
    source: str
    power_mode: TestMode
    test_macro_id: int | None = None
    python_test_id: int | None = None


@dataclass(slots=True)
class ResolvedTest:
    """Effective test selection for a BOM item."""

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
    """Resolve powered/unpowered tests for BOM items using cached lookups."""

    def __init__(
        self,
        assembly_id: int,
        bom_items: Mapping[int, BOMItem],
        parts: Mapping[int, Part | None],
        part_mappings: Iterable[PartTestMap],
        overrides: Iterable[BOMItemTestOverride],
        *,
        ce_linked_parts: Iterable[int] | None = None,
    ) -> None:
        self._assembly_id = assembly_id
        self._bom_items = bom_items
        self._parts = parts
        self._mappings: dict[int, dict[Tuple[TestMode, TestProfile], PartTestMap]] = {}
        for mapping in part_mappings:
            if mapping.part_id is None:
                continue
            key = (mapping.power_mode, mapping.profile)
            self._mappings.setdefault(mapping.part_id, {})[key] = mapping
        self._overrides: dict[int, dict[TestMode, BOMItemTestOverride]] = {}
        for override in overrides:
            self._overrides.setdefault(override.bom_item_id, {})[
                override.power_mode
            ] = override
        self._cache: MutableMapping[Tuple[int, TestMode], TestSelection] = {}
        self._ce_linked_parts: set[int] = set(ce_linked_parts or [])

    @classmethod
    def from_session(
        cls,
        session: Session,
        assembly_id: int,
        bom_rows: Iterable[Tuple[BOMItem, Part | None]],
    ) -> "BOMTestResolver":
        bom_items: Dict[int, BOMItem] = {}
        parts: Dict[int, Part | None] = {}
        part_ids: set[int] = set()
        bom_item_ids: list[int] = []
        for item, part in bom_rows:
            bom_items[item.id] = item
            parts[item.id] = part
            bom_item_ids.append(item.id)
            if part is not None and part.id is not None:
                part_ids.add(part.id)

        mapping_rows: Iterable[PartTestMap] = []
        if part_ids:
            mapping_rows = session.exec(
                select(PartTestMap).where(PartTestMap.part_id.in_(part_ids))
            )

        override_rows: Iterable[BOMItemTestOverride] = []
        if bom_item_ids:
            override_rows = session.exec(
                select(BOMItemTestOverride).where(
                    BOMItemTestOverride.bom_item_id.in_(bom_item_ids)
                )
            )

        ce_linked_parts: set[int] = set()
        if part_ids:
            linked_rows = session.exec(
                select(ComplexLink.part_id).where(ComplexLink.part_id.in_(part_ids))
            ).all()
            for row in linked_rows:
                if isinstance(row, tuple):
                    row = row[0]
                if row is not None:
                    try:
                        ce_linked_parts.add(int(row))
                    except (TypeError, ValueError):
                        continue

        return cls(
            assembly_id,
            bom_items,
            parts,
            mapping_rows,
            override_rows,
            ce_linked_parts=ce_linked_parts,
        )

    def resolve_effective_test(
        self, bom_item_id: int, assembly_test_mode: TestMode
    ) -> ResolvedTest:
        item = self._bom_items.get(bom_item_id)
        if item is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="missing_item",
                message=f"BOM item {bom_item_id} not loaded for assembly {self._assembly_id}",
            )

        part = self._parts.get(bom_item_id)
        part_type = self._part_type_for(part)
        if part is None:
            return ResolvedTest(
                method=None,
                detail=None,
                power_mode=None,
                source="missing_part",
                message=f"Part missing for BOM item {item.reference}",
            )

        powered_selection = self._resolve_for_mode(bom_item_id, TestMode.powered)
        unpowered_selection = self._resolve_for_mode(bom_item_id, TestMode.unpowered)

        if part_type == PartType.passive:
            effective = unpowered_selection
            return ResolvedTest(
                method=effective.method,
                detail=effective.detail,
                power_mode=effective.power_mode,
                source=effective.source,
                message=None if effective.method else self._missing_message(part, TestMode.unpowered),
                test_macro_id=effective.test_macro_id,
                python_test_id=effective.python_test_id,
                powered_method=powered_selection.method,
                powered_detail=powered_selection.detail,
            )

        if part_type == PartType.active:
            target_mode = (
                TestMode.powered if assembly_test_mode == TestMode.powered else TestMode.unpowered
            )
            effective = (
                powered_selection if target_mode == TestMode.powered else unpowered_selection
            )
            message = None
            if effective.method is None:
                message = self._missing_message(part, target_mode)
            return ResolvedTest(
                method=effective.method,
                detail=effective.detail,
                power_mode=effective.power_mode,
                source=effective.source,
                message=message,
                test_macro_id=effective.test_macro_id,
                python_test_id=effective.python_test_id,
                powered_method=powered_selection.method,
                powered_detail=powered_selection.detail,
            )

        return ResolvedTest(
            method=None,
            detail=None,
            power_mode=None,
            source="unknown_part_type",
            message=f"Unknown active/passive classification for part {getattr(part, 'part_number', 'unknown')}",
        )

    def _resolve_for_mode(self, bom_item_id: int, mode: TestMode) -> TestSelection:
        cache_key = (bom_item_id, mode)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        part = self._parts.get(bom_item_id)
        part_id = getattr(part, "id", None)
        if part is None or part_id is None:
            selection = TestSelection(
                method=None,
                detail=None,
                source="missing_part",
                power_mode=mode,
            )
            self._cache[cache_key] = selection
            return selection

        override = self._overrides.get(bom_item_id, {}).get(mode)
        if override:
            selection = TestSelection(
                method=self._method_label(override),
                detail=override.detail,
                source="override",
                power_mode=mode,
                test_macro_id=override.test_macro_id,
                python_test_id=override.python_test_id,
            )
            self._cache[cache_key] = selection
            return selection

        mapping = self._pick_mapping(part_id, part, mode)
        if mapping is not None:
            selection = TestSelection(
                method=self._method_label(mapping),
                detail=mapping.detail,
                source="mapping",
                power_mode=mode,
                test_macro_id=mapping.test_macro_id,
                python_test_id=mapping.python_test_id,
            )
            self._cache[cache_key] = selection
            return selection

        fallback_mode = TestMode.unpowered if mode == TestMode.powered else TestMode.powered
        if mode == TestMode.powered:
            mapping = self._pick_mapping(part_id, part, fallback_mode)
            if mapping is not None:
                selection = TestSelection(
                    method=self._method_label(mapping),
                    detail=mapping.detail,
                    source="fallback",
                    power_mode=mode,
                    test_macro_id=mapping.test_macro_id,
                    python_test_id=mapping.python_test_id,
                )
                self._cache[cache_key] = selection
                return selection

        if part_id in self._ce_linked_parts:
            selection = TestSelection(
                method="Complex",
                detail=None,
                source="complex_link_default",
                power_mode=mode,
            )
            self._cache[cache_key] = selection
            return selection

        selection = TestSelection(
            method=None,
            detail=None,
            source="unresolved",
            power_mode=mode,
        )
        self._cache[cache_key] = selection
        return selection

    def _pick_mapping(
        self, part_id: int, part: Part, mode: TestMode
    ) -> Optional[PartTestMap]:
        by_mode = self._mappings.get(part_id)
        if not by_mode:
            return None

        part_type = self._part_type_for(part)
        profiles = self._profiles_for(part_type, mode)
        for profile in profiles:
            candidate = by_mode.get((mode, profile))
            if candidate is not None:
                return candidate
        if mode == TestMode.powered:
            for profile in profiles:
                candidate = by_mode.get((TestMode.unpowered, profile))
                if candidate is not None:
                    return candidate
        return None

    @staticmethod
    def _method_label(obj: object) -> str | None:
        test_macro_id = getattr(obj, "test_macro_id", None)
        python_test_id = getattr(obj, "python_test_id", None)
        if python_test_id:
            return "Python code"
        if test_macro_id:
            return "Macro"
        return None

    @staticmethod
    def _part_type_for(part: Part | None) -> PartType | None:
        if part is None:
            return None
        part_type = getattr(part, "active_passive", None)
        if isinstance(part_type, PartType):
            return part_type
        if isinstance(part_type, str):
            try:
                return PartType(part_type)
            except ValueError:
                return None
        return None

    @staticmethod
    def _profiles_for(part_type: PartType | None, mode: TestMode) -> Tuple[TestProfile, ...]:
        if part_type == PartType.passive:
            return (TestProfile.PASSIVE,)
        if mode == TestMode.powered:
            return (TestProfile.ACTIVE, TestProfile.PASSIVE)
        return (TestProfile.PASSIVE, TestProfile.ACTIVE)

    @staticmethod
    def _missing_message(part: Part, mode: TestMode) -> str:
        pn = getattr(part, "part_number", None) or str(getattr(part, "id", "unknown"))
        return f"Missing {mode.value} test assignment for part {pn}"


__all__ = ["BOMTestResolver", "ResolvedTest", "TestSelection"]
