from importlib import reload
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session

import app.models as models
from app.services.bom_read_models import get_joined_bom_for_assembly


def setup_db():
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


def test_get_joined_bom_for_assembly_sorting_and_fields():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)
        p1 = models.Part(part_number="P1", description="Part1")
        p2 = models.Part(part_number="P2", description="Part2")
        session.add(p1); session.add(p2)
        session.commit(); session.refresh(p1); session.refresh(p2)
        session.add(models.BOMItem(assembly_id=asm.id, part_id=p1.id, reference="R10", qty=1, manufacturer="M1"))
        session.add(models.BOMItem(assembly_id=asm.id, part_id=p1.id, reference="R1", qty=1, manufacturer="M1"))
        session.add(models.BOMItem(assembly_id=asm.id, part_id=p2.id, reference="R2", qty=1, manufacturer="M2"))
        session.commit()
        rows = get_joined_bom_for_assembly(session, asm.id)
        assert [r.reference for r in rows] == ["R1", "R2", "R10"]
        first = rows[0]
        assert first.part_number == "P1"
        assert first.description == "Part1"
        assert first.manufacturer == "M1"
        assert first.active_passive == "passive"
        assert rows[2].part_id == p1.id
