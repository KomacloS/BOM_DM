from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine


@pytest.mark.usefixtures("monkeypatch")
def test_data_root_paths(tmp_path, monkeypatch):
    data_root = (tmp_path / "app-data").resolve()
    monkeypatch.setenv("BOM_DATA_ROOT", str(data_root))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import app.config as config

    config = importlib.reload(config)

    datasheets_module = None

    try:
        assert config.DATA_ROOT == data_root

        url = make_url(config.DATABASE_URL)
        db_path = Path(url.database or "").resolve()
        assert db_path.is_relative_to(config.DATA_ROOT)

        import app.services.datasheets as datasheets

        datasheets_module = importlib.reload(datasheets)
        assert datasheets_module.DATASHEET_STORE.is_relative_to(config.DATA_ROOT)

        engine = create_engine(
            config.DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.clear()
        import app.models as models

        models = importlib.reload(models)
        from app.domain import complex_linker as linker

        linker = importlib.reload(linker)
        SQLModel.metadata.create_all(engine)

        pdf_src = tmp_path / "datasheet.pdf"
        pdf_src.write_bytes(b"PDF")

        with Session(engine) as session:
            part = models.Part(part_number="PN-ROOT", description="Root test")
            session.add(part)
            session.commit()
            session.refresh(part)

            dst, existed = datasheets_module.register_datasheet_for_part(session, part.id, pdf_src)
            assert existed is False
            assert dst.exists()
            assert dst.resolve().is_relative_to(config.DATA_ROOT)

            project = models.Project(customer_id=1, code="P", title="Project")
            session.add(models.Customer(id=1, name="Customer"))
            session.add(project)
            session.commit()
            session.refresh(project)

            assembly = models.Assembly(project_id=project.id, rev="A")
            session.add(assembly)
            session.commit()
            session.refresh(assembly)

            part_assignment = models.PartTestAssignment(
                part_id=part.id,
                method=models.TestMethod.complex,
            )
            session.add(part_assignment)
            session.commit()

            bom_item = models.BOMItem(
                assembly_id=assembly.id,
                part_id=part.id,
                reference="R1",
                qty=1,
                is_fitted=True,
            )
            session.add(bom_item)
            session.commit()
            session.refresh(bom_item)

            session.add(linker.ComplexLink(part_id=part.id, ce_complex_id="321"))
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

            from app.services import perform_viva_export
            from app.integration import ce_bridge_client

            def _wait_ready(**_kwargs):
                return {"ready": True}

            def _export(comp_ids, out_dir, *, mdb_name="bom_complexes.mdb", **_kwargs):
                Path(out_dir, mdb_name).write_text("stub", encoding="utf-8")
                return {"exported": len(comp_ids), "trace_id": "trace"}

            monkeypatch.setattr(ce_bridge_client, "wait_until_ready", _wait_ready)
            monkeypatch.setattr(ce_bridge_client, "export_complexes_mdb", _export)
            monkeypatch.setattr(
                ce_bridge_client,
                "lookup_complex_ids",
                lambda pns: ({pns[0]: 321}, []),
            )

            export_root = config.DATA_ROOT / "exports"
            result = perform_viva_export(
                session,
                assembly.id,
                base_dir=export_root,
                bom_rows=rows,
            )

        assert result.paths.folder.resolve().is_relative_to(config.DATA_ROOT)
        assert result.paths.bom_txt.resolve().is_relative_to(config.DATA_ROOT)
        assert result.paths.mdb_path.resolve().is_relative_to(config.DATA_ROOT)
    finally:
        importlib.reload(config)
        if datasheets_module is not None:
            importlib.reload(datasheets_module)
