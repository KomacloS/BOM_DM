from importlib import reload

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import app.models as models
from app.services.customers import create_customer, CustomerExistsError


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


def test_create_customer_is_case_insensitive_unique():
    engine = setup_db()
    with Session(engine) as session:
        c1 = create_customer("Acme", None, session)
        assert c1.id is not None
        with pytest.raises(CustomerExistsError):
            create_customer("acME", None, session)
