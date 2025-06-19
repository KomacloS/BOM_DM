from datetime import datetime, timedelta
from sqlmodel import Session, select, SQLModel, Field
from .config import FX_CACHE_HOURS
from .vendor import fixer


class FXRate(SQLModel, table=True):
    code: str = Field(primary_key=True)
    rate: float
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


def get(code: str) -> float:
    code = code.upper()
    from . import main
    engine = main.engine
    with Session(engine) as session:
        stmt = select(FXRate).where(FXRate.code == code)
        if engine.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        rate_obj = session.exec(stmt).one_or_none()
        if rate_obj and datetime.utcnow() - rate_obj.fetched_at < timedelta(hours=FX_CACHE_HOURS):
            return rate_obj.rate
        rates = fixer.today()
        val = rates.get(code, 1.0)
        if rate_obj:
            rate_obj.rate = val
            rate_obj.fetched_at = datetime.utcnow()
        else:
            rate_obj = FXRate(code=code, rate=val, fetched_at=datetime.utcnow())
        session.add(rate_obj)
        session.commit()
        return rate_obj.rate
