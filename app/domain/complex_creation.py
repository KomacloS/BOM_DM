from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from app.integration import ce_bridge_client
from app.integration.ce_bridge_manager import launch_ce_wizard

logger = logging.getLogger(__name__)


@dataclass
class WizardLaunchResult:
    pn: str
    aliases: list[str]
    buffer_path: Path


def launch_wizard(pn: str, aliases: Sequence[str] | None = None) -> WizardLaunchResult:
    alias_list = [
        alias.strip()
        for alias in (aliases or [])
        if isinstance(alias, str) and alias.strip()
    ]
    buffer_path = launch_ce_wizard(pn, alias_list)
    return WizardLaunchResult(pn=pn, aliases=alias_list, buffer_path=buffer_path)


def cleanup_buffer(path: Optional[os.PathLike[str] | str]) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # pragma: no cover - defensive cleanup
        logger.debug("Failed to delete Complex Editor wizard buffer %s", path, exc_info=True)


def _normalize(value: str) -> str:
    return (value or "").strip().lower()


def _match_aliases(target: str, aliases: Iterable[object]) -> bool:
    for alias in aliases:
        if isinstance(alias, str) and _normalize(alias) == target:
            return True
    return False


def select_exact_match(pn: str, matches: Iterable[dict]) -> Optional[dict]:
    target = _normalize(pn)
    if not target:
        return None
    exact: list[dict] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        item_pn = _normalize(str(item.get("pn") or item.get("part_number") or ""))
        aliases = item.get("aliases") or []
        if item_pn == target or _match_aliases(target, aliases):
            exact.append(item)
    if len(exact) != 1:
        return None
    return exact[0]


@dataclass
class WizardPollResult:
    attached: bool
    ce_id: Optional[str] = None


class WizardPoller:
    """Poll Complex Editor search results and attach on a unique match."""

    def __init__(
        self,
        part_id: int,
        pn: str,
        *,
        limit: int = 5,
        search: Callable[[str, int], list[dict]] | None = None,
        attach: Callable[[int, str], None] | None = None,
    ) -> None:
        self._part_id = part_id
        self._pn = pn
        self._limit = limit
        self._search = search or (lambda pn, limit=5: ce_bridge_client.search_complexes(pn, limit=limit))
        self._attach = attach

    def poll_once(self) -> WizardPollResult:
        matches = self._search(self._pn, self._limit)
        chosen = select_exact_match(self._pn, matches)
        if not chosen:
            return WizardPollResult(attached=False)
        ce_id = chosen.get("id") or chosen.get("ce_id")
        if not ce_id:
            return WizardPollResult(attached=False)
        if self._attach is not None:
            self._attach(self._part_id, str(ce_id))
        return WizardPollResult(attached=True, ce_id=str(ce_id))
