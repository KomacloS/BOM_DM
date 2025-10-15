from app.domain.test_resolution import (
    ResolutionReason,
    resolve_test_for_bom_item,
)
from app.models import (
    Assembly,
    BOMItem,
    Part,
    PartTestMap,
    PartType,
    TestMode,
    TestProfile,
)


def _assembly(mode: TestMode) -> Assembly:
    return Assembly(id=1, project_id=1, rev="A", test_mode=mode)


def _bom_item(part_id: int = 1) -> BOMItem:
    return BOMItem(id=1, assembly_id=1, part_id=part_id, reference="U1", qty=1)


def _part(part_type: PartType, pn: str = "P1", pid: int = 1) -> Part:
    return Part(id=pid, part_number=pn, active_passive=part_type)


def test_passive_part_resolves_passive():
    asm = _assembly(TestMode.powered)
    part = _part(PartType.passive)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(part_id=part.id, test_id=42, profile=TestProfile.PASSIVE)

    resolved = resolve_test_for_bom_item(asm, bom, part, [mapping])

    assert resolved.reason == ResolutionReason.default
    assert resolved.profile_used == TestProfile.PASSIVE
    assert resolved.test_id == 42


def test_active_powered_prefers_active():
    asm = _assembly(TestMode.powered)
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(part_id=part.id, test_id=7, profile=TestProfile.ACTIVE)

    resolved = resolve_test_for_bom_item(asm, bom, part, [mapping])

    assert resolved.reason == ResolutionReason.default
    assert resolved.profile_used == TestProfile.ACTIVE
    assert resolved.test_id == 7


def test_active_non_powered_prefers_passive():
    asm = _assembly(TestMode.non_powered)
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(part_id=part.id, test_id=11, profile=TestProfile.PASSIVE)

    resolved = resolve_test_for_bom_item(asm, bom, part, [mapping])

    assert resolved.reason == ResolutionReason.default
    assert resolved.profile_used == TestProfile.PASSIVE
    assert resolved.test_id == 11


def test_fallback_equivalent_when_only_passive_available():
    asm = _assembly(TestMode.powered)
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    mapping = PartTestMap(part_id=part.id, test_id=9, profile=TestProfile.PASSIVE)

    resolved = resolve_test_for_bom_item(asm, bom, part, [mapping])

    assert resolved.reason == ResolutionReason.fallback_equivalent
    assert resolved.profile_used == TestProfile.PASSIVE
    assert resolved.test_id == 9


def test_unresolved_when_profiles_conflict():
    asm = _assembly(TestMode.powered)
    part = _part(PartType.active)
    bom = _bom_item(part_id=part.id)
    mappings = [
        PartTestMap(part_id=part.id, test_id=1, profile=TestProfile.PASSIVE),
        PartTestMap(part_id=part.id, test_id=2, profile=TestProfile.PASSIVE),
    ]

    resolved = resolve_test_for_bom_item(asm, bom, part, mappings)

    assert resolved.reason == ResolutionReason.unresolved
    assert resolved.profile_used is None
    assert "Missing ACTIVE" in (resolved.message or "")
