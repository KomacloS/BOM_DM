from __future__ import annotations
from sqlmodel import Session, select
from typing import List
from pydantic import BaseModel

from .models import BOMItem, Part, Task, TaskStatus, Assembly, AuditEvent, AuditAction
from .bom_schema import parse_bom, BOMRow


class ImportReport(BaseModel):
    total: int
    matched: int
    unmatched: int
    created_task_ids: List[int]
    errors: List[str] = []


def import_bom(session: Session, assembly_id: int, csv_bytes: bytes) -> ImportReport:
    errors: List[str] = []
    rows: List[BOMRow] = []
    try:
        rows = parse_bom(csv_bytes)
    except Exception as e:
        errors.append(str(e))
        return ImportReport(total=0, matched=0, unmatched=0, created_task_ids=[], errors=errors)

    assembly = session.get(Assembly, assembly_id)
    if not assembly:
        errors.append("assembly not found")
        return ImportReport(total=0, matched=0, unmatched=0, created_task_ids=[], errors=errors)

    total = matched = unmatched = 0
    created_task_ids: List[int] = []

    for row in rows:
        total += 1
        part = session.exec(select(Part).where(Part.part_number == row.part_number)).first()
        if part:
            matched += 1
            part_id = part.id
        else:
            unmatched += 1
            task = Task(
                project_id=assembly.project_id,
                title=f"Define part {row.part_number} from BOM of assembly {assembly.rev}",
                description=row.description,
                status=TaskStatus.todo,
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            created_task_ids.append(task.id)
            part_id = None
            session.add(AuditEvent(entity_type="task", entity_id=task.id, action=AuditAction.insert))
        bom_item = BOMItem(
            assembly_id=assembly_id,
            part_id=part_id,
            reference=row.reference,
            qty=row.qty,
            alt_part_number=row.mpn,
            is_fitted=True,
            notes=row.description,
        )
        session.add(bom_item)
        session.commit()
        session.refresh(bom_item)
        session.add(AuditEvent(entity_type="bom_item", entity_id=bom_item.id, action=AuditAction.insert))
        session.commit()

    return ImportReport(
        total=total,
        matched=matched,
        unmatched=unmatched,
        created_task_ids=created_task_ids,
        errors=errors,
    )
