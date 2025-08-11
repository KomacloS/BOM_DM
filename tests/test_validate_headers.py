import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool
from importlib import reload

import app.models as models
from app.services import validate_headers, import_bom


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


def test_validate_headers_errors():
    with pytest.raises(ValueError):
        validate_headers(["foo", "bar"])
    with pytest.raises(ValueError):
        validate_headers(["part_number", "description", "qty"])  # missing reference


def test_import_bom_bad_headers():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust)
        session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)
        report = import_bom(asm.id, b"bad,header\n", session)
        assert report.errors
