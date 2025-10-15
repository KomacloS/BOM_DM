from importlib import reload

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import StaticPool

import app.models as models
from app.services import import_bom, list_bom_items


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


def test_list_items_includes_part_number():
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

        data = (
            "PN,Reference\n"
            "P1,R1\n"
        ).encode()
        report = import_bom(asm.id, data, session)
        assert report.errors == []

        items = list_bom_items(asm.id, session)
        assert len(items) == 1
        assert items[0].part_number == "P1"
        assert items[0].resolution_reason == "unresolved"
        assert items[0].resolved_profile is None
