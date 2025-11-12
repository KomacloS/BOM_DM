from __future__ import annotations

import io
import os
from importlib import import_module, reload
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine


def _make_pdf_bytes(text: str = "U1") -> bytes:
    import fitz  # type: ignore

    doc = fitz.open()  # type: ignore[call-arg]
    page = doc.new_page()
    page.insert_text((72, 72), text)
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def _setup(tmp_path):
    os.environ["BOM_DATA_ROOT"] = str(tmp_path / "data")
    config = reload(import_module("app.config"))
    config.refresh_paths()

    SQLModel.metadata.clear()
    models = reload(import_module("app.models"))
    auth = reload(import_module("app.auth"))
    reload(import_module("app.services.schematic_storage"))
    reload(import_module("app.routers.schematic_packs"))
    api_module = reload(import_module("app.api"))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    api_module.app.dependency_overrides[api_module.get_session] = session_override
    api_module.app.dependency_overrides[auth.get_current_user] = lambda: models.User(
        id=1, username="tester", hashed_password="x"
    )

    return api_module, models, config, engine


@pytest.fixture()
def client(tmp_path):
    api_module, models, config, engine = _setup(tmp_path)
    client = TestClient(api_module.app)
    client.headers.update({"Authorization": "Bearer token"})
    try:
        yield client, models, config, engine, api_module
    finally:
        client.close()
        api_module.app.dependency_overrides.clear()


def _create_assembly(models, engine):
    with Session(engine) as session:
        customer = models.Customer(name="Customer")
        session.add(customer)
        session.commit()
        session.refresh(customer)

        project = models.Project(customer_id=customer.id, code="P", title="Project")
        session.add(project)
        session.commit()
        session.refresh(project)

        assembly = models.Assembly(project_id=project.id, rev="A")
        session.add(assembly)
        session.commit()
        session.refresh(assembly)
        return assembly.id


def test_pack_crud_flow(client, tmp_path):
    client_app, models, config, engine, api_module = client
    assembly_id = _create_assembly(models, engine)

    create_resp = client_app.post(
        f"/assemblies/{assembly_id}/schematic-packs",
        json={"display_name": "Main Pack"},
    )
    assert create_resp.status_code == 200
    pack_id = create_resp.json()["pack_id"]

    pdf_one = _make_pdf_bytes("U1")
    upload_one = client_app.post(
        f"/schematic-packs/{pack_id}/files",
        files=[("files", ("first.pdf", pdf_one, "application/pdf"))],
    )
    assert upload_one.status_code == 200
    first_file = upload_one.json()[0]
    assert first_file["file_order"] == 1
    assert first_file["page_count"] >= 1
    assert first_file["has_text_layer"] is True
    stored_path = config.DATA_ROOT / first_file["relative_path"]
    assert stored_path.exists()

    detail = client_app.get(f"/schematic-packs/{pack_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["pack_revision"] == 2
    assert detail_payload["files"][0]["file_order"] == 1

    list_resp = client_app.get(f"/assemblies/{assembly_id}/schematic-packs")
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload[0]["pack_revision"] == 2

    pdf_two = _make_pdf_bytes("R5")
    upload_two = client_app.post(
        f"/schematic-packs/{pack_id}/files",
        files=[("files", ("second.pdf", pdf_two, "application/pdf"))],
    )
    assert upload_two.status_code == 200
    second_file = upload_two.json()[0]
    assert second_file["file_order"] == 2

    reorder_resp = client_app.post(
        f"/schematic-packs/{pack_id}/reorder",
        json={"file_ids": [second_file["id"], first_file["id"]]},
    )
    assert reorder_resp.status_code == 200
    assert reorder_resp.json()["pack_revision"] == 4

    detail_after = client_app.get(f"/schematic-packs/{pack_id}")
    after_payload = detail_after.json()
    orders = [f["id"] for f in after_payload["files"]]
    assert orders == [second_file["id"], first_file["id"]]

    search_resp = client_app.get(
        f"/schematic-packs/{pack_id}/search",
        params={"q": "U1", "mode": "refdes"},
    )
    assert search_resp.status_code == 200
    assert search_resp.json() == []

    overlay_resp = client_app.get(
        f"/schematic-files/{first_file['id']}/page/1/overlays",
        params={"q": "U1"},
    )
    assert overlay_resp.status_code == 200
    assert overlay_resp.json()["boxes"] == []

    stream_resp = client_app.get(f"/schematic-files/{first_file['id']}/stream")
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("application/pdf")
    assert stream_resp.content.startswith(b"%PDF")

    reindex_resp = client_app.post(f"/schematic-files/{first_file['id']}/reindex")
    assert reindex_resp.status_code == 200
    assert reindex_resp.json()["status"] == "queued"

    with Session(engine) as session:
        refreshed = session.get(models.SchematicFile, first_file["id"])
        assert refreshed.last_indexed_at is not None
        relative = Path(refreshed.relative_path)
        assert relative.parts[0] == "assemblies"
        assert relative.parents[0].name == "files"


def test_upload_order_isolated_per_pack(client, tmp_path):
    client_app, models, config, engine, api_module = client
    assembly_id = _create_assembly(models, engine)

    first_pack = client_app.post(
        f"/assemblies/{assembly_id}/schematic-packs",
        json={"display_name": "Pack One"},
    ).json()["pack_id"]
    second_pack = client_app.post(
        f"/assemblies/{assembly_id}/schematic-packs",
        json={"display_name": "Pack Two"},
    ).json()["pack_id"]

    pdf_bytes = _make_pdf_bytes("R1")
    first_upload = client_app.post(
        f"/schematic-packs/{first_pack}/files",
        files=[("files", ("first.pdf", pdf_bytes, "application/pdf"))],
    ).json()[0]
    assert first_upload["file_order"] == 1

    second_upload = client_app.post(
        f"/schematic-packs/{second_pack}/files",
        files=[("files", ("second.pdf", pdf_bytes, "application/pdf"))],
    ).json()[0]
    assert second_upload["file_order"] == 1
