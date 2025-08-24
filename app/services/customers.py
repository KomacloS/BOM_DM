"""Customer service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models import Customer


class CustomerExistsError(ValueError):
    """Raised when attempting to create a duplicate customer."""
    pass


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
    """Create and persist a new :class:`Customer` record.

    Parameters
    ----------
    name:
        Name of the customer to create.  Leading/trailing whitespace is
        ignored and uniqueness is case-insensitive.
    email:
        Optional contact email for the customer.
    session:
        Active database session.
    """

    # 1) normalise inputs
    name = (name or "").strip()
    email = (email or "").strip() or None
    if not name:
        raise ValueError("Customer name is required")

    # 2) case-insensitive existence check
    existing = session.exec(
        select(Customer).where(func.lower(Customer.name) == name.lower())
    ).first()
    if existing:
        raise CustomerExistsError(f'Customer "{name}" already exists')

    # 3) insert
    cust = Customer(name=name, contact_email=email)
    session.add(cust)
    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        # re-check for duplicate as a fallback (SQLite unnamed constraints)
        exists = session.exec(
            select(Customer).where(func.lower(Customer.name) == name.lower())
        ).first()
        if exists:
            raise CustomerExistsError(f'Customer "{name}" already exists') from e
        raise
    session.refresh(cust)
    return cust

