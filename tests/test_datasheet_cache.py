from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy.pool import StaticPool
from importlib import reload

import app.models as models
from app.services import import_bom


def setup_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.clear()
    reload(models)
    SQLModel.metadata.create_all(engine)
    return engine


def test_datasheet_cached(tmp_path):
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)

        pdf = tmp_path / "ds.pdf"
        pdf.write_bytes(b"pdf")
        data = f"PN,Reference,Datasheet\nP1,R1,{pdf}\n".encode()

        report = import_bom(asm.id, data, session)
        assert report.total == 1 and not report.errors

        part = session.exec(select(models.Part)).first()
        assert part.datasheet_url
        assert Path(part.datasheet_url).exists()
        assert Path(part.datasheet_url).parent.name == "datasheets"

        item = session.exec(select(models.BOMItem)).first()
        assert item.datasheet_url == str(pdf)
