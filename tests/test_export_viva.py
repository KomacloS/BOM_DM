import json
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session

import importlib

import app.models as models

@pytest.fixture(autouse=True)
def _mock_ce_diagnostics(monkeypatch):
    monkeypatch.setattr("app.services.export_viva.ce_bridge_manager.get_last_ce_bridge_diagnostics", lambda: None)

from app.models import Part
from app.domain.complex_linker import ComplexLink
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CEExportError
from app.services.export_viva import (
    build_viva_groups,
    write_viva_txt,
    perform_viva_export,
    VivaExportError,
)


def setup_db():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    importlib.reload(models)
    SQLModel.metadata.create_all(engine)
    return engine


def test_build_viva_groups_grouping_and_fields(tmp_path):
    engine = setup_db()
    with Session(engine) as session:
        part = Part(part_number="CRCW04021K00FKTD", value="1k", tol_n="-1%", tol_p="1%")
        session.add(part)
        session.commit()
        rows_gui = [
            {"reference": "R5", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RESISTOR"},
            {"reference": "R1", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RESISTOR"},
            {"reference": "R7", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RESISTOR"},
            {"reference": "R8", "part_number": part.part_number, "test_method": "Something", "test_detail": ""},
            {"reference": "R9", "part_number": part.part_number, "test_method": "QT", "test_detail": ""},
        ]
        groups = build_viva_groups(rows_gui, session, assembly_id=1)
        assert len(groups) == 2
        assert groups[0]["reference"] == "R1,R5,R7"
        assert groups[0]["quantity"] == "3"
        assert groups[0]["function"] == "RESISTOR"
        assert groups[0]["value"] == "1k"
        assert groups[0]["toln"] == "-1%"
        assert groups[0]["tolp"] == "1%"
        assert groups[1]["reference"] == "R8,R9"
        assert groups[1]["function"] == "Digital"
        path = tmp_path / "out.txt"
        write_viva_txt(str(path), groups)
        content = path.read_text(encoding="utf-8").splitlines()
        assert content[0] == "reference\tquantity\tPart number\tFunction\tValue\tTolN\tTolP"


def test_build_viva_groups_missing_method():
    engine = setup_db()
    with Session(engine) as session:
        session.add(Part(part_number="PN1"))
        session.commit()
        rows_gui = [{"reference": "R1", "part_number": "PN1", "test_method": "", "test_detail": ""}]
        with pytest.raises(ValueError, match="Missing Test Method"):
            build_viva_groups(rows_gui, session, assembly_id=1)


def test_build_viva_groups_macro_missing_detail():
    engine = setup_db()
    with Session(engine) as session:
        session.add(Part(part_number="PN1"))
        session.commit()
        rows_gui = [{"reference": "R1", "part_number": "PN1", "test_method": "macro", "test_detail": ""}]
        with pytest.raises(ValueError, match="requires Test detail"):
            build_viva_groups(rows_gui, session, assembly_id=1)


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


def test_perform_viva_export_assigned_only(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        session.add(ComplexLink(part_id=part.id, ce_complex_id="321"))
        session.commit()
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RES"}
        ]
        captured = {}
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "1.0"})

        def fake_export(comp_ids, out_dir, mdb_name):
            captured["comp_ids"] = comp_ids
            captured["out_dir"] = out_dir
            captured["mdb_name"] = mdb_name
            return {"export_path": str(tmp_path / "bom_complexes.mdb"), "trace_id": "trace-123"}

        monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", fake_export)
        result = perform_viva_export(
            session,
            assembly.id,
            base_dir=str(tmp_path),
            bom_rows=rows_gui,
            strict=True,
        )
    assert captured["comp_ids"] == [321]
    assert captured["out_dir"] == str(tmp_path)
    assert captured["mdb_name"].endswith(".mdb")
    assert result.status == "success"
    assert result.mdb_path and result.mdb_path.name.endswith(".mdb")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "success"
    assert manifest["exported_comp_ids"] == [321]


def test_perform_viva_export_strict_missing_blocks(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        pn = part.part_number
        rows_gui = [
            {"reference": "R1", "part_number": pn, "test_method": "Complex", "test_detail": ""}
        ]
        monkeypatch.setattr(
            ce_bridge_client,
            "export_complexes_mdb",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("export should not be called")),
        )
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "1.0"})
        with pytest.raises(VivaExportError) as excinfo:
            perform_viva_export(
                session,
                assembly.id,
                base_dir=str(tmp_path),
                bom_rows=rows_gui,
                strict=True,
            )
    err = excinfo.value
    assert err.reason == "unlinked_required"
    manifest_path = tmp_path / "viva_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"


def test_perform_viva_export_relaxed_resolves_by_pn(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        pn = part.part_number
        rows_gui = [
            {"reference": "R1", "part_number": pn, "test_method": "Complex", "test_detail": ""}
        ]
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "1.0"})
        monkeypatch.setattr(
            ce_bridge_client,
            "search_complexes",
            lambda pn, limit=20: [{"pn": pn, "id": "555"}],
        )
        captured = {}

        def fake_export(comp_ids, out_dir, mdb_name):
            captured["comp_ids"] = comp_ids
            return {"export_path": str(tmp_path / "bom_complexes.mdb")}

        monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", fake_export)
        result = perform_viva_export(
            session,
            assembly.id,
            base_dir=str(tmp_path),
            bom_rows=rows_gui,
            strict=False,
        )
    assert captured["comp_ids"] == [555]
    assert result.status == "success"
    if result.warnings:
        assert any("Resolved" in w for w in result.warnings)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["exported_comp_ids"] == [555]
    assert manifest["status"] == "success"


def test_perform_viva_export_relaxed_skips_when_unresolved(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Complex", "test_detail": ""}
        ]
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "1.0"})
        monkeypatch.setattr(ce_bridge_client, "search_complexes", lambda pn, limit=20: [])
        monkeypatch.setattr(
            ce_bridge_client,
            "export_complexes_mdb",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("export should not be called")),
        )
        result = perform_viva_export(
            session,
            assembly.id,
            base_dir=str(tmp_path),
            bom_rows=rows_gui,
            strict=False,
        )
    assert result.status == "skipped"
    assert not result.exported_comp_ids
    assert result.mdb_path is None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "skipped"


def test_perform_viva_export_endpoint_missing(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        session.add(ComplexLink(part_id=part.id, ce_complex_id="999"))
        session.commit()
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RES"}
        ]
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "1.0"})

        def _raise_not_found(*_args, **_kwargs):
            raise CEExportError(404, "endpoint_missing", {"detail": "missing"}, "trace-y")

        monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", _raise_not_found)
        with pytest.raises(VivaExportError) as excinfo:
            perform_viva_export(
                session,
                assembly.id,
                base_dir=str(tmp_path),
                bom_rows=rows_gui,
                strict=True,
            )
        err = excinfo.value
        assert err.reason == "endpoint_missing"
        manifest_path = tmp_path / "viva_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "error"
        assert any("404" in w or "support" in w.lower() for w in manifest["warnings"])


def test_perform_viva_export_bridge_too_old(tmp_path, monkeypatch):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        session.add(ComplexLink(part_id=part.id, ce_complex_id="1001"))
        session.commit()
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RES"}
        ]
        monkeypatch.setattr(
            ce_bridge_client,
            "get_bridge_context",
            lambda: {"base_url": "http://127.0.0.1:8765", "timeout": 10.0, "ui_enabled": True},
        )
        monkeypatch.setattr(ce_bridge_client, "wait_until_ready", lambda **kwargs: {"version": "0.9"})

        def _raise_unsupported(*_args, **_kwargs):
            raise CEExportError(404, "export_mdb_unsupported", {"detail": "not supported"}, "trace-z")

        monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", _raise_unsupported)
        with pytest.raises(VivaExportError) as excinfo:
            perform_viva_export(
                session,
                assembly.id,
                base_dir=str(tmp_path),
                bom_rows=rows_gui,
                strict=True,
            )
        err = excinfo.value
        assert err.reason == "export_mdb_unsupported"
        assert err.trace_id == "trace-z"
        manifest_path = tmp_path / "viva_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "error"
        assert any("old" in w.lower() for w in manifest["warnings"])
