from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlmodel import Field, SQLModel, Session, select

from app import database
from app.integration import ce_bridge_client
from app.integration.ce_bridge_client import (
    CEAuthError,
    CENetworkError,
    CENotFound,
    CEAliasConflict,
    CEBusyError,
    CEStaleLink,
    CEUserCancelled,
    add_aliases,
    open_complex,
    search_complexes,
)
from app.integration.ce_bridge_manager import record_bridge_action

logger = logging.getLogger(__name__)


class ComplexLink(SQLModel, table=True):
    __tablename__ = "complex_links"
    id: Optional[int] = Field(default=None, primary_key=True)
    part_id: int = Field(index=True, nullable=False, unique=True)
    ce_db_uri: Optional[str] = Field(default=None)
    ce_complex_id: str = Field(index=True, nullable=False)
    ce_pn: Optional[str] = Field(default=None)
    aliases: Optional[str] = Field(default=None)
    pin_map: Optional[str] = Field(default=None)
    macro_ids: Optional[str] = Field(default=None)
    source_hash: Optional[str] = Field(default=None)
    total_pins: int = Field(default=0, nullable=False)
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


def attach_existing_complex(part_id: int, ce_id: str) -> dict[str, Any]:
    """Fetch CE detail and upsert a link+snapshot row for this part."""
    data = ce_bridge_client.get_complex(ce_id)
    snapshot_id = str(data.get("id") or ce_id)
    total_pins_raw = data.get("total_pins")
    try:
        total_pins_int = int(total_pins_raw)
    except (TypeError, ValueError):
        total_pins_int = 0
    record_data = {
        "part_id": part_id,
        "ce_complex_id": snapshot_id,
        "ce_db_uri": data.get("db_path"),
        "ce_pn": data.get("pn"),
        "aliases": _json_dump(data.get("aliases"), "[]"),
        "pin_map": _json_dump(data.get("pin_map"), "{}"),
        "macro_ids": _json_dump(data.get("macro_ids"), "[]"),
        "source_hash": data.get("source_hash"),
        "total_pins": total_pins_int,
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
        return record_data
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CEBusyEditorError(Exception):
    """Raised when Complex Editor is busy (wizard already open)."""

class CEStaleLinkError(Exception):
    """Raised when the stored Complex link no longer exists in CE."""

class CESelectionRequired(Exception):
    """Raised when the user must choose a Complex Editor record."""

    def __init__(self, matches: Sequence[dict[str, Any]]) -> None:
        super().__init__("Multiple Complex Editor matches available")
        self.matches: List[dict[str, Any]] = [dict(item) for item in matches if isinstance(item, dict)]


class AliasConflictError(Exception):
    """Raised when a BOM part number conflicts with an existing Complex alias."""

    def __init__(self, ce_id: str | int, conflicts: Sequence[str]) -> None:
        self.ce_id = ce_id
        self.conflicts = list(conflicts)
        msg = f"Alias already belongs to Complex {ce_id}"
        if self.conflicts:
            msg = f"{msg}: {', '.join(self.conflicts)}"
        super().__init__(msg)


def _select_exact_match(pn: str, matches: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    target = (pn or "").strip().lower()
    if not target:
        return None
    exact = [
        match
        for match in matches
        if isinstance(match, dict) and str(match.get("pn") or "").strip().lower() == target
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def create_and_attach_complex(
    part_id: int,
    pn: str,
    aliases: list[str] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    created = ce_bridge_client.create_complex(pn, aliases, status_callback=status_callback)
    ce_id = created.get("id") or created.get("ce_id")
    if not ce_id:
        matches = ce_bridge_client.search_complexes(pn, limit=10)
        matches = [m for m in matches if isinstance(m, dict) and (m.get("id") or m.get("ce_id"))]
        preferred = _select_exact_match(pn, matches)
        if preferred:
            ce_id = preferred.get("id") or preferred.get("ce_id")
        else:
            raise CESelectionRequired(matches)
    record = attach_existing_complex(part_id, str(ce_id))
    logger.info("Created Complex %s for part %s", ce_id, part_id)
    return record


def attach_as_alias_and_link(
    part_id: int,
    pn: str,
    target_ce_id: str | int,
    *,
    status_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def _update_status(message: str) -> None:
        if status_callback:
            status_callback(message)

    _update_status("Adding alias in Complex Editor...")
    try:
        add_aliases(target_ce_id, [pn])
    except CEAliasConflict as exc:
        record_bridge_action(
            f"Alias conflict for {pn} when linking to Complex {exc.ce_id} ({', '.join(exc.conflicts)})"
        )
        raise AliasConflictError(exc.ce_id, exc.conflicts) from exc

    record_bridge_action(f"Alias {pn} added to Complex {target_ce_id}; linking part {part_id}")
    _update_status("Linking Complex Editor record...")
    return attach_existing_complex(part_id, str(target_ce_id))


@dataclass
class OpenInCEResult:
    ce_id: str
    already_open: bool
    link_record: Optional[dict[str, Any]] = None


def _normalize_open_context(link_or_pn: Any) -> Tuple[Optional[dict[str, Any]], Optional[str], Optional[int]]:
    def _as_dict(candidate: Any) -> Optional[dict[str, Any]]:
        if isinstance(candidate, Mapping):
            return {str(key): candidate[key] for key in candidate}
        return None

    link: Optional[dict[str, Any]] = None
    pn: Optional[str] = None
    part_id: Optional[int] = None

    if isinstance(link_or_pn, ComplexLink):
        link = link_or_pn.dict()
        part_id = link_or_pn.part_id
        pn = link_or_pn.ce_pn or pn
    elif isinstance(link_or_pn, Mapping):
        mapping = _as_dict(link_or_pn) or {}
        nested_link = mapping.get("link") or mapping.get("link_record")
        if isinstance(nested_link, Mapping):
            link = _as_dict(nested_link)
        elif "ce_complex_id" in mapping or "ce_id" in mapping:
            link = mapping
        if mapping.get("pn") is not None:
            pn = str(mapping["pn"])
        elif mapping.get("part_number") is not None:
            pn = str(mapping["part_number"])
        part_value = mapping.get("part_id")
        if part_value is None and link is not None:
            part_value = link.get("part_id")
        if part_value is not None:
            try:
                part_id = int(part_value)
            except (TypeError, ValueError):
                part_id = None
    elif isinstance(link_or_pn, (list, tuple)):
        values = list(link_or_pn)
        if values:
            link_candidate = values[0]
            if isinstance(link_candidate, Mapping):
                link = _as_dict(link_candidate)
        if len(values) > 1 and values[1] is not None:
            pn = str(values[1])
        if len(values) > 2 and values[2] is not None:
            try:
                part_id = int(values[2])
            except (TypeError, ValueError):
                part_id = None
    elif isinstance(link_or_pn, str):
        pn = link_or_pn

    if link is not None:
        pn_value = link.get("ce_pn") or link.get("pn")
        if isinstance(pn_value, str) and not pn:
            pn = pn_value
        part_value = link.get("part_id")
        if part_id is None and part_value is not None:
            try:
                part_id = int(part_value)
            except (TypeError, ValueError):
                part_id = None
    return link, pn, part_id


def open_in_ce(
    link_or_pn: Any,
    status_callback: Callable[[str], None] | None = None,
    *,
    chooser: Optional[Callable[[List[dict[str, Any]]], Tuple[Optional[dict[str, Any]], bool]]] = None,
    use_cached_preflight: bool = True,
) -> OpenInCEResult:
    link, pn, part_id = _normalize_open_context(link_or_pn)

    ce_id_value = None
    if link:
        ce_id_value = link.get("ce_complex_id") or link.get("ce_id")
    if ce_id_value is None:
        pn = (pn or "").strip()
        if not pn:
            raise CENotFound("Part number is required to open in Complex Editor.")
        record_bridge_action(f"Searching Complex Editor for PN '{pn}'")
        matches = search_complexes(pn, limit=20)
        items = [dict(item) for item in matches if isinstance(item, dict)]
        if not items:
            raise CENotFound("No Complex Editor entry matches this part number.")
        selection: Optional[dict[str, Any]] = None
        attach_first = False
        if chooser is not None:
            selection, attach_first = chooser(items)
            if selection is None:
                raise CEUserCancelled("Selection cancelled")
        elif len(items) == 1:
            selection = items[0]
        else:
            raise CESelectionRequired(items)

        ce_id_value = selection.get("id") or selection.get("ce_id")
        if ce_id_value is None:
            raise CENetworkError("Selected Complex Editor record is missing an id.")
        if attach_first:
            if part_id is not None:
                link = attach_as_alias_and_link(part_id, pn, ce_id_value, status_callback=status_callback)
            else:
                record_bridge_action(
                    f"Alias add requested for PN '{pn}' and Complex {ce_id_value}, but part_id unavailable; skipping."
                )

    if ce_id_value is None:
        raise CENetworkError("Unable to resolve Complex Editor record for opening.")

    ce_id = str(ce_id_value)
    link_record: Optional[dict[str, Any]] = dict(link) if isinstance(link, Mapping) else None

    try:
        already_open = open_complex(
            ce_id,
            status_callback=status_callback,
            allow_cached=use_cached_preflight,
        )
    except CEStaleLink as exc:
        record_bridge_action(f"Attempted to open stale link ce_id={exc.ce_id}")
        raise CEStaleLinkError(str(exc.ce_id)) from exc
    except CEBusyError as exc:
        raise CEBusyEditorError(str(exc)) from exc

    return OpenInCEResult(
        ce_id=ce_id,
        already_open=bool(already_open),
        link_record=link_record,
    )

def unlink_existing_complex(part_id: int, *, user_initiated: bool = True) -> bool:
    session = _session()
    try:
        stmt = select(ComplexLink).where(ComplexLink.part_id == part_id)
        link = session.exec(stmt).first()
        if link is None:
            return False
        ce_id = link.ce_complex_id
        ce_pn = getattr(link, "ce_pn", None)
        session.delete(link)
        session.commit()
        record_bridge_action(f"Unlinked CE link (ce_id={ce_id}, pn=\"{ce_pn or ''}\")")
        logger.info(
            "Unlinked Complex %s (pn=%s) for part %s (user_initiated=%s)",
            ce_id,
            ce_pn or "",
            part_id,
            user_initiated,
        )
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_link_stale(
    ce_client,
    link: dict[str, Any],
) -> tuple[bool, str]:
    ce_id = link.get("ce_complex_id")
    if not ce_id:
        return False, ""
    try:
        ce_client.get_complex(ce_id)
    except CEAuthError:
        return False, "auth"
    except CENotFound:
        record_bridge_action(f"Stale link detected (ce_id={ce_id}): CE returned 404")
        logger.info("Detected stale Complex Editor link %s (404 from CE)", ce_id)
        return True, "not_found"
    except CENetworkError:
        return False, "transient"
    return False, ""


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
