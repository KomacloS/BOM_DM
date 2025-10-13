from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from sqlmodel import Field, SQLModel, Session, select

from app import config, database
from app.domain import complex_creation
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CENetworkError
from app.integration.ce_bridge_manager import CEBridgeError

logger = logging.getLogger(__name__)


class ComplexLink(SQLModel, table=True):
    __tablename__ = "complex_links"
    id: Optional[int] = Field(default=None, primary_key=True)
    part_id: int = Field(index=True, nullable=False, unique=True)
    ce_db_uri: Optional[str] = Field(default=None)
    ce_complex_id: str = Field(index=True, nullable=False)
    aliases: Optional[str] = Field(default=None)
    pin_map: Optional[str] = Field(default=None)
    macro_ids: Optional[str] = Field(default=None)
    source_hash: Optional[str] = Field(default=None)
    synced_at: Optional[str] = Field(default=None)
    created_at: Optional[str] = Field(default=None)
    updated_at: Optional[str] = Field(default=None)


def _json_dump(value: Any, empty: str) -> str:
    if value in (None, ""):
        return empty
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except TypeError:
        return empty


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _session() -> Session:
    sess = database.new_session()
    return sess


def attach_existing_complex(part_id: int, ce_id: str) -> None:
    """Fetch CE detail and upsert a link+snapshot row for this part."""
    data = ce_bridge_client.get_complex(ce_id)
    snapshot_id = str(data.get("id") or ce_id)
    record_data = {
        "part_id": part_id,
        "ce_complex_id": snapshot_id,
        "ce_db_uri": data.get("db_path"),
        "aliases": _json_dump(data.get("aliases"), "[]"),
        "pin_map": _json_dump(data.get("pin_map"), "{}"),
        "macro_ids": _json_dump(data.get("macro_ids"), "[]"),
        "source_hash": data.get("source_hash"),
        "synced_at": _utc_iso(),
    }

    session = _session()
    try:
        stmt = select(ComplexLink).where(ComplexLink.part_id == part_id)
        existing = session.exec(stmt).first()
        if existing:
            for key, value in record_data.items():
                setattr(existing, key, value)
        else:
            existing = ComplexLink(**record_data)
            session.add(existing)
        existing.updated_at = record_data["synced_at"]
        session.add(existing)
        session.commit()
        logger.info("Attached Complex %s to part %s", record_data["ce_complex_id"], part_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class CreateComplexOutcome:
    status: Literal["attached", "wizard", "cancelled"]
    message: str
    created_id: Optional[str] = None
    buffer_path: Optional[str] = None
    pn: Optional[str] = None
    aliases: Optional[list[str]] = None
    polling_enabled: bool = False


class CEWizardLaunchError(CENetworkError):
    """Raised when the Complex Editor GUI wizard cannot be launched."""

    def __init__(self, message: str, *, fix_in_settings: bool = False) -> None:
        super().__init__(message)
        self.fix_in_settings = fix_in_settings


def _bridge_polling_enabled(settings: Optional[dict[str, Any]]) -> bool:
    if not isinstance(settings, dict):
        return False
    bridge_cfg = settings.get("bridge", {})
    if not isinstance(bridge_cfg, dict):
        return False
    return bool(bridge_cfg.get("enabled", True))


def _needs_settings_fix(error: CEBridgeError) -> bool:
    message = str(error).lower()
    return "complex editor executable" in message or "config" in message


def create_and_attach_complex(
    part_id: int, pn: str, aliases: list[str] | None = None
) -> CreateComplexOutcome:
    try:
        launch = complex_creation.launch_wizard(pn, aliases or [])
    except CEBridgeError as exc:
        raise CEWizardLaunchError(str(exc), fix_in_settings=_needs_settings_fix(exc)) from exc

    message = f"Opened Complex Editor wizard for {pn}. Save in CE to complete."
    settings = config.get_complex_editor_settings()
    logger.info("Launched Complex Editor wizard for part %s", pn)
    return CreateComplexOutcome(
        status="wizard",
        message=message,
        buffer_path=str(launch.buffer_path),
        pn=launch.pn,
        aliases=launch.aliases,
        polling_enabled=_bridge_polling_enabled(settings),
    )


def auto_link_by_pn(part_id: int, pn: str, limit: int = 10) -> bool:
    """If exactly one exact match by PN or alias from CE, attach and return True; else False."""
    target = (pn or "").strip()
    if not target:
        return False
    try:
        matches = ce_bridge_client.search_complexes(pn, limit=limit)
    except CENetworkError:
        logger.info("Complex Editor bridge unavailable during auto-link for part %s", part_id)
        return False
    chosen = complex_creation.select_exact_match(pn, matches)
    if not chosen:
        return False
    ce_id = chosen.get("id") or chosen.get("ce_id")
    if not ce_id:
        return False
    attach_existing_complex(part_id, str(ce_id))
    logger.info("Auto-linked Complex %s to part %s by PN '%s'", ce_id, part_id, pn)
    return True
