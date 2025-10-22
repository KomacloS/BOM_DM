from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from sqlmodel import Field, SQLModel, Session, select

from app import config, database
from app.domain import complex_creation
from app.integration import ce_bridge_client, ce_bridge_linker
from app.integration.ce_bridge_client import (
    CEUserCancelled,
    CEWizardUnavailable,
)
from app.integration.ce_bridge_linker import (
    LinkerError,
    LinkerFeatureError,
    LinkerInputError,
)
from app.integration.ce_supervisor import CEBridgeError
from app.models import PartTestAssignment, TestMethod

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


class CEWizardLaunchError(ce_bridge_client.CENetworkError):
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


def _clean_aliases(aliases: list[str] | None) -> list[str]:
    alias_list: list[str] = []
    for alias in aliases or []:
        if isinstance(alias, str):
            text = alias.strip()
            if text:
                alias_list.append(text)
    return alias_list


def _persist_link(part_id: int, ce_id: str) -> None:
    session = _session()
    now_iso = _utc_iso()
    now_dt = datetime.utcnow()
    try:
        stmt = select(ComplexLink).where(ComplexLink.part_id == part_id)
        link = session.exec(stmt).first()
        if link:
            link.ce_complex_id = ce_id
            link.ce_db_uri = link.ce_db_uri or None
            link.aliases = link.aliases or "[]"
            link.pin_map = link.pin_map or "{}"
            link.macro_ids = link.macro_ids or "[]"
            link.synced_at = now_iso
            link.updated_at = now_iso
            if not link.created_at:
                link.created_at = now_iso
        else:
            link = ComplexLink(
                part_id=part_id,
                ce_complex_id=ce_id,
                ce_db_uri=None,
                aliases="[]",
                pin_map="{}",
                macro_ids="[]",
                source_hash=None,
                synced_at=now_iso,
                created_at=now_iso,
                updated_at=now_iso,
            )
            session.add(link)

        assignment = session.get(PartTestAssignment, part_id)
        if assignment is None:
            assignment = PartTestAssignment(part_id=part_id, method=TestMethod.complex)
            assignment.created_at = now_dt
            assignment.updated_at = now_dt
            session.add(assignment)
        else:
            if assignment.method != TestMethod.complex:
                assignment.method = TestMethod.complex
            assignment.updated_at = now_dt
            session.add(assignment)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _refresh_link_snapshot(part_id: int, ce_id: str) -> None:
    try:
        attach_existing_complex(part_id, ce_id)
    except ce_bridge_client.CENetworkError as exc:
        logger.warning(
            "Failed to refresh Complex %s after creation: %s",
            ce_id,
            exc,
        )


def _launch_wizard_flow(
    part_id: int, pn: str, aliases: list[str]
) -> CreateComplexOutcome:
    try:
        launch = complex_creation.launch_wizard(pn, aliases)
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


def create_and_attach_complex(
    part_id: int, pn: str, aliases: list[str] | None = None
) -> CreateComplexOutcome:
    alias_list = _clean_aliases(aliases)

    try:
        ce_bridge_client.wait_until_ready()
        payload = ce_bridge_client.create_complex(pn, alias_list or None)
    except CEUserCancelled:
        logger.info("Complex creation cancelled for part %s", part_id)
        return CreateComplexOutcome(status="cancelled", message="Creation cancelled.")
    except CEWizardUnavailable:
        return _launch_wizard_flow(part_id, pn, alias_list)

    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise ce_bridge_client.CENetworkError(
            "Complex Editor returned an unexpected response for create_complex"
        )

    created = payload.get("id")
    try:
        created_id = int(created)
    except (TypeError, ValueError):
        raise ce_bridge_client.CENetworkError(
            "Complex Editor did not return a valid complex ID"
        ) from None

    ce_id = str(created_id)
    _persist_link(part_id, ce_id)
    _refresh_link_snapshot(part_id, ce_id)

    base_url = ce_bridge_client.get_active_base_url()
    logger.info(
        "Created Complex %s for part %s via bridge %s",
        ce_id,
        part_id,
        base_url,
    )

    message = f"Created Complex {ce_id} and attached to part {part_id}."
    return CreateComplexOutcome(
        status="attached",
        message=message,
        created_id=ce_id,
        pn=pn,
        aliases=alias_list,
        polling_enabled=False,
    )


def auto_link_by_pn(part_id: int, pn: str, limit: int = 10) -> bool:
    """Attempt to auto-link a part using the CE Bridge search analysis."""

    target = (pn or "").strip()
    if not target:
        return False

    try:
        decision = ce_bridge_linker.select_best_match(target, limit=limit)
    except (LinkerInputError, LinkerFeatureError):
        return False
    except LinkerError:
        logger.info("Complex Editor bridge unavailable during auto-link for part %s", part_id)
        return False

    candidate = decision.best
    if candidate is None:
        return False
    if decision.needs_review:
        logger.info(
            "Auto-link skipped for part %s due to ambiguous matches (trace=%s)",
            part_id,
            decision.trace_id,
        )
        return False
    if candidate.match_kind not in {"exact_pn", "exact_alias"}:
        return False

    attach_existing_complex(part_id, str(candidate.id))
    logger.info(
        "Auto-linked Complex %s to part %s by PN '%s' (trace=%s)",
        candidate.id,
        part_id,
        pn,
        decision.trace_id,
    )
    return True
