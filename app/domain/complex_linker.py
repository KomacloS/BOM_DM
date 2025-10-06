from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlmodel import Field, SQLModel, Session, select

from app import database
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import CENetworkError

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


def create_and_attach_complex(part_id: int, pn: str, aliases: list[str] | None = None) -> None:
    created = ce_bridge_client.create_complex(pn, aliases)
    attach_existing_complex(part_id, created.get("id") or created.get("ce_id"))
    logger.info("Created Complex %s for part %s", created.get("id"), part_id)


def _match_aliases(target: str, aliases: Iterable[Any]) -> bool:
    for alias in aliases:
        if isinstance(alias, str) and alias.strip().lower() == target:
            return True
    return False


def auto_link_by_pn(part_id: int, pn: str) -> bool:
    """If exactly one exact match by PN or alias from CE, attach and return True; else False."""
    target = (pn or "").strip().lower()
    if not target:
        return False
    try:
        matches = ce_bridge_client.search_complexes(pn, limit=10)
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
