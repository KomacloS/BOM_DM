from importlib import reload

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import StaticPool

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
    SQLModel.metadata.create_all(engine)
    return engine


def test_bom_list_includes_part_number():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)
        part = models.Part(part_number="P1")
        session.add(part); session.commit(); session.refresh(part)
        for ref in ["R1", "R2", "R3"]:
            item = models.BOMItem(assembly_id=asm.id, reference=ref, part_id=part.id, qty=1)
            session.add(item)
        session.commit()

        items = list_bom_items(asm.id, session)
        assert len(items) == 3
        assert all(it.part_number == "P1" for it in items)
        assert all(it.test_resolution_source == "unresolved" for it in items)
        assert all(it.test_method is None for it in items)
