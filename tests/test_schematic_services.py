from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from app import models, services, config


def _make_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF")


def _prepare_assembly(session: Session) -> models.Assembly:
    customer = models.Customer(name="Customer")
    session.add(customer)
    session.commit()
    session.refresh(customer)

    project = models.Project(customer_id=customer.id, code="PRJ", title="Project")
    session.add(project)
    session.commit()
    session.refresh(project)

    assembly = models.Assembly(project_id=project.id, rev="A")
    session.add(assembly)
    session.commit()
    session.refresh(assembly)
    return assembly


def test_schematic_pack_add_replace_remove(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(config, "DATA_ROOT", data_root, raising=False)

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        assembly = _prepare_assembly(session)
        pack = services.create_schematic_pack(session, assembly.id, "Primary")

        pdf_one = tmp_path / "schematic_one.pdf"
        _make_pdf(pdf_one)
        info = services.add_schematic_file_from_path(session, pack.id, pdf_one)
        assert info.file_order == 1
        assert info.exists
        stored_path = data_root / info.relative_path
        assert stored_path.exists()

        pdf_two = tmp_path / "schematic_two.pdf"
        _make_pdf(pdf_two)
        updated = services.replace_schematic_file_from_path(session, info.id, pdf_two)
        assert updated.id == info.id
        assert updated.relative_path != info.relative_path
        assert (data_root / updated.relative_path).exists()
        assert not (data_root / info.relative_path).exists()

        services.rename_schematic_pack(session, pack.id, "Updated Pack")
        refreshed = services.get_pack_detail(session, pack.id)
        assert refreshed is not None
        assert refreshed.display_name == "Updated Pack"
        assert len(refreshed.files) == 1

        services.remove_schematic_file(session, updated.id)
        assert not (data_root / updated.relative_path).exists()
        packs = services.list_schematic_packs(session, assembly.id)
        assert len(packs) == 1
        assert packs[0].files == []
