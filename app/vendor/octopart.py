import os
import requests
from ..config import BOM_DEFAULT_CURRENCY

TOKEN = os.getenv("OCTOPART_TOKEN")

MOCK_DATA = {"KNOWN": 0.42}


def lookup(mpn: str) -> dict:
    """Return price info for given MPN."""
    if not TOKEN:
        if mpn in MOCK_DATA:
            return {"price": MOCK_DATA[mpn], "currency": BOM_DEFAULT_CURRENCY}
        raise KeyError(mpn)
    try:
        resp = requests.get(
            "https://octopart.com/api/v4/parts/search",
            params={"q": mpn, "apikey": TOKEN},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        price = data["results"][0]["item"]["offers"][0]["prices"]["USD"][0][1]
        return {"price": price, "currency": "USD"}
    except Exception as e:  # pragma: no cover - network not used in tests
        raise KeyError(mpn) from e
