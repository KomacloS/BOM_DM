import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain import complex_linker
from app.domain.complex_linker import ComplexLink
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CENetworkError, CENotFound


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
        "pn": "PN-1",
        "aliases": ["ALIAS"],
        "pin_map": {"1": "A"},
        "macro_ids": ["M1"],
        "source_hash": "abc",
        "total_pins": 24,
    }
    payload2 = {
        "id": "ce-1",
        "db_path": "C:/complex1_v2.mdb",
        "pn": "PN-1",
        "aliases": ["ALT"],
        "pin_map": {"2": "B"},
        "macro_ids": ["M2"],
        "source_hash": "xyz",
        "total_pins": 30,
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
        assert link.ce_pn == "PN-1"
        assert link.aliases == '["ALT"]'
        assert link.pin_map == '{"2": "B"}'
        assert link.macro_ids == '["M2"]'
        assert link.source_hash == "xyz"
        assert link.total_pins == 30
        assert link.synced_at == "2024-01-02T00:00:00"


def test_create_and_attach_complex(monkeypatch, sqlite_engine):
    created = {"id": "ce-99"}
    detail = {
        "id": "ce-99",
        "db_path": "D:/complex99.mdb",
        "pn": "PN-777",
        "aliases": [],
        "pin_map": {},
        "macro_ids": [],
        "source_hash": None,
        "total_pins": 18,
    }
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None, status_callback=None: created,
    )
    monkeypatch.setattr(complex_linker.ce_bridge_client, "get_complex", lambda _id: detail)
    monkeypatch.setattr(complex_linker, "_utc_iso", lambda: "2024-03-01T12:00:00")

    record = complex_linker.create_and_attach_complex(7, "PN-777")
    assert record["ce_complex_id"] == "ce-99"

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink).where(ComplexLink.part_id == 7)).one()
        assert link.ce_complex_id == "ce-99"
        assert link.ce_db_uri == "D:/complex99.mdb"
        assert link.ce_pn == "PN-777"
        assert link.total_pins == 18
        assert link.synced_at == "2024-03-01T12:00:00"


def test_create_and_attach_without_id_triggers_selection(monkeypatch):
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None, status_callback=None: {},
    )
    matches = [
        {"id": "ce-1", "pn": "PN-123", "aliases": []},
        {"id": "ce-2", "pn": "PN-123", "aliases": []},
    ]
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda pn, limit=10: matches,
    )
    with pytest.raises(complex_linker.CESelectionRequired) as exc:
        complex_linker.create_and_attach_complex(1, "PN-123")
    assert exc.value.matches == matches


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
        lambda part_id, ce_id: (attached.append((part_id, ce_id)) or {'ce_complex_id': ce_id}),
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


def test_unlink_existing_complex(monkeypatch, sqlite_engine):
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda _ce_id: {
            "id": "ce-77",
            "db_path": "C:/x.mdb",
            "pn": "PN-77",
            "aliases": [],
            "pin_map": {},
            "macro_ids": [],
            "source_hash": None,
            "total_pins": 64,
        },
    )
    complex_linker.attach_existing_complex(9, "ce-77")

    assert complex_linker.unlink_existing_complex(9) is True
    assert complex_linker.unlink_existing_complex(9) is False

    with Session(sqlite_engine) as session:
        assert session.exec(select(ComplexLink).where(ComplexLink.part_id == 9)).first() is None


def test_check_link_stale_not_found(monkeypatch):
    class DummyClient:
        def get_complex(self, ce_id):
            raise CENotFound("gone")

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is True
    assert reason == "not_found"


def test_check_link_stale_transient(monkeypatch):
    class DummyClient:
        def get_complex(self, ce_id):
            raise CENetworkError("offline")

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is False
    assert reason == "transient"


def test_check_link_stale_ok(monkeypatch):
    class DummyClient:
        def get_complex(self, ce_id):
            return {"id": ce_id}

    stale, reason = complex_linker.check_link_stale(DummyClient(), {"ce_complex_id": "ce-1"})
    assert stale is False
    assert reason == ""
