from pathlib import Path
from typing import List

import json
import pytest
from requests import exceptions as req_exc
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.domain.complex_linker import ComplexLink
from app.models import Assembly, BOMItem, Part, PartTestAssignment, Project, TestMethod
from app.services import export_bom_to_ce_bridge


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return json.loads(json.dumps(self._payload))


class _DummySession:
    def __init__(self):
        self.calls: List[dict] = []
        self._queue: List[object] = []

    def queue(self, response: object) -> None:
        self._queue.append(response)

    def _handle(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._queue:
            raise AssertionError("Unexpected request")
        response = self._queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)


@pytest.fixture()
def sqlite_session():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Project.__table__,
        Assembly.__table__,
        Part.__table__,
        PartTestAssignment.__table__,
        BOMItem.__table__,
        ComplexLink.__table__,
    ]
    for table in tables:
        table.create(engine, checkfirst=True)

    with Session(engine) as session:
        yield session


def _prepare_bom(session: Session) -> tuple[Assembly, BOMItem, BOMItem, BOMItem]:
    project = Project(customer_id=1, code="PRJ", title="Widget")
    session.add(project)
    session.commit()
    session.refresh(project)

    assembly = Assembly(project_id=project.id, rev="A")
    session.add(assembly)
    session.commit()
    session.refresh(assembly)

    part1 = Part(part_number="PN-1")
    part2 = Part(part_number="PN-2")
    part3 = Part(part_number="PN-3")
    session.add_all([part1, part2, part3])
    session.commit()
    session.refresh(part1)
    session.refresh(part2)
    session.refresh(part3)

    for part in (part1, part2, part3):
        session.add(PartTestAssignment(part_id=part.id, method=TestMethod.complex))
    session.commit()

    bom1 = BOMItem(assembly_id=assembly.id, part_id=part1.id, reference="R1", qty=1)
    bom2 = BOMItem(assembly_id=assembly.id, part_id=part2.id, reference="R2", qty=1)
    bom3 = BOMItem(assembly_id=assembly.id, part_id=part3.id, reference="R3", qty=1)
    session.add_all([bom1, bom2, bom3])
    session.commit()
    session.refresh(bom1)
    session.refresh(bom2)
    session.refresh(bom3)

    session.add(ComplexLink(part_id=part1.id, ce_complex_id="100"))
    session.commit()

    return assembly, bom1, bom2, bom3


def _install_component_map(session: Session, mapping: dict[str, int]) -> None:
    session.exec(
        text(
            "CREATE TABLE IF NOT EXISTS ce_component_map (pn TEXT PRIMARY KEY, comp_id INTEGER)"
        )
    )
    for pn, comp_id in mapping.items():
        session.exec(
            text(
                "INSERT OR REPLACE INTO ce_component_map (pn, comp_id) VALUES (:pn, :comp_id)"
            ).bindparams(pn=pn, comp_id=comp_id)
        )
    session.commit()


def test_export_success_with_report(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, _bom1, bom2, bom3 = _prepare_bom(session)
    _install_component_map(session, {"PN-2": 101})

    dummy = _DummySession()
    health_payload = {"ready": True, "headless": False, "allow_headless": True}
    dummy.queue(_DummyResponse(200, health_payload))
    export_payload = {
        "ok": True,
        "export_path": "C:/exports/bom.mdb",
        "exported_comp_ids": [100],
        "missing": [101],
        "unlinked": [],
        "trace_id": "ce-trace",
    }
    dummy.queue(_DummyResponse(200, export_payload))

    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["exported_count"] == 1
    assert result["missing_count"] == 2
    report_path = Path(result["report_path"])
    assert report_path.exists()
    report_lines = report_path.read_text(encoding="utf-8").strip().splitlines()
    assert report_lines[0].split(",")[0] == "bom_id"
    assert any("not_linked_in_CE" in line for line in report_lines[1:])
    assert any("not_found_in_CE" in line for line in report_lines[1:])

    trace_id = result["trace_id"]
    assert dummy.calls[0]["headers"]["X-Trace-Id"] == trace_id
    assert dummy.calls[1]["headers"]["X-Trace-Id"] == trace_id


def test_export_no_comp_ids_creates_failed_input(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, _bom1, _bom2, bom3 = _prepare_bom(session)
    # Remove complex link to ensure no comp_ids remain
    session.exec(text("DELETE FROM complex_links"))
    session.commit()

    dummy = _DummySession()
    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "FAILED_INPUT"
    assert result["missing_count"] == 3
    report_path = Path(result["report_path"])
    assert report_path.exists()
    assert dummy.calls == []


def test_export_headless_returns_retry_later(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, *_ = _prepare_bom(session)

    dummy = _DummySession()
    dummy.queue(_DummyResponse(200, {"ready": True, "headless": True, "allow_headless": False}))

    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "RETRY_LATER"
    assert result["detail"].startswith("Complex Editor exports disabled")
    assert len(dummy.calls) == 1


def test_export_handles_not_found(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, *_ = _prepare_bom(session)

    dummy = _DummySession()
    dummy.queue(_DummyResponse(200, {"ready": True, "headless": False, "allow_headless": True}))
    dummy.queue(_DummyResponse(404, {"reason": "comp_ids_not_found"}))

    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "FAILED_INPUT"
    assert result["missing_count"] >= 1
    assert Path(result["report_path"]).exists()


def test_export_network_error(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, *_ = _prepare_bom(session)

    dummy = _DummySession()
    dummy.queue(req_exc.ConnectionError("boom"))

    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "RETRY_WITH_BACKOFF"
    assert "Network" in result["detail"]


def test_export_template_failure_marks_backend(sqlite_session, monkeypatch, tmp_path):
    session = sqlite_session
    assembly, _bom1, _bom2, _bom3 = _prepare_bom(session)
    _install_component_map(session, {"PN-2": 101})

    dummy = _DummySession()
    dummy.queue(_DummyResponse(200, {"ready": True, "headless": False, "allow_headless": True}))
    dummy.queue(
        _DummyResponse(
            409,
            {
                "reason": "template_missing_or_incompatible",
                "template_path": "C:/templates/custom.mdb",
                "detail": "Template missing",
            },
        )
    )

    monkeypatch.setattr(
        "app.integration.ce_bridge_transport.get_session", lambda: dummy
    )
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.resolve_bridge_connection",
        lambda: ("http://bridge.local", "token", 5.0),
    )

    result = export_bom_to_ce_bridge(session, assembly.id, out_dir=tmp_path)

    assert result["status"] == "FAILED_BACKEND"
    assert result["detail"].startswith("Template missing")
    # Should still surface skipped rows from unresolved components
    if result["report_path"]:
        assert Path(result["report_path"]).exists()
