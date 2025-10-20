import json
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session

import importlib

import app.models as models

from app.models import Part
from app.domain.complex_linker import ComplexLink
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


def test_perform_viva_export_assigned_only(tmp_path):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        session.add(ComplexLink(part_id=part.id, ce_complex_id="321"))
        session.commit()
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Macro", "test_detail": "RES"}
        ]
        result = perform_viva_export(
            session,
            assembly.id,
            base_dir=str(tmp_path),
            bom_rows=rows_gui,
            strict=True,
        )
    assert result.status == "success"
    assert result.exported_comp_ids == [321]
    assert result.mdb_path is None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "success"
    assert manifest["exported_comp_ids"] == [321]
    assert manifest["export_path"] is None


def test_perform_viva_export_strict_missing_blocks(tmp_path):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        pn = part.part_number
        rows_gui = [
            {"reference": "R1", "part_number": pn, "test_method": "Complex", "test_detail": ""}
        ]
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

def test_perform_viva_export_relaxed_skips_when_unresolved(tmp_path):
    engine = setup_db()
    ComplexLink.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        assembly, part = _seed_basic_bom(session)
        rows_gui = [
            {"reference": "R1", "part_number": part.part_number, "test_method": "Complex", "test_detail": ""}
        ]
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

