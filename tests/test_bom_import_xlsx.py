import io
from openpyxl import Workbook
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


def test_import_xlsx_with_price():
    engine = setup_db()
    with Session(engine) as session:
        cust = models.Customer(name="Cust")
        session.add(cust); session.commit(); session.refresh(cust)
        proj = models.Project(customer_id=cust.id, code="PRJ", title="Proj")
        session.add(proj); session.commit(); session.refresh(proj)
        asm = models.Assembly(project_id=proj.id, rev="A")
        session.add(asm); session.commit(); session.refresh(asm)

        wb = Workbook()
        ws = wb.active
        ws.append(["PN", "Reference", "Qty", "Price"])
        ws.append(["P1", "R1", 2, 0.5])
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()

        report = import_bom(asm.id, data, session)
        assert report.total == 1 and not report.errors

        item = session.exec(select(models.BOMItem)).first()
        assert float(item.unit_cost) == 0.5 and item.qty == 2
