"""Application configuration values."""

from pathlib import Path
import os
import toml

SETTINGS_PATH = Path.home() / ".bom_platform" / "settings.toml"

BASE_DIR = Path(__file__).resolve().parent


def load_settings() -> str:
    """Return database URL from env or settings.toml."""
    url = os.getenv("DATABASE_URL")
    if SETTINGS_PATH.exists():
        data = toml.load(SETTINGS_PATH)
        url = data.get("database", {}).get("url", url)
    return url or f"sqlite:///{BASE_DIR / 'bom_dev.db'}"


DATABASE_URL = load_settings()


def save_database_url(url: str) -> None:
    SETTINGS_PATH.parent.mkdir(exist_ok=True)
    data = {"database": {"url": url}}
    with open(SETTINGS_PATH, "w") as f:
        toml.dump(data, f)

SECRET_KEY = "secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
