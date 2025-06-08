# root: app/trace_utils.py
from __future__ import annotations


from sqlmodel import Session, select



def component_trace(part_number: str, session: Session) -> list[dict]:
    """Return failed boards referencing the given part number."""
    from .main import BOMItem, TestResult  # local import to avoid circular

    bom_items = session.exec(select(BOMItem).where(BOMItem.part_number == part_number)).all()
    if not bom_items:
        return []
    refs: list[str] = [b.reference for b in bom_items if b.reference]
    failed = session.exec(select(TestResult).where(TestResult.result == False)).all()
    matches: list[dict] = []
    for tr in failed:
        details = tr.failure_details or ""
        if part_number in details or any(ref in details for ref in refs):
            matches.append({
                "serial_number": tr.serial_number,
                "result": tr.result,
                "failure_details": tr.failure_details,
            })
    return matches


def board_trace(serial_number: str, session: Session) -> dict:
    """Return test result info for a board with BOM status annotations."""
    from .main import BOMItem, TestResult  # local import to avoid circular

    result = session.exec(select(TestResult).where(TestResult.serial_number == serial_number)).first()
    if not result:
        return {}
    bom_items = session.exec(select(BOMItem)).all()
    details = result.failure_details or ""
    annotated = []
    for item in bom_items:
        status = "FAIL" if (item.part_number in details or (item.reference and item.reference in details)) else "OK"
        annotated.append({
            "id": item.id,
            "part_number": item.part_number,
            "description": item.description,
            "quantity": item.quantity,
            "reference": item.reference,
            "status": status,
        })
    return {
        "serial_number": result.serial_number,
        "result": result.result,
        "failure_details": result.failure_details,
        "bom": annotated,
    }
