"""Service layer for business logic.

This package exposes thin wrappers around common CRUD workflows used by
both the desktop GUI and the forthcoming FastAPI layer.  Each function is
UI agnostic and operates directly on a SQLModel ``Session`` instance.

The modules re-export the most commonly used functions so existing imports
like ``from app.services import import_bom`` continue to work.
"""

from decimal import Decimal

from pydantic import BaseModel

class BOMItemRead(BaseModel):
    id: int
    assembly_id: int
    reference: str
    qty: int
    manufacturer: str | None = None
    unit_cost: Decimal | None = None
    currency: str | None = None
    datasheet_url: str | None = None
    alt_part_number: str | None = None
    is_fitted: bool
    notes: str | None = None
    part_id: int | None = None
    part_number: str | None = None


from .customers import (
    list_customers,
    create_customer,
    delete_customer,
    DeleteBlockedError,
)
from .projects import list_projects, create_project, delete_project
from .assemblies import list_assemblies, list_bom_items, create_assembly, delete_assembly
from .tasks import list_tasks
from .bom_import import ImportReport, validate_headers, import_bom
from .bom_read_models import JoinedBOMRow, get_joined_bom_for_assembly
from .parts import update_part_active_passive, update_part_datasheet_url, update_part_function
from .datasheets import (
    DATASHEET_STORE,
    sha256_of_file,
    canonical_path_for_hash,
    ensure_store_dirs,
    register_datasheet_for_part,
)

__all__ = [
    "list_customers",
    "create_customer",
    "list_projects",
    "create_project",
    "delete_project",
    "list_assemblies",
    "list_bom_items",
    "create_assembly",
    "delete_assembly",
    "delete_customer",
    "DeleteBlockedError",
    "list_tasks",
    "BOMItemRead",
    "ImportReport",
    "validate_headers",
    "import_bom",
    "JoinedBOMRow",
    "get_joined_bom_for_assembly",
    "update_part_active_passive",
    "update_part_datasheet_url",
    "update_part_function",
    "DATASHEET_STORE",
    "sha256_of_file",
    "canonical_path_for_hash",
    "ensure_store_dirs",
    "register_datasheet_for_part",
]

