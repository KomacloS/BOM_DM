from importlib import reload

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from sqlmodel import SQLModel, Session

import app.models as models
from app.services import list_bom_items


def setup_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    from app.domain import complex_linker as linker

    reload(linker)
    SQLModel.metadata.create_all(engine)
    return engine


def test_unpowered_active_excludes_powered_fields():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit()
        session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A", test_mode=models.TestMode.unpowered)
        session.add(asm)
        session.commit()
        session.refresh(asm)
        macro = models.TestMacro(name="Macro")
        session.add(macro)
        session.commit()
        session.refresh(macro)
        part = models.Part(part_number="P1", active_passive=models.PartType.active)
        session.add(part)
        session.commit()
        session.refresh(part)
        mapping = models.PartTestMap(
            part_id=part.id,
            power_mode=models.TestMode.unpowered,
            profile=models.TestProfile.ACTIVE,
            test_macro_id=macro.id,
            detail="Unpowered",
        )
        session.add(mapping)
        item = models.BOMItem(
            assembly_id=asm.id,
            part_id=part.id,
            reference="R1",
            qty=1,
            is_fitted=True,
        )
        session.add(item)
        session.commit()

        items = list_bom_items(asm.id, session)
        assert len(items) == 1
        row = items[0]
        assert row.test_method == "Macro"
        assert row.test_detail == "Unpowered"
        assert row.test_method_powered is None
        assert row.test_detail_powered is None


def test_powered_active_includes_powered_fields():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit()
        session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A", test_mode=models.TestMode.powered)
        session.add(asm)
        session.commit()
        session.refresh(asm)
        macro = models.TestMacro(name="Macro")
        session.add(macro)
        session.commit()
        session.refresh(macro)
        part = models.Part(part_number="P1", active_passive=models.PartType.active)
        session.add(part)
        session.commit()
        session.refresh(part)
        session.add_all(
            [
                models.PartTestMap(
                    part_id=part.id,
                    power_mode=models.TestMode.unpowered,
                    profile=models.TestProfile.ACTIVE,
                    test_macro_id=macro.id,
                    detail="Unpowered",
                ),
                models.PartTestMap(
                    part_id=part.id,
                    power_mode=models.TestMode.powered,
                    profile=models.TestProfile.ACTIVE,
                    test_macro_id=macro.id,
                    detail="Powered",
                ),
            ]
        )
        item = models.BOMItem(
            assembly_id=asm.id,
            part_id=part.id,
            reference="U1",
            qty=1,
            is_fitted=True,
        )
        session.add(item)
        session.commit()

        items = list_bom_items(asm.id, session)
        assert len(items) == 1
        row = items[0]
        assert row.test_method == "Macro"
        assert row.test_detail == "Powered"
        assert row.test_method_powered == "Macro"
        assert row.test_detail_powered == "Powered"


def test_powered_passive_omits_powered_fields():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit()
        session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A", test_mode=models.TestMode.powered)
        session.add(asm)
        session.commit()
        session.refresh(asm)
        macro = models.TestMacro(name="Macro")
        session.add(macro)
        session.commit()
        session.refresh(macro)
        part = models.Part(part_number="P1", active_passive=models.PartType.passive)
        session.add(part)
        session.commit()
        session.refresh(part)
        mapping = models.PartTestMap(
            part_id=part.id,
            power_mode=models.TestMode.unpowered,
            profile=models.TestProfile.PASSIVE,
            test_macro_id=macro.id,
            detail="Passive",
        )
        session.add(mapping)
        item = models.BOMItem(
            assembly_id=asm.id,
            part_id=part.id,
            reference="R1",
            qty=1,
            is_fitted=True,
        )
        session.add(item)
        session.commit()

        items = list_bom_items(asm.id, session)
        assert len(items) == 1
        row = items[0]
        assert row.test_method == "Macro"
        assert row.test_detail == "Passive"
        assert row.test_method_powered is None
        assert row.test_detail_powered is None
