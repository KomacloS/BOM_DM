from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.domain import complex_linker
from app.domain.complex_creation import WizardLaunchResult
from app.domain.complex_linker import CEWizardLaunchError, ComplexLink
from app.integration.ce_bridge_client import (
    CENetworkError,
    CEUserCancelled,
    CEWizardUnavailable,
)
from app.integration.ce_bridge_manager import CEBridgeError
from app.models import Part, PartTestAssignment, TestMethod


@pytest.fixture
def sqlite_engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ComplexLink.__table__,
            Part.__table__,
            PartTestAssignment.__table__,
        ],
    )

    def new_session():
        SQLModel.metadata.create_all(
            engine,
            tables=[
                ComplexLink.__table__,
                Part.__table__,
                PartTestAssignment.__table__,
            ],
        )
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

    with Session(sqlite_engine) as session:
        session.add(Part(id=5, part_number="PN-5"))
        session.commit()

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


def test_create_complex_launches_gui(monkeypatch):
    captured: list[tuple[str, list[str]]] = []
    buffer_path = Path("/tmp/ce-buffer.json")
    launch_result = WizardLaunchResult(
        pn="PN-777",
        aliases=["ALT"],
        buffer_path=buffer_path,
    )

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "wait_until_ready",
        lambda **_: {"ready": True},
    )
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None: (_ for _ in ()).throw(
            CEWizardUnavailable("wizard handler unavailable")
        ),
    )
    monkeypatch.setattr(
        complex_linker.config,
        "get_complex_editor_settings",
        lambda: {"bridge": {"enabled": True}},
    )

    def fake_launch(pn: str, aliases):
        cleaned = [a.strip() for a in aliases if isinstance(a, str) and a.strip()]
        captured.append((pn, cleaned))
        return launch_result

    monkeypatch.setattr(complex_linker.complex_creation, "launch_wizard", fake_launch)

    outcome = complex_linker.create_and_attach_complex(7, "PN-777", ["ALT", " "])
    assert outcome.status == "wizard"
    assert outcome.pn == "PN-777"
    assert outcome.aliases == ["ALT"]
    assert outcome.buffer_path == str(buffer_path)
    assert outcome.polling_enabled is True
    assert captured == [("PN-777", ["ALT"])]


def test_create_complex_launch_disabled_poll(monkeypatch):
    launch_result = WizardLaunchResult(
        pn="PN-200",
        aliases=[],
        buffer_path=Path("/tmp/buf.json"),
    )

    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "wait_until_ready",
        lambda **_: {"ready": True},
    )
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None: (_ for _ in ()).throw(
            CEWizardUnavailable("wizard handler unavailable")
        ),
    )
    monkeypatch.setattr(
        complex_linker.config,
        "get_complex_editor_settings",
        lambda: {"bridge": {"enabled": False}},
    )
    monkeypatch.setattr(
        complex_linker.complex_creation, "launch_wizard", lambda pn, aliases: launch_result
    )

    outcome = complex_linker.create_and_attach_complex(2, "PN-200")
    assert outcome.status == "wizard"
    assert outcome.polling_enabled is False


def test_create_complex_launch_error_flags_fix(monkeypatch):
    monkeypatch.setattr(
        complex_linker.config,
        "get_complex_editor_settings",
        lambda: {},
    )
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "wait_until_ready",
        lambda **_: {"ready": True},
    )
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "create_complex",
        lambda pn, aliases=None: (_ for _ in ()).throw(
            CEWizardUnavailable("wizard handler unavailable")
        ),
    )

    def fake_launch(_pn: str, _aliases: list[str]):
        raise CEBridgeError("Complex Editor executable not found: C:/missing.exe")

    monkeypatch.setattr(complex_linker.complex_creation, "launch_wizard", fake_launch)

    with pytest.raises(CEWizardLaunchError) as excinfo:
        complex_linker.create_and_attach_complex(1, "PN-100")

    assert excinfo.value.fix_in_settings is True


def test_create_complex_creates_and_links(monkeypatch, sqlite_engine):
    created_payload = {
        "id": 321,
        "db_path": "C:/ce321.mdb",
        "aliases": ["ALT321"],
        "pin_map": {"1": "A"},
        "macro_ids": ["M321"],
        "source_hash": "hash321",
    }
    _capture_updates(monkeypatch, created_payload)

    waits: list[dict] = []

    def fake_wait(**kwargs):
        waits.append(kwargs)
        return {"ready": True}

    created_calls: list[tuple[str, list[str] | None]] = []

    def fake_create(pn: str, aliases=None):
        created_calls.append((pn, aliases))
        return {"id": 321}

    monkeypatch.setattr(complex_linker.ce_bridge_client, "wait_until_ready", fake_wait)
    monkeypatch.setattr(complex_linker.ce_bridge_client, "create_complex", fake_create)
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_active_base_url",
        lambda: "http://127.0.0.1:8765",
    )
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "search_complexes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("search should not be called")),
    )

    with Session(sqlite_engine) as session:
        session.add(Part(id=10, part_number="PN-100"))
        session.commit()

    outcome = complex_linker.create_and_attach_complex(10, "PN-100", [" ALT ", " "])
    assert outcome.status == "attached"
    assert outcome.created_id == "321"
    assert created_calls == [("PN-100", ["ALT"])]
    assert waits and waits[0] == {}

    with Session(sqlite_engine) as session:
        link = session.exec(select(ComplexLink)).one()
        assert link.part_id == 10
        assert link.ce_complex_id == "321"
        assert link.ce_db_uri == "C:/ce321.mdb"
        assignment = session.get(PartTestAssignment, 10)
        assert assignment is not None
        assert assignment.method == TestMethod.complex


def test_create_complex_cancelled(monkeypatch, sqlite_engine):
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "wait_until_ready",
        lambda **_: {"ready": True},
    )

    def fake_cancel(*_args, **_kwargs):
        raise CEUserCancelled("cancelled")

    monkeypatch.setattr(complex_linker.ce_bridge_client, "create_complex", fake_cancel)
    monkeypatch.setattr(
        complex_linker.ce_bridge_client,
        "get_complex",
        lambda _ce_id: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    with Session(sqlite_engine) as session:
        session.add(Part(id=20, part_number="PN-200"))
        session.commit()

    outcome = complex_linker.create_and_attach_complex(20, "PN-200")
    assert outcome.status == "cancelled"
    assert outcome.message.lower().startswith("creation cancelled")

    with Session(sqlite_engine) as session:
        links = session.exec(select(ComplexLink)).all()
        assert links == []
        assignments = session.exec(select(PartTestAssignment)).all()
        assert assignments == []


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
