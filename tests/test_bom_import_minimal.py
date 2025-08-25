from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy.pool import StaticPool
from importlib import reload

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


def test_import_minimal_csv():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)

        data = b"PN,Reference\nP1,R1\n"
        report = import_bom(asm.id, data, session)
        assert report.total == 1 and not report.errors

        parts = session.exec(select(models.Part)).all()
        items = session.exec(select(models.BOMItem)).all()
        assert len(parts) == 1 and parts[0].part_number == "P1"
        assert len(items) == 1 and items[0].qty == 1
