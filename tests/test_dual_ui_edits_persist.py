from __future__ import annotations

from importlib import reload

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models as models
from app.services.parts import update_part


def setup_engine():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    from app.domain import complex_linker as linker

    reload(linker)
    SQLModel.metadata.create_all(engine)
    return engine


def test_dual_ui_edits_persist():
    engine = setup_engine()

    with Session(engine) as session:
        part = models.Part(part_number="PN-EDIT", package="0603", value="0.1uF")
        session.add(part)
        session.commit(); session.refresh(part)
        part_id = part.id

    with Session(engine) as session:
        update_part(session, part_id, package="0805")

    with Session(engine) as session:
        update_part(session, part_id, value="1uF")

    with Session(engine) as session:
        refreshed = session.get(models.Part, part_id)
        assert refreshed is not None
        assert refreshed.package == "0805"
        assert refreshed.value == "1uF"
