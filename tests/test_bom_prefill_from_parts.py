from __future__ import annotations

from importlib import reload

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models as models
from app.services.bom_read_models import get_joined_bom_for_assembly


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


def test_bom_prefill_from_parts():
    engine = setup_engine()
    with Session(engine) as session:
        customer = models.Customer(name="Customer")
        session.add(customer)
        session.commit(); session.refresh(customer)

        project = models.Project(customer_id=customer.id, code="P", title="Project")
        session.add(project)
        session.commit(); session.refresh(project)

        assembly = models.Assembly(project_id=project.id, rev="A")
        session.add(assembly)
        session.commit(); session.refresh(assembly)

        part = models.Part(
            part_number="PN1",
            description="FromDB",
            package="0603",
            value="0.1uF",
            function="Capacitor",
            active_passive=models.PartType.passive,
            tol_p="+10%",
            tol_n="-10%",
            datasheet_url="http://datasheet",
            product_url="http://product",
        )
        session.add(part)
        session.commit(); session.refresh(part)

        bom_item = models.BOMItem(
            assembly_id=assembly.id,
            part_id=part.id,
            reference="C1",
            qty=2,
            manufacturer="Vendor",
        )
        session.add(bom_item)
        session.commit()

        rows = get_joined_bom_for_assembly(session, assembly.id)
        assert len(rows) == 1
        row = rows[0]

        assert row.description == "FromDB"
        assert row.package == "0603"
        assert row.value == "0.1uF"
        assert row.function == "Capacitor"
        assert row.active_passive == "passive"
        assert row.tol_p == "+10%"
        assert row.tol_n == "-10%"
        assert row.datasheet_url == "http://datasheet"
        assert row.product_url == "http://product"
