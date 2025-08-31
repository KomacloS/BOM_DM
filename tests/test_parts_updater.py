from importlib import reload
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session
import pytest

import app.models as models
from app.services.parts import update_part_active_passive


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
