from pathlib import Path
from importlib import reload
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session

import app.models as models
from app.services.datasheets import (
    DATASHEET_STORE,
    sha256_of_file,
    canonical_path_for_hash,
    register_datasheet_for_part,
)
from app.services.parts import update_part_datasheet_url


def setup_db():
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    SQLModel.metadata.create_all(engine)
    return engine


def test_register_and_update_datasheet_url(tmp_path: Path):
    # Point store into temp dir
    import app.services.datasheets as ds
    ds.DATASHEET_STORE = tmp_path / "datasheets"

    # Prepare two identical small pdf files (text is fine)
    f1 = tmp_path / "a.pdf"
    f1.write_bytes(b"PDF-TEST-CONTENT")
    f2 = tmp_path / "b.pdf"
    f2.write_bytes(b"PDF-TEST-CONTENT")

    h1 = sha256_of_file(f1)
    h2 = sha256_of_file(f2)
    assert h1 == h2

    engine = setup_db()
    with Session(engine) as session:
        p = models.Part(part_number="PX")
        session.add(p); session.commit(); session.refresh(p)

        dst1, existed1 = register_datasheet_for_part(session, p.id, f1)
        assert existed1 is False
        assert dst1.exists()
        assert dst1 == canonical_path_for_hash(h1)

        dst2, existed2 = register_datasheet_for_part(session, p.id, f2)
        assert existed2 is True
        assert dst2 == dst1

        update_part_datasheet_url(session, p.id, str(dst1))
        session.refresh(p)
        assert p.datasheet_url == str(dst1)

