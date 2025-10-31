from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, Column, JSON, CheckConstraint, Enum as SAEnum, Index
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


class TestMode(str, Enum):
    powered = "powered"
    unpowered = "unpowered"


class Assembly(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    rev: str
    notes: Optional[str] = None
    test_mode: TestMode = Field(
        default=TestMode.unpowered,
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            server_default=TestMode.unpowered.value,
        ),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PartType(str, Enum):
    active = "active"
    passive = "passive"


class TestProfile(str, Enum):
    active = "active"
    passive = "passive"


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


class TestMacro(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, nullable=False, unique=True)
    glb_path: Optional[str] = Field(default=None, max_length=512)
    notes: Optional[str] = Field(default=None)


class PythonTest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128, nullable=False, unique=True)
    file_path: Optional[str] = Field(default=None, max_length=512)
    notes: Optional[str] = Field(default=None)


class PartTestMap(SQLModel, table=True):
    part_id: int = Field(foreign_key="part.id", primary_key=True)
    power_mode: TestMode = Field(
        default=TestMode.unpowered,
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            server_default=TestMode.unpowered.value,
            primary_key=True,
        ),
    )
    profile: TestProfile = Field(
        sa_column=Column(
            SAEnum(TestProfile, name="test_profile_enum"),
            nullable=False,
            primary_key=True,
        ),
    )
    test_macro_id: Optional[int] = Field(default=None, foreign_key="testmacro.id")
    python_test_id: Optional[int] = Field(default=None, foreign_key="pythontest.id")
    detail: Optional[str] = Field(default=None)

    __table_args__ = (
        CheckConstraint(
            "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
            "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
            name="ck_part_test_map_single_source",
        ),
        Index("ix_part_test_map_part_profile_mode", "part_id", "profile", "power_mode"),
        Index("ix_part_test_map_part_mode", "part_id", "power_mode"),
    )


class BOMItemTestOverride(SQLModel, table=True):
    bom_item_id: int = Field(foreign_key="bomitem.id", primary_key=True)
    power_mode: TestMode = Field(
        default=TestMode.unpowered,
        sa_column=Column(
            SAEnum(TestMode, name="test_mode_enum"),
            nullable=False,
            server_default=TestMode.unpowered.value,
            primary_key=True,
        ),
    )
    test_macro_id: Optional[int] = Field(default=None, foreign_key="testmacro.id")
    python_test_id: Optional[int] = Field(default=None, foreign_key="pythontest.id")
    detail: Optional[str] = Field(default=None)

    __table_args__ = (
        CheckConstraint(
            "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
            "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
            name="ck_bom_item_test_override_single_source",
        ),
        Index("ix_bom_item_test_override_item_mode", "bom_item_id", "power_mode"),
    )


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
