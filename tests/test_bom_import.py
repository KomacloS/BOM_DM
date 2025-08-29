from sqlalchemy.pool import StaticPool
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session, select

from importlib import reload

import app.models as models
from app.services import import_bom


def setup_db():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    SQLModel.metadata.create_all(engine)
    return engine


def test_bom_import_creates_parts_and_items():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)
        session.add(models.Part(part_number="P1", description="Known part 1"))
        session.add(models.Part(part_number="P2", description="Known part 2"))
        session.commit()
        csv_bytes = Path("tests/fixtures/sample_bom.csv").read_bytes()
        report = import_bom(asm.id, csv_bytes, session)
        assert report.total == 3
        assert report.matched == 2
        assert report.unmatched == 1
        assert report.created_task_ids == []
        items = session.exec(select(models.BOMItem)).all()
        assert len(items) == 4
        parts = session.exec(select(models.Part)).all()
        assert any(p.part_number == "P3" for p in parts)
