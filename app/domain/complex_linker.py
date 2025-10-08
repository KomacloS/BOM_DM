from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Literal, Optional

from sqlmodel import Field, SQLModel, Session, select

from app import database
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import (
    CENetworkError,
    CEUserCancelled,
    CEWizardUnavailable,
)
from app.integration.ce_bridge_manager import CEBridgeError, launch_ce_wizard

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


def create_and_attach_complex(
    part_id: int, pn: str, aliases: list[str] | None = None
) -> CreateComplexOutcome:
    try:
        created = ce_bridge_client.create_complex(pn, aliases)
    except CEWizardUnavailable:
        alias_list = aliases or []
        try:
            launch_ce_wizard(pn, alias_list)
        except CEBridgeError as exc:
            raise CENetworkError(str(exc)) from exc
        message = "Opened Complex Editor wizard—finish and we’ll attach automatically…"
        logger.info("Launched Complex Editor wizard for part %s", pn)
        return CreateComplexOutcome(status="wizard", message=message)
    except CEUserCancelled:
        return CreateComplexOutcome(
            status="cancelled", message="Creation cancelled in Complex Editor."
        )

    ce_id = str(created.get("id") or created.get("ce_id") or "").strip()
    if not ce_id:
        raise CENetworkError("Complex Editor bridge returned an incomplete create response.")
    attach_existing_complex(part_id, ce_id)
    logger.info("Created Complex %s for part %s", ce_id, part_id)
    return CreateComplexOutcome(
        status="attached",
        message=f"Created new Complex for part {pn}.",
        created_id=ce_id,
    )


def _match_aliases(target: str, aliases: Iterable[Any]) -> bool:
    for alias in aliases:
        if isinstance(alias, str) and alias.strip().lower() == target:
            return True
    return False


def auto_link_by_pn(part_id: int, pn: str, limit: int = 10) -> bool:
    """If exactly one exact match by PN or alias from CE, attach and return True; else False."""
    target = (pn or "").strip().lower()
    if not target:
        return False
    try:
        matches = ce_bridge_client.search_complexes(pn, limit=limit)
    except CENetworkError:
        logger.info("Complex Editor bridge unavailable during auto-link for part %s", part_id)
        return False
    if not matches:
        return False

    exact_matches = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        item_pn = str(item.get("pn") or item.get("part_number") or "").strip().lower()
        aliases = item.get("aliases") or []
        if item_pn == target or _match_aliases(target, aliases):
            exact_matches.append(item)
    if len(exact_matches) != 1:
        return False
    chosen = exact_matches[0]
    ce_id = chosen.get("id") or chosen.get("ce_id")
    if not ce_id:
        return False
    attach_existing_complex(part_id, str(ce_id))
    logger.info("Auto-linked Complex %s to part %s by PN '%s'", ce_id, part_id, pn)
    return True
