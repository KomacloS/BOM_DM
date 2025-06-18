"""Application configuration values and helpers."""

from pathlib import Path
import os
import toml
from sqlmodel import create_engine
from sqlalchemy.engine import Engine

SETTINGS_PATH = Path.home() / ".bom_platform" / "settings.toml"
DEFAULT_URL = f"sqlite:///{Path.home() / '.bom_platform' / 'bom_dev.db'}"

BASE_DIR = Path(__file__).resolve().parent


def _ensure_settings() -> None:
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            toml.dump({"database": {"url": DEFAULT_URL}}, f)


def load_settings() -> str:
    """Return database URL from env or settings.toml."""
    _ensure_settings()
    url = os.getenv("DATABASE_URL")
    if SETTINGS_PATH.exists():
        data = toml.load(SETTINGS_PATH)
        url = data.get("database", {}).get("url", url)
    return url or DEFAULT_URL


DATABASE_URL = load_settings()
_ENGINE: Engine = create_engine(DATABASE_URL, echo=False)


def get_engine(url: str | None = None) -> Engine:
    """Return engine, recreating if the URL changed."""
    global _ENGINE, DATABASE_URL
    new_url = url or load_settings()
    if new_url != DATABASE_URL:
        DATABASE_URL = new_url
        _ENGINE.dispose()
        _ENGINE = create_engine(DATABASE_URL, echo=False)
    return _ENGINE


def reload_settings() -> None:
    """Reload settings from disk and rebuild engine if needed."""
    get_engine(load_settings())


def save_database_url(url: str) -> None:
    SETTINGS_PATH.parent.mkdir(exist_ok=True)
    data = {"database": {"url": url}}
    with open(SETTINGS_PATH, "w") as f:
        toml.dump(data, f)

SECRET_KEY = "secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Max allowed datasheet upload size in megabytes
MAX_DATASHEET_MB = int(os.getenv("BOM_MAX_DS_MB", 10))
