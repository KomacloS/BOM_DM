from importlib import reload

from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import app.models as models
from app.services import (
    create_assembly,
    create_customer,
    create_project,
    list_assemblies,
    list_customers,
    list_projects,
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


def test_create_and_list_entities():
    engine = setup_db()
    with Session(engine) as session:
        cust = create_customer("Acme", None, session)
        customers = list_customers(None, session)
        assert customers and customers[0].id == cust.id

        proj = create_project(cust.id, "PRJ", "Project", "med", None, session)
        projects = list_projects(cust.id, session)
        assert projects and projects[0].id == proj.id

        asm = create_assembly(proj.id, "A", None, session)
        assemblies = list_assemblies(proj.id, session)
        assert assemblies and assemblies[0].id == asm.id


def test_create_project_sets_legacy_name():
    engine = setup_db()
    with Session(engine) as session:
        cust = create_customer("Acme", None, session)
        p = create_project(cust.id, "P-001", "Board A", "med", None, session)
        assert p.id
        cols = {c["name"] for c in inspect(engine).get_columns("project")}
        if "name" in cols:
            assert p.name == "Board A"

