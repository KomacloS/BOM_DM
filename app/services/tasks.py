"""Task service helpers."""

from __future__ import annotations

from typing import List, Optional

from sqlmodel import Session, select

from ..models import Task, TaskStatus


def list_tasks(
    project_id: int, status: Optional[str], session: Session
) -> List[Task]:
    """Return tasks for a project with optional status filtering."""

    stmt = select(Task).where(Task.project_id == project_id)
    if status:
        stmt = stmt.where(Task.status == TaskStatus(status))
    stmt = stmt.order_by(Task.created_at)
    return session.exec(stmt).all()

