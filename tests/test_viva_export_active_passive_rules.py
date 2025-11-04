from __future__ import annotations

from importlib import reload
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models as models
from app.services import collect_bom_lines, perform_viva_export


@pytest.fixture()
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    from app.domain import complex_linker as linker

    reload(linker)
    SQLModel.metadata.create_all(engine)
    return engine


def _prep_common_data(session: Session):
    customer = models.Customer(name="Customer")
    session.add(customer)
    session.commit(); session.refresh(customer)

    project = models.Project(customer_id=customer.id, code="P", title="Project")
    session.add(project)
    session.commit(); session.refresh(project)

    powered = models.Assembly(project_id=project.id, rev="A", test_mode=models.TestMode.powered)
    unpowered = models.Assembly(project_id=project.id, rev="B", test_mode=models.TestMode.unpowered)
    session.add(powered)
    session.add(unpowered)
    session.commit(); session.refresh(powered); session.refresh(unpowered)

    passive = models.Part(
        part_number="P-PASS",
        description="Passive",
        active_passive=models.PartType.passive,
    )
    active = models.Part(
        part_number="P-ACT",
        description="Active",
        active_passive=models.PartType.active,
    )
    active_same = models.Part(
        part_number="P-ACT-SAME",
        description="ActiveSame",
        active_passive=models.PartType.active,
    )
    session.add(passive); session.add(active); session.add(active_same)
    session.commit()
    session.refresh(passive); session.refresh(active); session.refresh(active_same)

    macro_passive = models.TestMacro(name="Passive Macro")
    macro_active = models.TestMacro(name="Active Macro")
    session.add(macro_passive)
    session.add(macro_active)
    session.commit(); session.refresh(macro_passive); session.refresh(macro_active)

    passive_map = models.PartTestMap(
        part_id=passive.id,
        power_mode=models.TestMode.unpowered,
        profile=models.TestProfile.PASSIVE,
        test_macro_id=macro_passive.id,
        detail="Passive detail",
    )
    active_power_map = models.PartTestMap(
        part_id=active.id,
        power_mode=models.TestMode.powered,
        profile=models.TestProfile.ACTIVE,
        test_macro_id=macro_active.id,
        detail="Active powered",
    )
    active_passive_map = models.PartTestMap(
        part_id=active.id,
        power_mode=models.TestMode.unpowered,
        profile=models.TestProfile.PASSIVE,
        test_macro_id=macro_passive.id,
        detail="Active passive",
    )
    same_power_map = models.PartTestMap(
        part_id=active_same.id,
        power_mode=models.TestMode.powered,
        profile=models.TestProfile.ACTIVE,
        test_macro_id=macro_active.id,
        detail="Same detail",
    )
    same_passive_map = models.PartTestMap(
        part_id=active_same.id,
        power_mode=models.TestMode.unpowered,
        profile=models.TestProfile.PASSIVE,
        test_macro_id=macro_active.id,
        detail="Same detail",
    )
    session.add_all([
        passive_map,
        active_power_map,
        active_passive_map,
        same_power_map,
        same_passive_map,
    ])
    session.commit()

    def _add_bom(assembly_id: int, part: models.Part, ref: str):
        item = models.BOMItem(
            assembly_id=assembly_id,
            part_id=part.id,
            reference=ref,
            qty=1,
            is_fitted=True,
        )
        session.add(item)
        session.commit(); session.refresh(item)

    _add_bom(powered.id, passive, "R1")
    _add_bom(powered.id, active, "U1")
    _add_bom(powered.id, active_same, "U2")

    _add_bom(unpowered.id, passive, "R1")
    _add_bom(unpowered.id, active, "U1")
    _add_bom(unpowered.id, active_same, "U2")

    return {
        "powered": powered,
        "unpowered": unpowered,
        "passive": passive,
        "active": active,
        "active_same": active_same,
    }


def _export_rows(parts: dict[str, models.Part]):
    return [
        {
            "reference": "R1",
            "quantity": "1",
            "part_number": parts["passive"].part_number,
            "function": "Passive",
            "value": "",
            "toln": "",
            "tolp": "",
        },
        {
            "reference": "U1",
            "quantity": "1",
            "part_number": parts["active"].part_number,
            "function": "Active",
            "value": "",
            "toln": "",
            "tolp": "",
        },
        {
            "reference": "U2",
            "quantity": "1",
            "part_number": parts["active_same"].part_number,
            "function": "Active",
            "value": "",
            "toln": "",
            "tolp": "",
        },
    ]


def _patch_ce(monkeypatch):
    from app.integration import ce_bridge_client

    def _wait_ready(**_kwargs):
        return {"ready": True}

    def _export(comp_ids, out_dir, *, mdb_name="bom_complexes.mdb", **_kwargs):
        Path(out_dir, mdb_name).write_text("stub", encoding="utf-8")
        return {"exported": len(comp_ids), "trace_id": "trace"}

    monkeypatch.setattr(ce_bridge_client, "wait_until_ready", _wait_ready)
    monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", _export)
    monkeypatch.setattr(ce_bridge_client, "lookup_complex_ids", lambda pns: ({}, []))


def test_viva_export_active_passive_rules(sqlite_engine, tmp_path, monkeypatch):
    _patch_ce(monkeypatch)

    with Session(sqlite_engine) as session:
        parts = _prep_common_data(session)

        powered_rows = _export_rows(parts)
        powered_result = perform_viva_export(
            session,
            parts["powered"].id,
            base_dir=tmp_path / "powered",
            bom_rows=powered_rows,
        )

        unpowered_rows = _export_rows(parts)
        unpowered_result = perform_viva_export(
            session,
            parts["unpowered"].id,
            base_dir=tmp_path / "unpowered",
            bom_rows=unpowered_rows,
        )

        lines_powered = collect_bom_lines(session, parts["powered"].id)
        lines_unpowered = collect_bom_lines(session, parts["unpowered"].id)

    def _find(lines, pn):
        for line in lines:
            if line.part_number == pn:
                return line
        raise AssertionError(f"Part {pn} not found")

    passive_powered = _find(lines_powered, parts["passive"].part_number)
    passive_unpowered = _find(lines_unpowered, parts["passive"].part_number)
    assert passive_powered.test_method == "Macro"
    assert passive_unpowered.test_method == "Macro"
    assert passive_powered.test_detail == "Passive detail"
    assert passive_unpowered.test_detail == "Passive detail"

    active_powered = _find(lines_powered, parts["active"].part_number)
    active_unpowered = _find(lines_unpowered, parts["active"].part_number)
    assert active_powered.test_method == "Macro"
    assert active_powered.test_detail == "Active powered"
    assert active_unpowered.test_method == "Macro"
    assert active_unpowered.test_detail == "Active passive"

    same_powered = _find(lines_powered, parts["active_same"].part_number)
    same_unpowered = _find(lines_unpowered, parts["active_same"].part_number)
    assert same_powered.test_detail == same_unpowered.test_detail == "Same detail"

    for outcome in (powered_result, unpowered_result):
        assert outcome.paths.folder.exists()
        assert outcome.paths.bom_txt.exists()
        assert outcome.paths.mdb_path.exists()
