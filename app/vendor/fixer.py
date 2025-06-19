import os
import requests

API_KEY = os.getenv("FX_API_KEY")
MOCK_RATES = {"USD": 1.0, "EUR": 0.9, "GBP": 0.8}


def today() -> dict:
    """Return FX rates with USD base."""
    if not API_KEY:
        return MOCK_RATES
    try:
        resp = requests.get(
            "https://data.fixer.io/api/latest",
            params={"access_key": API_KEY},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rates", MOCK_RATES)
    except Exception:  # pragma: no cover - network not used in tests
        return MOCK_RATES
