"""Project service helpers."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import Session, select

from ..models import Project, ProjectPriority


def list_projects(customer_id: int, session: Session) -> List[Project]:
    """Return all projects for a given customer."""

    stmt = select(Project).where(Project.customer_id == customer_id)
    stmt = stmt.order_by(Project.created_at)
    return session.exec(stmt).all()


def create_project(
    customer_id: int,
    code: str,
    title: str,
    priority: str,
    due_at: Optional[datetime],
    session: Session,
) -> Project:
    """Create a new :class:`Project` instance."""

    prio = ProjectPriority(priority)
    proj = Project(
        customer_id=customer_id,
        code=code,
        title=title,
        priority=prio,
        due_at=due_at,
    )
    session.add(proj)
    session.commit()
    session.refresh(proj)
    return proj

