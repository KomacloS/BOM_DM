from sqlalchemy.pool import StaticPool
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session, select

from app.models import Customer, Project, Assembly, Part, BOMItem, Task, AuditEvent
from app.services import import_bom


def setup_db():
    engine = create_engine("sqlite://", echo=False, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def test_bom_import_creates_items_and_tasks():
    engine = setup_db()
    with Session(engine) as session:
        cust = Customer(name="Cust")
        session.add(cust)
        session.commit()
        session.refresh(cust)
        proj = Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        asm = Assembly(project_id=proj.id, rev="A")
        session.add(asm)
        session.commit()
        session.refresh(asm)
        session.add(Part(part_number="P1", description="Known part 1"))
        session.add(Part(part_number="P2", description="Known part 2"))
        session.commit()
        csv_bytes = Path("tests/fixtures/sample_bom.csv").read_bytes()
        report = import_bom(session, asm.id, csv_bytes)
        assert report.total == 3
        assert report.matched == 2
        assert report.unmatched == 1
        assert len(report.created_task_ids) == 1
        items = session.exec(select(BOMItem)).all()
        assert len(items) == 3
        task = session.exec(select(Task)).first()
        assert task.title.startswith("Define part P3")
        events = session.exec(select(AuditEvent)).all()
        assert len(events) >= 4
