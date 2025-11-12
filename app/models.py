from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Column,
    JSON,
    Enum as SAEnum,
    Text,
    Index,
    CheckConstraint,
)
from sqlmodel import SQLModel, Field

if SQLModel.metadata.tables:
    SQLModel.metadata.clear()


class UserRole(str, Enum):
    admin = "admin"
    user = "user"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    role: UserRole = Field(default=UserRole.user)

    # backward-compat alias
    @property
    def hashed_pw(self) -> str:  # pragma: no cover - simple alias
        return self.hashed_password

    @hashed_pw.setter
    def hashed_pw(self, v: str) -> None:  # pragma: no cover - simple alias
        self.hashed_password = v


class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    contact_email: Optional[str] = None
    active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectStatus(str, Enum):
    draft = "draft"
    active = "active"
    on_hold = "on_hold"
    done = "done"


class ProjectPriority(str, Enum):
    low = "low"
    med = "med"
    high = "high"
    urgent = "urgent"


class TestMode(str, Enum):
    powered = "powered"
    unpowered = "unpowered"


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    code: str
    title: str
    name: Optional[str] = None  # legacy compatibility
    status: ProjectStatus = Field(default=ProjectStatus.draft)
    priority: ProjectPriority = Field(default=ProjectPriority.med)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None


class Assembly(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    rev: str
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    test_mode: TestMode = Field(
        default=TestMode.unpowered,
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            server_default=TestMode.unpowered.value,
        ),
    )


class PartType(str, Enum):
    active = "active"
    passive = "passive"


class TestProfile(str, Enum):
    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"


class Part(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    part_number: str = Field(unique=True, nullable=False, index=True)
    description: Optional[str] = None
    package: Optional[str] = None
    value: Optional[str] = None
    function: Optional[str] = None
    active_passive: PartType = Field(default=PartType.passive)
    power_required: bool = False
    datasheet_url: Optional[str] = None
    product_url: Optional[str] = None
    tol_p: Optional[str] = Field(default=None, max_length=8)
    tol_n: Optional[str] = Field(default=None, max_length=8)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TestMethod(str, Enum):
    macro = "macro"
    complex = "complex"
    python = "python"
    quick_test = "quick_test"


class PartTestAssignment(SQLModel, table=True):
    part_id: int = Field(primary_key=True, foreign_key="part.id")
    method: TestMethod = Field(
        default=TestMethod.macro,
        sa_column=Column(
            SAEnum(TestMethod, name="testmethod"),
            nullable=False,
            server_default=TestMethod.macro.value,
        ),
    )
    notes: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TestMacro(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    glb_path: Optional[str] = None
    notes: Optional[str] = None


class PythonTest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    file_path: Optional[str] = None
    notes: Optional[str] = None


class PartTestMap(SQLModel, table=True):
    __tablename__ = "part_test_map"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN test_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN python_test_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="ck_part_test_map_exactly_one_test",
        ),
        Index("ptm_part_profile_mode_idx", "part_id", "profile", "power_mode"),
        Index("ptm_part_power_mode_idx", "part_id", "power_mode"),
    )

    part_id: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("part.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    power_mode: TestMode = Field(
        default=TestMode.unpowered,
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            primary_key=True,
        ),
    )
    profile: TestProfile = Field(
        default=TestProfile.PASSIVE,
        sa_column=Column(
            SAEnum(TestProfile, name="test_profile_enum"),
            nullable=False,
            primary_key=True,
            server_default=TestProfile.PASSIVE.value,
        ),
    )
    test_macro_id: int | None = Field(
        default=None,
        sa_column=Column(
            "test_id",
            sa.Integer,
            sa.ForeignKey("testmacro.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    python_test_id: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("pythontest.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )


class BOMItemTestOverride(SQLModel, table=True):
    __tablename__ = "bom_item_test_override"
    __table_args__ = (
        Index("bom_item_override_mode_idx", "bom_item_id", "power_mode"),
        CheckConstraint(
            "((CASE WHEN test_macro_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN python_test_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="ck_bom_item_test_override_one_source",
        ),
    )

    bom_item_id: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("bomitem.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    power_mode: TestMode = Field(
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            primary_key=True,
        ),
    )
    test_macro_id: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("testmacro.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    python_test_id: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("pythontest.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )


class BOMItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    assembly_id: int = Field(foreign_key="assembly.id")
    part_id: Optional[int] = Field(default=None, foreign_key="part.id")
    reference: str = Field(max_length=64, nullable=False)
    qty: int = Field(default=1, nullable=False)
    manufacturer: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    currency: Optional[str] = None
    datasheet_url: Optional[str] = None
    alt_part_number: Optional[str] = None
    is_fitted: bool = True
    notes: Optional[str] = None


class TaskStatus(str, Enum):
    todo = "todo"
    doing = "doing"
    done = "done"


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    title: str
    description: Optional[str] = None
    status: TaskStatus = Field(default=TaskStatus.todo)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditAction(str, Enum):
    insert = "insert"
    update = "update"
    delete = "delete"


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str
    entity_id: int
    action: AuditAction
    diff: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    ts: datetime = Field(default_factory=datetime.utcnow)


class SchematicTokenKind(str, Enum):
    refdes = "refdes"
    pn = "pn"
    net = "net"


class SchematicIndexSource(str, Enum):
    vector = "vector"
    ocr = "ocr"


class SchematicOcrStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class SchematicPack(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    assembly_id: int = Field(foreign_key="assembly.id", index=True)
    display_name: str = Field(nullable=False)
    pack_revision: int = Field(default=1, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SchematicFile(SQLModel, table=True):
    __table_args__ = (
        sa.UniqueConstraint("pack_id", "file_order", name="uq_schematic_file_order"),
        Index("ix_schematic_file_pack_order", "pack_id", "file_order"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    pack_id: int = Field(foreign_key="schematicpack.id", index=True)
    file_order: int = Field(default=1, nullable=False)
    relative_path: str = Field(sa_column=Column(Text, nullable=False))
    page_count: int = Field(default=0, nullable=False)
    has_text_layer: bool = Field(default=False, nullable=False)
    ocr_status: SchematicOcrStatus = Field(
        default=SchematicOcrStatus.pending,
        sa_column=Column(
            SAEnum(SchematicOcrStatus, name="schematic_ocr_status"),
            server_default=SchematicOcrStatus.pending.value,
        ),
    )
    last_indexed_at: datetime | None = Field(default=None)


class SchematicPage(SQLModel, table=True):
    __table_args__ = (
        Index("ix_schematic_page_file_page", "file_id", "page_num"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="schematicfile.id", index=True)
    page_num: int = Field(nullable=False)
    ocr_backed: bool = Field(default=False, nullable=False)


class SchematicIndex(SQLModel, table=True):
    __table_args__ = (
        Index("ix_schematic_index_token", "token_norm"),
        Index("ix_schematic_index_file_page", "file_id", "page_num"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="schematicfile.id", index=True)
    page_num: int = Field(nullable=False)
    token_raw: str = Field(sa_column=Column(Text, nullable=False))
    token_norm: str = Field(sa_column=Column(Text, nullable=False))
    kind: SchematicTokenKind = Field(
        sa_column=Column(
            SAEnum(SchematicTokenKind, name="schematic_token_kind"),
            nullable=False,
        ),
    )
    boxes_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source: SchematicIndexSource = Field(
        default=SchematicIndexSource.vector,
        sa_column=Column(
            SAEnum(SchematicIndexSource, name="schematic_index_source"),
            server_default=SchematicIndexSource.vector.value,
        ),
    )
