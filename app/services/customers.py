"""Customer service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlmodel import Session, select

from ..models import Customer


def list_customers(q: str | None, session: Session) -> List[Customer]:
    """Return customers optionally filtered by a name query.

    Parameters
    ----------
    q:
        Optional substring to filter the customer name.  ``None`` returns
        all customers.
    session:
        Active database session.
    """

    stmt = select(Customer)
    if q:
        stmt = stmt.where(Customer.name.contains(q))
    stmt = stmt.order_by(Customer.name)
    return session.exec(stmt).all()


def create_customer(name: str, email: Optional[str], session: Session) -> Customer:
    """Create and persist a new :class:`Customer` record."""

    cust = Customer(name=name, contact_email=email)
    session.add(cust)
    session.commit()
    session.refresh(cust)
    return cust

