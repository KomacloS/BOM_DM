from app.models import (
    BOMItem,
    BOMItemTestOverride,
    Part,
    PartTestMap,
    PartType,
    TestMode,
    TestProfile,
)
from app.services.test_resolution import BOMTestResolver


def _bom_item(part_id: int = 1) -> BOMItem:
    return BOMItem(id=1, assembly_id=1, part_id=part_id, reference="U1", qty=1)


def _part(part_type: PartType, pn: str = "P1", pid: int = 1) -> Part:
    return Part(id=pid, part_number=pn, active_passive=part_type)


def _resolver(
    item: BOMItem,
    part: Part,
    mappings: list[PartTestMap],
    overrides: list[BOMItemTestOverride] | None = None,
    *,
    ce_linked_parts: list[int] | None = None,
) -> BOMTestResolver:
    return BOMTestResolver(
        assembly_id=item.assembly_id,
        bom_items={item.id: item},
        parts={item.id: part},
        part_mappings=mappings,
        overrides=overrides or [],
        ce_linked_parts=ce_linked_parts,
    )


def test_passive_part_ignores_mode():
    part = _part(PartType.passive)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.unpowered,
        profile=TestProfile.PASSIVE,
        test_macro_id=42,
        detail="Passive test",
    )
    resolver = _resolver(bom, part, [mapping])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.powered)

    assert resolved.method == "Macro"
    assert resolved.detail == "Passive test"
    assert resolved.power_mode == TestMode.unpowered


def test_active_powered_prefers_powered_mapping():
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    powered_mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.powered,
        profile=TestProfile.ACTIVE,
        test_macro_id=7,
        detail="Powered",
    )
    resolver = _resolver(bom, part, [powered_mapping])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.powered)

    assert resolved.method == "Macro"
    assert resolved.detail == "Powered"
    assert resolved.power_mode == TestMode.powered


def test_active_unpowered_falls_back_to_unpowered_mapping():
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    unpowered_mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.unpowered,
        profile=TestProfile.PASSIVE,
        test_macro_id=11,
        detail="Unpowered",
    )
    resolver = _resolver(bom, part, [unpowered_mapping])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.unpowered)

    assert resolved.method == "Macro"
    assert resolved.detail == "Unpowered"
    assert resolved.power_mode == TestMode.unpowered


def test_override_takes_precedence():
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.powered,
        profile=TestProfile.ACTIVE,
        test_macro_id=5,
        detail="Mapping",
    )
    override = BOMItemTestOverride(
        bom_item_id=bom.id,
        power_mode=TestMode.powered,
        python_test_id=9,
        detail="Override",
    )
    resolver = _resolver(bom, part, [mapping], [override])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.powered)

    assert resolved.method == "Python code"
    assert resolved.detail == "Override"
    assert resolved.power_mode == TestMode.powered


def test_powered_fallback_to_unpowered_mapping_for_active():
    part = _part(PartType.active, pid=123)
    bom = _bom_item(part_id=part.id)
    unpowered_mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.unpowered,
        profile=TestProfile.PASSIVE,
        test_macro_id=777,
        detail="UnpoweredOnly",
    )
    resolver = _resolver(bom, part, [unpowered_mapping])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.powered)

    assert resolved.method == "Macro"
    assert resolved.detail == "UnpoweredOnly"
    assert resolved.source in ("fallback", "mapping")


def test_unpowered_override_precedence_for_active():
    part = _part(PartType.active, pid=124)
    bom = _bom_item(part_id=part.id)
    unpowered_mapping = PartTestMap(
        part_id=part.id,
        power_mode=TestMode.unpowered,
        profile=TestProfile.ACTIVE,
        test_macro_id=123,
        detail="Mapped",
    )
    override = BOMItemTestOverride(
        bom_item_id=bom.id,
        power_mode=TestMode.unpowered,
        python_test_id=999,
        detail="OverrideDetail",
    )
    resolver = _resolver(bom, part, [unpowered_mapping], [override])

    resolved = resolver.resolve_effective_test(bom.id, TestMode.unpowered)

    assert resolved.method == "Python code"
    assert resolved.detail == "OverrideDetail"
    assert resolved.power_mode == TestMode.unpowered
    assert resolved.source == "override"


def test_complex_link_default_when_unmapped():
    part = _part(PartType.active, pid=200)
    bom = _bom_item(part_id=part.id)
    resolver = _resolver(
        bom,
        part,
        [],
        [],
        ce_linked_parts=[part.id],
    )

    resolved = resolver.resolve_effective_test(bom.id, TestMode.powered)

    assert resolved.method == "Complex"
    assert resolved.detail is None
    assert resolved.source == "complex_link_default"
