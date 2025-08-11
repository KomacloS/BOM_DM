from __future__ import annotations
import csv
import io
from typing import List
from pydantic import BaseModel, validator

ALLOWED_HEADERS = [
    "part_number","description","qty","reference","manufacturer","mpn","package","value"
]
REQUIRED_HEADERS = ["part_number","description","qty","reference"]


class BOMRow(BaseModel):
    part_number: str
    description: str
    qty: int
    reference: str
    manufacturer: str | None = None
    mpn: str | None = None
    package: str | None = None
    value: str | None = None

    @validator('part_number','description','reference')
    def not_empty(cls,v):
        if v is None or str(v).strip()=="":
            raise ValueError('field required')
        return v

    @validator('qty')
    def qty_positive(cls,v):
        if isinstance(v,str):
            v = int(v)
        if v < 1:
            raise ValueError('qty must be >=1')
        return v


def parse_bom(csv_bytes: bytes) -> List[BOMRow]:
    text = csv_bytes.decode('utf-8')
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    header_l = [h.strip().lower() for h in header]
    if set(header_l) - set(ALLOWED_HEADERS):
        raise ValueError("Unknown columns")
    for req in REQUIRED_HEADERS:
        if req not in header_l:
            raise ValueError(f"Missing column {req}")
    field_map = {h: i for i,h in enumerate(header_l)}
    rows: List[BOMRow] = []
    for line_num, row in enumerate(reader, start=2):
        if len(row) != len(header_l):
            raise ValueError(f"Row {line_num} has wrong number of columns")
        data = {col: row[field_map[col]] if field_map.get(col) is not None else None for col in header_l}
        rows.append(BOMRow(**data))
    return rows
