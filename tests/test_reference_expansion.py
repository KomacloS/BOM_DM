from importlib import reload

from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy.pool import StaticPool

import app.models as models
from app.services import import_bom


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


def test_reference_expansion():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)

        data = (
            "PN,Reference,Qty\n"
            "P1,R1-R3,999\n"
            "P2,\"R5,R7\",\n"
            "P3,R9,10\n"
        ).encode()
        report = import_bom(asm.id, data, session)
        assert report.total == 3
        assert "qty=999 but 3 references expanded" in "\n".join(report.errors)

        parts = session.exec(select(models.Part)).all()
        assert {p.part_number for p in parts} == {"P1", "P2", "P3"}

        items = session.exec(select(models.BOMItem)).all()
        pmap = {p.id: p.part_number for p in parts}
        items_by_part = {}
        for item in items:
            pn = pmap.get(item.part_id)
            items_by_part.setdefault(pn, []).append(item)

        refs_p1 = {i.reference for i in items_by_part["P1"]}
        assert refs_p1 == {"R1", "R2", "R3"}
        assert all(i.qty == 1 for i in items_by_part["P1"])

        refs_p2 = {i.reference for i in items_by_part["P2"]}
        assert refs_p2 == {"R5", "R7"}
        assert all(i.qty == 1 for i in items_by_part["P2"])

        refs_p3 = {i.reference for i in items_by_part["P3"]}
        assert refs_p3 == {"R9"}
        assert items_by_part["P3"][0].qty == 10

        assert sum(1 for p in parts if p.part_number == "P1") == 1
        assert sum(1 for p in parts if p.part_number == "P2") == 1
