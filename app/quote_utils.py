# root: app/quote_utils.py
from __future__ import annotations
from typing import TYPE_CHECKING

BASE_SETUP_TIME = 60  # seconds
SEC_PER_COMPONENT = 7
BASE_COST_USD = 100
COST_PER_COMP = 0.07

if TYPE_CHECKING:  # pragma: no cover - for type hints only
    from .main import BOMItem

def calculate_quote(items: list["BOMItem"]) -> dict:
    """Return rough time and cost estimates for the given BOM items."""
    total_quantity = sum(getattr(item, "quantity", 0) for item in items)
    time_sec = BASE_SETUP_TIME + SEC_PER_COMPONENT * total_quantity
    cost_usd = BASE_COST_USD + COST_PER_COMP * total_quantity
    return {
        "total_components": total_quantity,
        "estimated_time_s": time_sec,
        "estimated_cost_usd": round(cost_usd, 2),
    }
