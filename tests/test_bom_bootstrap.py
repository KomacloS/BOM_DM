from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app import services
from app.models import (
    Assembly,
    BOMItem,
    Customer,
    Part,
    PartTestMap,
    PartType,
    Project,
    TestMacro,
    TestMode,
    TestProfile,
)


def _setup_database():
    engine = create_engine("sqlite:///:memory:", future=True)
    SQLModel.metadata.create_all(engine)
    return engine


def test_list_bom_items_mode_switch() -> None:
    engine = _setup_database()
    with Session(engine) as session:
        customer = Customer(name="ACME")
        session.add(customer)
        session.commit()
        session.refresh(customer)

        project = Project(customer_id=customer.id, code="P1", title="Project")
        session.add(project)
        session.commit()
        session.refresh(project)

        assembly = Assembly(project_id=project.id, rev="A")
        session.add(assembly)
        session.commit()
        session.refresh(assembly)

        part = Part(
            part_number="P-100",
            description="Test part",
            active_passive=PartType.active,
        )
        session.add(part)
        session.commit()
        session.refresh(part)

        bom_item = BOMItem(assembly_id=assembly.id, part_id=part.id, reference="R1", qty=1)
        session.add(bom_item)

        macro = TestMacro(name="MAC-1")
        session.add(macro)
        session.commit()
        session.refresh(macro)

        session.add_all(
            [
                PartTestMap(
                    part_id=part.id,
                    power_mode=TestMode.unpowered,
                    profile=TestProfile.passive,
                    test_macro_id=macro.id,
                    detail="U",
                ),
                PartTestMap(
                    part_id=part.id,
                    power_mode=TestMode.powered,
                    profile=TestProfile.active,
                    test_macro_id=macro.id,
                    detail="P",
                ),
            ]
        )
        session.commit()

        items = services.list_bom_items(assembly.id, session)
        assert len(items) == 1
        row = items[0]
        assert row.test_method == "Macro"
        assert row.test_detail == "U"
        assert row.test_method_powered is None
        assert row.test_detail_powered is None

        services.update_assembly_test_mode(session, assembly.id, "powered")
        items = services.list_bom_items(assembly.id, session)
        row = items[0]
        assert row.test_method == "Macro"
        assert row.test_detail == "P"
        assert row.test_method_powered == "Macro"
        assert row.test_detail_powered == "P"
