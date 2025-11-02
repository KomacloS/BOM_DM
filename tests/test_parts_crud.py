from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app import services
from app.models import Assembly, BOMItem, Customer, Part, Project


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_search_and_update_part():
    with make_session() as session:
        part = services.create_part(session, part_number="PN-001", description="Widget")
        results = services.search_parts(session, "", limit=10)
        assert any(p.id == part.id for p in results)

        updated = services.update_part(session, part.id, package="0603")
        assert updated.package == "0603"


def test_delete_part_with_references():
    with make_session() as session:
        customer = Customer(name="Acme")
        session.add(customer)
        session.commit()
        session.refresh(customer)

        project = Project(customer_id=customer.id, code="P1", title="Project")
        session.add(project)
        session.commit()
        session.refresh(project)

        assembly = Assembly(project_id=project.id, rev="A")
        session.add(assembly)
        session.commit()
        session.refresh(assembly)

        part = services.create_part(session, part_number="PN-REF", description="Ref part")

        item = BOMItem(assembly_id=assembly.id, part_id=part.id, reference="R1", qty=1)
        session.add(item)
        session.commit()
        session.refresh(item)

        assert services.count_part_references(session, part.id) == 1

        with pytest.raises(RuntimeError):
            services.delete_part(session, part.id, mode="block")

        services.delete_part(session, part.id, mode="unlink_then_delete")

        refreshed_item = session.get(BOMItem, item.id)
        assert refreshed_item is not None
        assert refreshed_item.part_id is None
        assert session.get(Part, part.id) is None
