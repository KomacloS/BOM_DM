from importlib import reload

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import app.models as models
from app.models import Customer, Project, Assembly, BOMItem, Task
from app.services import (
    create_customer,
    create_project,
    create_assembly,
    delete_customer,
    delete_project,
    DeleteBlockedError,
)


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


def populate(session: Session):
    cust = create_customer("Acme", None, session)
    proj = create_project(cust.id, "PRJ", "Project", "med", None, session)
    asm = create_assembly(proj.id, "A", None, session)
    session.add(BOMItem(assembly_id=asm.id, reference="R1", qty=1))
    session.add(Task(project_id=proj.id, title="t1"))
    session.commit()
    return cust, proj, asm


def test_delete_customer_cascade_blocked():
    engine = setup_db()
    with Session(engine) as session:
        cust, proj, asm = populate(session)
        try:
            delete_customer(cust.id, session)
        except DeleteBlockedError:
            pass
        else:
            assert False, "expected DeleteBlockedError"


def test_delete_customer_cascade_success():
    engine = setup_db()
    with Session(engine) as session:
        cust, proj, asm = populate(session)
        delete_customer(cust.id, session, cascade=True)
        assert session.exec(select(Customer)).all() == []
        assert session.exec(select(Project)).all() == []
        assert session.exec(select(Assembly)).all() == []
        assert session.exec(select(BOMItem)).all() == []
        assert session.exec(select(Task)).all() == []


def test_delete_project_cascade_success():
    engine = setup_db()
    with Session(engine) as session:
        cust, proj, asm = populate(session)
        delete_project(proj.id, session, cascade=True)
        assert session.get(Customer, cust.id) is not None
        assert session.exec(select(Project)).all() == []
        assert session.exec(select(Assembly)).all() == []
        assert session.exec(select(BOMItem)).all() == []
        assert session.exec(select(Task)).all() == []
