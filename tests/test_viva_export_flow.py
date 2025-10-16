import json
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.domain.complex_linker import ComplexLink
from app.models import Assembly, BOMItem, Part, PartTestAssignment, Project, TestMethod
from app.services import (
    VIVAExportValidationError,
    collect_bom_lines,
    determine_comp_ids,
    perform_viva_export,
)
from app.integration.ce_bridge_client import CEExportError


@pytest.fixture()
def sqlite_session(tmp_path, monkeypatch):
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

    def _session():
        for table in tables:
            table.create(engine, checkfirst=True)
        return Session(engine)

    monkeypatch.setattr("app.database.new_session", _session, raising=False)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def populated_context(sqlite_session):
    session = sqlite_session
    project = Project(customer_id=1, code="PRJ", title="Widget")
    session.add(project)
    session.commit()
    session.refresh(project)
    assembly = Assembly(project_id=project.id, rev="A")
    session.add(assembly)
    session.commit()
    session.refresh(assembly)

    part = Part(part_number="PN-1", description="Widget")
    session.add(part)
    session.commit()
    session.refresh(part)

    assignment = PartTestAssignment(part_id=part.id, method=TestMethod.complex)
    session.add(assignment)
    session.commit()

    bom = BOMItem(
        assembly_id=assembly.id,
        part_id=part.id,
        reference="R1",
        qty=1,
        is_fitted=True,
    )
    session.add(bom)
    session.commit()
    session.refresh(bom)

    return {
        "session": session,
        "project": project,
        "assembly": assembly,
        "part": part,
        "bom": bom,
    }


@pytest.fixture()
def mock_ce(monkeypatch, tmp_path):
    calls = {"export": None}

    def wait_ready(**kwargs):
        return {"ready": True}

    def get_base():
        return "http://127.0.0.1:8765"

    def export(
        comp_ids,
        out_dir,
        *,
        mdb_name="bom_complexes.mdb",
        require_linked=True,
        pns=None,
    ):
        calls["export"] = {
            "comp_ids": list(comp_ids),
            "out_dir": out_dir,
            "mdb_name": mdb_name,
            "require_linked": require_linked,
            "pns": list(pns or []),
        }
        Path(out_dir, mdb_name).write_text("stub", encoding="utf-8")
        return {"exported": len(comp_ids), "trace_id": "trace-123"}

    monkeypatch.setattr("app.integration.ce_bridge_client.wait_until_ready", wait_ready)
    monkeypatch.setattr("app.integration.ce_bridge_client.get_active_base_url", get_base)
    monkeypatch.setattr("app.integration.ce_bridge_client.export_complexes_mdb", export)
    monkeypatch.setattr(
        "app.integration.ce_bridge_client.lookup_complex_ids",
        lambda pns: ({pn: 456 for pn in pns}, []),
    )
    return calls


def test_perform_viva_export_happy_path(populated_context, mock_ce, tmp_path):
    session = populated_context["session"]
    assembly = populated_context["assembly"]
    part = populated_context["part"]

    # Insert complex link directly via SQL
    session.add(ComplexLink(part_id=part.id, ce_complex_id="123"))
    session.commit()

    rows = [
        {
            "reference": "R1",
            "quantity": "1",
            "part_number": part.part_number,
            "function": "Digital",
            "value": "",
            "toln": "",
            "tolp": "",
        }
    ]

    result = perform_viva_export(
        session,
        assembly.id,
        base_dir=tmp_path,
        bom_rows=rows,
    )

    assert result.comp_ids == (123,)
    assert result.warnings == ()
    manifest_path = result.diagnostics.manifest_path
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["exported_comp_ids"] == [123]
    assert manifest["trace_id"] == "trace-123"
    assert manifest["status"] == "success"
    assert Path(result.paths.bom_txt).exists()
    assert Path(result.paths.mdb_path).exists()

    assert mock_ce["export"]["comp_ids"] == [123]
    assert mock_ce["export"]["pns"] == [part.part_number]


def test_determine_comp_ids_requires_complex(populated_context, monkeypatch):
    session = populated_context["session"]
    assembly = populated_context["assembly"]

    lines = collect_bom_lines(session, assembly.id)
    with pytest.raises(VIVAExportValidationError) as excinfo:
        determine_comp_ids(lines)
    missing = excinfo.value.missing
    assert missing and missing[0].line_number == 1


def test_perform_viva_export_resolves_pn(populated_context, mock_ce, tmp_path, monkeypatch):
    session = populated_context["session"]
    assembly = populated_context["assembly"]
    part = populated_context["part"]

    rows = [
        {
            "reference": "R1",
            "quantity": "1",
            "part_number": part.part_number,
            "function": "Digital",
            "value": "",
            "toln": "",
            "tolp": "",
        }
    ]

    def resolver(pns):
        return {pns[0]: 789}, []

    result = perform_viva_export(
        session,
        assembly.id,
        base_dir=tmp_path,
        bom_rows=rows,
        strict=False,
        resolver=resolver,
    )

    assert result.comp_ids == (789,)


def test_perform_viva_export_persists_ce_error(populated_context, tmp_path, monkeypatch):
    session = populated_context["session"]
    assembly = populated_context["assembly"]
    part = populated_context["part"]

    # Link part to complex
    session.add(ComplexLink(part_id=part.id, ce_complex_id="321"))
    session.commit()

    rows = [
        {
            "reference": "R1",
            "quantity": "1",
            "part_number": part.part_number,
            "function": "Digital",
            "value": "",
            "toln": "",
            "tolp": "",
        }
    ]

    payload = {"reason": "busy", "trace_id": "abc-123"}

    def raise_busy(*args, **kwargs):
        raise CEExportError(
            "Complex Editor busy",
            status_code=409,
            reason="busy",
            payload=payload,
        )

    monkeypatch.setattr("app.integration.ce_bridge_client.wait_until_ready", lambda **_: {"ready": True})
    monkeypatch.setattr("app.integration.ce_bridge_client.get_active_base_url", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr("app.integration.ce_bridge_client.export_complexes_mdb", raise_busy)

    with pytest.raises(CEExportError) as excinfo:
        perform_viva_export(
            session,
            assembly.id,
            base_dir=tmp_path,
            bom_rows=rows,
        )

    error = excinfo.value
    diagnostics = getattr(error, "diagnostics", None)
    assert diagnostics is not None
    manifest_path = diagnostics.manifest_path
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "error"
    assert manifest["error"]["reason"] == "busy"
    assert manifest["ce_response"]["trace_id"] == "abc-123"
    if diagnostics.ce_response_path:
        ce_response = json.loads(diagnostics.ce_response_path.read_text(encoding="utf-8"))
        assert ce_response["reason"] == "busy"
