"""Service layer for business logic.

This package exposes thin wrappers around common CRUD workflows used by
both the desktop GUI and the forthcoming FastAPI layer.  Each function is
UI agnostic and operates directly on a SQLModel ``Session`` instance.

The modules re-export the most commonly used functions so existing imports
like ``from app.services import import_bom`` continue to work.
"""

from .customers import (
    list_customers,
    create_customer,
    delete_customer,
    DeleteBlockedError,
)
from .projects import list_projects, create_project, delete_project
from .assemblies import list_assemblies, create_assembly, delete_assembly
from .tasks import list_tasks
from .bom_import import ImportReport, validate_headers, import_bom

__all__ = [
    "list_customers",
    "create_customer",
    "list_projects",
    "create_project",
    "delete_project",
    "list_assemblies",
    "create_assembly",
    "delete_assembly",
    "delete_customer",
    "DeleteBlockedError",
    "list_tasks",
    "ImportReport",
    "validate_headers",
    "import_bom",
]

