from importlib import reload
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session
import pytest

import app.models as models
from app.services.parts import (
    update_part_active_passive,
    update_part_package,
    update_part_value,
    update_part_tolerances,
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


def test_update_part_active_passive():
    engine = setup_db()
    with Session(engine) as session:
        p = models.Part(part_number="P1")
        session.add(p)
        session.commit(); session.refresh(p)
        updated = update_part_active_passive(session, p.id, "active")
        assert updated.active_passive == models.PartType.active
        session.refresh(p)
        assert p.active_passive == models.PartType.active
        with pytest.raises(ValueError):
            update_part_active_passive(session, p.id, "foo")


def test_update_part_package_value():
    engine = setup_db()
    with Session(engine) as session:
        p = models.Part(part_number="P1")
        session.add(p)
        session.commit(); session.refresh(p)
        update_part_package(session, p.id, "0603")
        update_part_value(session, p.id, "10k")
        session.refresh(p)
        assert p.package == "0603"
        assert p.value == "10k"


def test_update_part_tolerances():
    engine = setup_db()
    with Session(engine) as session:
        p = models.Part(part_number="P1")
        session.add(p)
        session.commit(); session.refresh(p)
        update_part_tolerances(session, p.id, "10", "5")
        session.refresh(p)
        assert p.tol_p == "10" and p.tol_n == "5"
