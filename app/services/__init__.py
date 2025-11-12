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
    test_method: str | None = None
    test_detail: str | None = None
    test_method_powered: str | None = None
    test_detail_powered: str | None = None
    test_resolution_source: str | None = None
    test_resolution_message: str | None = None


from .customers import (
    list_customers,
    create_customer,
    delete_customer,
    DeleteBlockedError,
)
from .projects import list_projects, create_project, delete_project
from .assemblies import (
    list_assemblies,
    list_bom_items,
    create_assembly,
    delete_assembly,
    delete_bom_items,
    delete_bom_items_for_part,
    update_bom_item_manufacturer,
    update_manufacturer_for_part_in_assembly,
    update_assembly_test_mode,
)
from .tasks import list_tasks
from .bom_import import ImportReport, validate_headers, import_bom
from .bom_read_models import JoinedBOMRow, get_joined_bom_for_assembly
from .parts import (
    create_part,
    search_parts,
    update_part,
    count_part_references,
    unlink_part_from_boms,
    delete_part,
    update_part_active_passive,
    update_part_datasheet_url,
    update_part_product_url,
    update_part_description_if_empty,
    update_part_description,
    remove_part_datasheet,
    update_part_function,
    update_part_package,
    update_part_value,
    update_part_tolerances,
    clear_part_datasheet,
)
from .export_viva import (
    VIVABOMLine,
    VIVAExportDiagnostics,
    VIVAExportOutcome,
    VIVAExportPaths,
    VIVAMissingComplex,
    VIVAExportValidationError,
    build_export_folder_name,
    build_export_paths,
    build_viva_groups,
    collect_bom_lines,
    determine_comp_ids,
    perform_viva_export,
    sanitize_token,
    write_viva_txt,
)
from .bom_to_ce_export import export_bom_to_ce_bridge
from .datasheets import (
    DATASHEET_STORE,
    sha256_of_file,
    canonical_path_for_hash,
    ensure_store_dirs,
    register_datasheet_for_part,
)
from .test_defaults import (
    upsert_part_test_map,
    upsert_python_test,
    upsert_test_macro,
)
from .schematics import (
    SchematicFileInfo,
    SchematicPackInfo,
    list_schematic_packs,
    create_schematic_pack,
    get_pack_detail,
    rename_schematic_pack,
    add_schematic_file_from_path,
    add_schematic_files_from_uploads,
    replace_schematic_file_from_path,
    remove_schematic_file,
    reorder_schematic_files,
    mark_schematic_file_reindexed,
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
    "delete_bom_items",
    "delete_bom_items_for_part",
    "update_assembly_test_mode",
    "update_bom_item_manufacturer",
    "update_manufacturer_for_part_in_assembly",
    "delete_customer",
    "DeleteBlockedError",
    "list_tasks",
    "BOMItemRead",
    "ImportReport",
    "validate_headers",
    "import_bom",
    "JoinedBOMRow",
    "get_joined_bom_for_assembly",
    "create_part",
    "search_parts",
    "update_part",
    "count_part_references",
    "unlink_part_from_boms",
    "delete_part",
    "update_part_active_passive",
    "update_part_datasheet_url",
    "update_part_product_url",
    "update_part_description_if_empty",
    "update_part_description",
    "remove_part_datasheet",
    "update_part_function",
    "update_part_package",
    "update_part_value",
    "update_part_tolerances",
    "clear_part_datasheet",
    "VIVABOMLine",
    "VIVAExportDiagnostics",
    "VIVAExportOutcome",
    "VIVAExportPaths",
    "VIVAMissingComplex",
    "VIVAExportValidationError",
    "build_export_folder_name",
    "build_export_paths",
    "build_viva_groups",
    "collect_bom_lines",
    "determine_comp_ids",
    "perform_viva_export",
    "sanitize_token",
    "write_viva_txt",
    "export_bom_to_ce_bridge",
    "DATASHEET_STORE",
    "sha256_of_file",
    "canonical_path_for_hash",
    "ensure_store_dirs",
    "register_datasheet_for_part",
    "upsert_part_test_map",
    "upsert_python_test",
    "upsert_test_macro",
    "SchematicFileInfo",
    "SchematicPackInfo",
    "list_schematic_packs",
    "create_schematic_pack",
    "get_pack_detail",
    "rename_schematic_pack",
    "add_schematic_file_from_path",
    "add_schematic_files_from_uploads",
    "replace_schematic_file_from_path",
    "remove_schematic_file",
    "reorder_schematic_files",
    "mark_schematic_file_reindexed",
]

