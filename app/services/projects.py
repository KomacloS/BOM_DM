"""Project service helpers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import List, Optional

from sqlmodel import Session, select

from sqlalchemy import func

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

    code = (code or "").strip()
    title = (title or "").strip()
    if not code or not title:
        raise ValueError("Code and title are required")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", code):
        raise ValueError("Invalid code format")

    existing = session.exec(
        select(Project).where(
            Project.customer_id == customer_id,
            func.lower(Project.code) == code.lower(),
        )
    ).first()
    if existing:
        raise ProjectCodeExistsError("Code already exists for this customer.")

    prio = ProjectPriority(priority)
    proj = Project(
        customer_id=customer_id,
        code=code,
        title=title,
        name=title,
        priority=prio,
        status="draft",
        due_at=due_at,
    )
    session.add(proj)
    session.commit()
    session.refresh(proj)
    return proj


class ProjectCodeExistsError(ValueError):
    pass

