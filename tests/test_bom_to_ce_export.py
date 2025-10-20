import json
from pathlib import Path

import subprocess
from types import SimpleNamespace

import pytest
from requests import exceptions as req_exc
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import importlib

import app.models as models
from app.models import Part
from app.domain.complex_linker import ComplexLink
from app.services.bom_to_ce_export import (
    STATUS_PARTIAL_SUCCESS,
    STATUS_RETRY_LATER,
    STATUS_RETRY_WITH_BACKOFF,
    export_bom_to_ce_bridge,
)
from app.integration import ce_bridge_client, ce_bridge_transport
from app.integration.ce_supervisor import CESupervisor


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        if payload is None:
            self.content = b""
            self._text = ""
        else:
            serialized = json.dumps(payload)
            self.content = serialized.encode("utf-8")
            self._text = serialized
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON body")
        if isinstance(self._payload, dict):
            return dict(self._payload)
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = False

    def _next(self):
        if not self.responses:
            raise AssertionError("No responses configured")
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next()


class FailingSession:
    def __init__(self, exception):
        self._exception = exception
        self.calls = []
        self.trust_env = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        raise self._exception

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        raise self._exception


def setup_db():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    importlib.reload(models)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_basic_bom(session: Session) -> tuple[models.Assembly, Part]:
    customer = models.Customer(name="ACME")
    session.add(customer)
    session.commit()
    project = models.Project(customer_id=customer.id, code="PRJ", title="Widget")
    session.add(project)
    session.commit()
    assembly = models.Assembly(project_id=project.id, rev="A")
    session.add(assembly)
    session.commit()
    part = Part(part_number="PN123")
    session.add(part)
    session.commit()
    bom = models.BOMItem(assembly_id=assembly.id, part_id=part.id, reference="R1", qty=1, is_fitted=True)
    session.add(bom)
    session.commit()
    return assembly, part


@pytest.fixture(autouse=True)
def _reset_sessions():
    ce_bridge_transport.reset_session()
    yield
    ce_bridge_transport.reset_session()


def _build_rows(*rows):
    return list(rows)


def _prepare_table_rows(part_number: str, reference: str = "R1") -> dict:
    return {
        "reference": reference,
        "part_number": part_number,
        "test_method": "Complex",
        "is_fitted": True,
    }


def test_export_bom_to_ce_bridge_partial_success(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        session.add(ComplexLink(part_id=part.id, ce_complex_id="101"))
        extra_part = Part(part_number="PN999")
        session.add(extra_part)
        session.commit()
        session.add(
            models.BOMItem(
                assembly_id=assembly.id,
                part_id=extra_part.id,
                reference="R2",
                qty=1,
                is_fitted=True,
            )
        )
        session.commit()

        rows_gui = _build_rows(
            _prepare_table_rows(part.part_number, "R1"),
            _prepare_table_rows(extra_part.part_number, "R2"),
        )
        assembly_id = assembly.id

    health = DummyResponse(200, {"ready": True, "headless": False})
    export_payload = {
        "exported_comp_ids": ["101"],
        "missing": ["202"],
        "unlinked": ["303"],
        "export_path": str((tmp_path / "CE" / "bom_complexes.mdb").resolve()),
    }
    export_resp = DummyResponse(200, export_payload)
    http_session = FakeSession(health, export_resp)

    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: http_session)

    # use fresh DB session for export call
    with Session(engine) as session:
        result = export_bom_to_ce_bridge(
            session,
            assembly_id,
            bom_rows=rows_gui,
            export_dir=tmp_path / "ce-out",
            mdb_name="bom_complexes.mdb",
        )

    assert result["status"] == STATUS_PARTIAL_SUCCESS
    assert result["exported_count"] == 1
    assert result["missing_count"] == 3
    assert result["export_path"].endswith("bom_complexes.mdb")
    assert result["report_path"]
    assert Path(result["report_path"]).exists()
    assert result["trace_id"]

    report_rows = Path(result["report_path"]).read_text(encoding="utf-8").splitlines()
    assert report_rows[0].split(",") == ["bom_id", "line_id", "part_number", "comp_id", "test_method", "status", "reason"]
    assert any("not_linked_in_CE" in row for row in report_rows[1:])
    assert any("not_found_in_CE" in row for row in report_rows[1:])
    assert any("unlinked_data_in_CE" in row for row in report_rows[1:])

    assert http_session.calls[0][0] == "GET"
    assert http_session.calls[0][1].endswith("/admin/health")
    assert http_session.calls[1][0] == "POST"
    assert http_session.calls[1][1].endswith("/exports/mdb")


def test_export_bom_to_ce_bridge_health_not_ready(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        rows_gui = _build_rows(_prepare_table_rows(part.part_number, "R1"))
        assembly_id = assembly.id
        assembly_id = assembly.id

    health = DummyResponse(200, {"ready": False, "last_ready_error": "warming up"})
    http_session = FakeSession(health)

    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: http_session)

    with Session(engine) as session:
        result = export_bom_to_ce_bridge(
            session,
            assembly_id,
            bom_rows=rows_gui,
            export_dir=tmp_path / "ce-out",
            mdb_name="bom_complexes.mdb",
        )

    assert result["status"] == STATUS_RETRY_LATER
    assert "warming up" in result["detail"]
    assert len(http_session.calls) == 1
    method, url, kwargs = http_session.calls[0]
    assert method == "GET"
    assert url == "http://bridge.local/admin/health"


def test_export_bom_to_ce_bridge_network_error(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        rows_gui = _build_rows(_prepare_table_rows(part.part_number, "R1"))
        assembly_id = assembly.id

    failing = FailingSession(req_exc.Timeout("boom"))

    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: failing)

    with Session(engine) as session:
        result = export_bom_to_ce_bridge(
            session,
            assembly_id,
            bom_rows=rows_gui,
            export_dir=tmp_path / "ce-out",
            mdb_name="bom_complexes.mdb",
        )

    assert result["status"] == STATUS_RETRY_WITH_BACKOFF
    assert result["detail"].startswith("Complex Editor bridge unreachable")


def test_export_bom_to_ce_bridge_no_resolved_ids(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        rows_gui = _build_rows(_prepare_table_rows(part.part_number, "R1"))
        assembly_id = assembly.id

    health = DummyResponse(200, {"ready": True, "headless": False})
    http_session = FakeSession(health)

    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: http_session)

    with Session(engine) as session:
        result = export_bom_to_ce_bridge(
            session,
            assembly_id,
            bom_rows=rows_gui,
            export_dir=tmp_path / "ce-out",
            mdb_name="bom_complexes.mdb",
        )

    assert result["status"] == STATUS_PARTIAL_SUCCESS
    assert result["exported_count"] == 0
    assert result["missing_count"] >= 1
    assert result["report_path"]
    report_path = Path(result["report_path"])
    assert report_path.exists()
    report_lines = report_path.read_text(encoding="utf-8").splitlines()
    assert any("not_linked_in_CE" in line for line in report_lines[1:])
    assert len(http_session.calls) == 1
    method, url, kwargs = http_session.calls[0]
    assert method == "GET"
    assert url == "http://bridge.local/admin/health"


class _SessionStub:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []
        self.trust_env = False

    def get(self, url, headers=None, timeout=None):
        payload = self._payloads.pop(0) if self._payloads else {}
        self.calls.append(("GET", url, headers, timeout))
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda payload=payload: payload)


class _ProcessStub:
    def __init__(self):
        self.pid = 4242
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def wait(self, timeout=None):
        self._poll = 0

    def kill(self):
        self.killed = True
        self._poll = -1


def test_ce_supervisor_ready_when_bridge_allows_headless(monkeypatch):
    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    session = _SessionStub([{"ready": True, "headless": True, "allow_headless": True}])
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: session)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not launch CE")))
    supervisor = CESupervisor(app_exe=None)
    ok, info = supervisor.ensure_ready("trace-id")
    assert ok is True
    assert info["status"] == "READY"


def test_ce_supervisor_launches_ui_when_headless_blocked(monkeypatch, tmp_path):
    exe = tmp_path / "ce.exe"
    exe.write_text("echo")
    payloads = [
        {"ready": True, "headless": True, "allow_headless": False},
        {"ready": True, "headless": False, "allow_headless": True},
    ]
    session = _SessionStub(payloads)
    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 5.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: session)
    proc_stub = _ProcessStub()
    called = {"count": 0}

    def fake_popen(*args, **kwargs):
        called["count"] += 1
        return proc_stub

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    supervisor = CESupervisor(app_exe=exe, poll_timeout_s=1.0, poll_interval_s=0.05)
    ok, info = supervisor.ensure_ready("trace-id")
    assert ok is True
    assert info["status"] == "READY"
    assert called["count"] == 1


def test_ce_supervisor_times_out_and_returns_retry_later(monkeypatch):
    payload = {"ready": True, "headless": True, "allow_headless": False, "detail": "blocked"}
    session = _SessionStub([payload] * 10)
    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 1.0))
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: session)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not launch")))
    supervisor = CESupervisor(app_exe=None, poll_timeout_s=0.3, poll_interval_s=0.05)
    ok, info = supervisor.ensure_ready("trace-id")
    assert ok is False
    assert info["status"] == "RETRY_LATER"
    assert "blocked" in info["detail"].lower()


def test_ce_supervisor_shutdown_owned_on_exit(monkeypatch, tmp_path):
    exe = tmp_path / "ce.exe"
    exe.write_text("echo")
    supervisor = CESupervisor(app_exe=exe)
    proc = _ProcessStub()
    supervisor._owned_proc = proc
    monkeypatch.setattr(ce_bridge_client, "resolve_bridge_connection", lambda: ("http://bridge.local", "token", 1.0))

    class ShutdownSession:
        def __init__(self):
            self.calls = []
            self.trust_env = False

        def post(self, url, headers=None, json=None, timeout=None):
            self.calls.append((url, headers, json, timeout))
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: {"ok": True})

    shutdown_session = ShutdownSession()
    monkeypatch.setattr(ce_bridge_transport, "get_session", lambda base=None: shutdown_session)
    supervisor._shutdown_owned()
    assert shutdown_session.calls
    assert proc.terminated is True or proc.killed is True
