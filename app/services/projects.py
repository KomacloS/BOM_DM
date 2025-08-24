"""Project service helpers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from ..models import Project, ProjectPriority, Assembly, BOMItem, Task
from .customers import DeleteBlockedError


def list_projects(customer_id: int, session: Session) -> List[Project]:
    """Return all projects for a given customer."""

    stmt = select(Project).where(Project.customer_id == customer_id)
    stmt = stmt.order_by(Project.created_at)
    try:
        return session.exec(stmt).all()
    except OperationalError as e:  # pragma: no cover - depends on DB schema
        raise RuntimeError(
            "Projects query failed; run 'python -m app.tools.db migrate'. Details: "
            f"{e}"
        ) from e


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


def delete_project(project_id: int, session: Session, *, cascade: bool = False) -> None:
    """Delete a project and optionally cascade to assemblies and tasks."""

    proj = session.get(Project, project_id)
    if not proj:
        return

    asm_ids = session.exec(
        select(Assembly.id).where(Assembly.project_id == project_id)
    ).all()
    if asm_ids and not cascade:
        raise DeleteBlockedError(f"Project has {len(asm_ids)} assemblies")

    if cascade:
        for aid in asm_ids:
            from .assemblies import delete_assembly

            delete_assembly(aid, session)

        session.exec(Task.__table__.delete().where(Task.project_id == project_id))

    session.delete(proj)
    session.commit()

