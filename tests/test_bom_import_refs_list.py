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


def test_reference_list_expansion():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)

        data = (
            "PN,Reference\n"
            "P1,\"R1,R5,R7\"\n"
        ).encode()
        report = import_bom(asm.id, data, session)
        assert report.errors == []

        items = session.exec(select(models.BOMItem)).all()
        refs = {i.reference for i in items}
        assert refs == {"R1", "R5", "R7"}
        assert all(i.qty == 1 for i in items)
