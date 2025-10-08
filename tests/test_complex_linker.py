import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain import complex_linker
from app.domain.complex_linker import ComplexLink
from app.integration.ce_bridge_client import (
    CENetworkError,
    CEUserCancelled,
    CEWizardUnavailable,
)


@pytest.fixture
def sqlite_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def new_session():
        SQLModel.metadata.create_all(engine)
        ComplexLink.__table__.create(engine, checkfirst=True)
        return Session(engine)

    monkeypatch.setattr(complex_linker.database, "new_session", new_session)
    return engine


def _capture_updates(monkeypatch, first_payload, second_payload=None):
    payloads = iter([first_payload, second_payload or first_payload])
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda _ce_id: next(payloads),
    )


def test_attach_existing_complex_inserts_and_updates(monkeypatch, sqlite_engine):
    payload1 = {
        "id": "ce-1",
        "db_path": "C:/complex1.mdb",
        "aliases": ["ALIAS"],
        "pin_map": {"1": "A"},
        "macro_ids": ["M1"],
        "source_hash": "abc",
    }
    payload2 = {
        "id": "ce-1",
        "db_path": "C:/complex1_v2.mdb",
        "aliases": ["ALT"],
        "pin_map": {"2": "B"},
        "macro_ids": ["M2"],
        "source_hash": "xyz",
    }

    timestamps = iter(["2024-01-01T00:00:00", "2024-01-02T00:00:00"])
    monkeypatch.setattr(complex_linker, "_utc_iso", lambda: next(timestamps))
    _capture_updates(monkeypatch, payload1, payload2)

    complex_linker.attach_existing_complex(5, "ce-1")
    complex_linker.attach_existing_complex(5, "ce-1")

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink)).one()
        assert link.part_id == 5
        assert link.ce_complex_id == "ce-1"
        assert link.ce_db_uri == "C:/complex1_v2.mdb"
        assert link.aliases == '["ALT"]'
        assert link.pin_map == '{"2": "B"}'
        assert link.macro_ids == '["M2"]'
        assert link.source_hash == "xyz"
        assert link.synced_at == "2024-01-02T00:00:00"


def test_create_and_attach_complex(monkeypatch, sqlite_engine):
    created = {"id": "ce-99"}
    detail = {
        "id": "ce-99",
        "db_path": "D:/complex99.mdb",
        "aliases": [],
        "pin_map": {},
        "macro_ids": [],
        "source_hash": None,
    }
    monkeypatch.setattr(complex_linker.ce_bridge_client, "create_complex", lambda pn, aliases=None: created)
    monkeypatch.setattr(complex_linker.ce_bridge_client, "get_complex", lambda _id: detail)
    monkeypatch.setattr(complex_linker, "_utc_iso", lambda: "2024-03-01T12:00:00")

    outcome = complex_linker.create_and_attach_complex(7, "PN-777")
    assert outcome.status == "attached"
    assert outcome.created_id == "ce-99"

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink).where(ComplexLink.part_id == 7)).one()
        assert link.ce_complex_id == "ce-99"
        assert link.ce_db_uri == "D:/complex99.mdb"
        assert link.synced_at == "2024-03-01T12:00:00"


def test_create_and_attach_complex_wizard(monkeypatch, sqlite_engine):
    def raise_wizard(_pn, _aliases=None):
        raise CEWizardUnavailable("wizard handler unavailable")

    monkeypatch.setattr(
        complex_linker.ce_bridge_client, "create_complex", raise_wizard
    )

    launched: list[tuple[str, list[str]]] = []

    def fake_launch(pn: str, aliases):
        launched.append((pn, list(aliases)))

    monkeypatch.setattr(complex_linker, "launch_ce_wizard", fake_launch)

    outcome = complex_linker.create_and_attach_complex(3, "PN-300", ["ALT"])
    assert outcome.status == "wizard"
    assert launched == [("PN-300", ["ALT"])]


def test_create_and_attach_complex_cancelled(monkeypatch, sqlite_engine):
    def raise_cancel(_pn, _aliases=None):
        raise CEUserCancelled("cancelled")

    monkeypatch.setattr(
        complex_linker.ce_bridge_client, "create_complex", raise_cancel
    )

    outcome = complex_linker.create_and_attach_complex(4, "PN-400")
    assert outcome.status == "cancelled"


def test_auto_link_by_pn_success_and_network(monkeypatch):
    attached = []

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: [
            {"id": "ce-1", "pn": "pn-123", "aliases": ["alt-1"]},
            {"id": "ce-2", "pn": "other", "aliases": []},
        ],
    )
    monkeypatch.setattr(
        complex_linker,
        "attach_existing_complex",
        lambda part_id, ce_id: attached.append((part_id, ce_id)),
    )

    result = complex_linker.auto_link_by_pn(11, "PN-123")
    assert result is True
    assert attached == [(11, "ce-1")]

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: (_ for _ in ()).throw(CENetworkError("offline")),
    )
    attached.clear()
    assert complex_linker.auto_link_by_pn(11, "PN-123") is False
    assert attached == []
