"""Application configuration values and helpers.

Centralizes runtime configuration for:
  - Database URL (supports local file or server URL)
  - Data directories (e.g., datasheets store)

Values can be provided via environment variables or a user settings file at
``~/.bom_platform/settings.toml``.

Environment variables (quick overrides):
  - DATABASE_URL: full SQLAlchemy URL; overrides settings.toml
  - BOM_DATA_ROOT: base directory for application data
  - BOM_DATASHEETS_DIR: directory for the datasheets store
  - BOM_MAX_DS_MB: max datasheet size (MB)
"""

from pathlib import Path
import os
from sqlmodel import create_engine
from sqlalchemy.engine import Engine

# TOML read/write helpers: prefer stdlib tomllib (3.11+),
# fall back to third-party toml if available; otherwise write minimal TOML.
try:  # Python 3.11+
    import tomllib as _toml_reader  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - environment-dependent
    _toml_reader = None  # type: ignore

try:
    import toml as _toml_rw  # type: ignore
except Exception:  # pragma: no cover - environment-dependent
    _toml_rw = None  # type: ignore

SETTINGS_PATH = Path.home() / ".bom_platform" / "settings.toml"
DEFAULT_URL = f"sqlite:///{Path.home() / '.bom_platform' / 'bom_dev.db'}"

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent


def _ensure_settings() -> None:
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"database": {"url": DEFAULT_URL}}
        if _toml_rw is not None:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                _toml_rw.dump(data, f)  # type: ignore[attr-defined]
        else:
            # Minimal TOML writer
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                f.write("[database]\n")
                f.write(f"url = \"{DEFAULT_URL}\"\n")


def load_settings() -> str:
    """Return database URL from env or settings.toml."""
    _ensure_settings()
    url = os.getenv("DATABASE_URL")
    if SETTINGS_PATH.exists():
        try:
            if _toml_reader is not None:
                with open(SETTINGS_PATH, "rb") as f:
                    data = _toml_reader.load(f)  # type: ignore[arg-type]
            elif _toml_rw is not None:
                data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
            else:
                data = {}
        except Exception:
            data = {}
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


# ------------------------- Path configuration -------------------------
# A single place to control on-disk locations for files/folders so that
# the program can run locally while data is on a server or cloud share.

def _from_settings(section: str, key: str, default: str) -> str:
    # Read a string from settings.toml (if present)
    try:
        if _toml_reader is not None and SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "rb") as f:
                data = _toml_reader.load(f)  # type: ignore[arg-type]
            return str(((data.get(section) or {}).get(key)) or default)
        elif _toml_rw is not None and SETTINGS_PATH.exists():
            data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
            return str(((data.get(section) or {}).get(key)) or default)
    except Exception:
        pass
    return default


# Base data root: can point to a local folder, network share (e.g., \\server\share),
# or a synced cloud directory. Defaults to <repo>/data.
DATA_ROOT: Path = Path(
    os.getenv("BOM_DATA_ROOT",
             _from_settings("paths", "data_root", str(REPO_ROOT / "data")))
).resolve()

# Datasheets directory: defaults under DATA_ROOT/datasheets unless explicitly overridden.
DATASHEETS_DIR: Path = Path(
    os.getenv(
        "BOM_DATASHEETS_DIR",
        _from_settings("paths", "datasheets_dir", str(DATA_ROOT / "datasheets")),
    )
).resolve()


def save_paths_config(data_root: Path | None = None, datasheets_dir: Path | None = None) -> None:
    """Persist path configuration into settings.toml.

    Any value left as None is preserved.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Load existing
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as f:
            data = _toml_reader.load(f)  # type: ignore[arg-type]
    elif _toml_rw is not None and SETTINGS_PATH.exists():
        data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    else:
        data = {}
    paths = dict(data.get("paths", {}))
    if data_root is not None:
        paths["data_root"] = str(Path(data_root).resolve())
    if datasheets_dir is not None:
        paths["datasheets_dir"] = str(Path(datasheets_dir).resolve())
    data["paths"] = paths
    # Write back
    if _toml_rw is not None:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            _toml_rw.dump(data, f)  # type: ignore[attr-defined]
    else:
        # Minimal TOML writer
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            # database section
            f.write("[database]\n")
            f.write(f"url = \"{data.get('database', {}).get('url', DEFAULT_URL)}\"\n\n")
            # paths section
            f.write("[paths]\n")
            if "data_root" in paths:
                f.write(f"data_root = \"{paths['data_root']}\"\n")
            if "datasheets_dir" in paths:
                f.write(f"datasheets_dir = \"{paths['datasheets_dir']}\"\n")


def save_database_url(url: str) -> None:
    SETTINGS_PATH.parent.mkdir(exist_ok=True)
    data = {"database": {"url": url}}
    if _toml_rw is not None:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            _toml_rw.dump(data, f)  # type: ignore[attr-defined]
    else:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            f.write("[database]\n")
            f.write(f"url = \"{url}\"\n")

SECRET_KEY = "secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Max allowed datasheet download size in megabytes (default 50MB)
MAX_DATASHEET_MB = int(os.getenv("BOM_MAX_DS_MB", 50))

# Max upload sizes for test assets
MAX_GLB_MB = int(os.getenv("BOM_MAX_GLB_MB", 10))
MAX_EDA_MB = int(os.getenv("BOM_MAX_EDA_MB", 20))
MAX_PY_MB = int(os.getenv("BOM_MAX_PY_MB", 1))

# Hourly assembly cost used for quotes
BOM_HOURLY_USD = float(os.getenv("BOM_HOURLY_USD", 25))

# Default currency for BOM items and quotes
BOM_DEFAULT_CURRENCY = os.getenv("BOM_DEFAULT_CURRENCY", "USD")

# Hours to keep cached FX rates
FX_CACHE_HOURS = int(os.getenv("FX_CACHE_HOURS", 24))

# Preferred PDF viewer for opening datasheets from the UI.
# Options: 'system' (default), 'chrome', 'edge', or an absolute path in BOM_PDF_VIEWER_PATH
def _as_bool(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")

# PDF viewer preferences (env overrides settings.toml)
PDF_VIEWER = (
    os.getenv("BOM_PDF_VIEWER")
    or _from_settings("viewer", "pdf_viewer", "chrome")
).strip().lower()
PDF_VIEWER_PATH = (
    os.getenv("BOM_PDF_VIEWER_PATH")
    or _from_settings("viewer", "pdf_viewer_path", "")
).strip()
PDF_OPEN_DEBUG = _as_bool(
    os.getenv("BOM_PDF_OPEN_DEBUG")
    or _from_settings("viewer", "pdf_open_debug", "1")
)

def save_viewer_config(pdf_viewer: str | None = None, pdf_viewer_path: str | None = None, pdf_open_debug: bool | None = None) -> None:
    """Persist viewer preferences into settings.toml.

    Any value left as None is preserved.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Load existing
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as f:
            data = _toml_reader.load(f)  # type: ignore[arg-type]
    elif _toml_rw is not None and SETTINGS_PATH.exists():
        data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    else:
        data = {}
    viewer = dict(data.get("viewer", {}))
    if pdf_viewer is not None:
        viewer["pdf_viewer"] = str(pdf_viewer).strip().lower()
    if pdf_viewer_path is not None:
        viewer["pdf_viewer_path"] = str(pdf_viewer_path)
    if pdf_open_debug is not None:
        viewer["pdf_open_debug"] = bool(pdf_open_debug)
    data["viewer"] = viewer
    # Write back
    if _toml_rw is not None:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            _toml_rw.dump(data, f)  # type: ignore[attr-defined]
    else:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            # database section
            f.write("[database]\n")
            f.write(f"url = \"{data.get('database', {}).get('url', DEFAULT_URL)}\"\n\n")
            # paths section
            if "paths" in data:
                f.write("[paths]\n")
                for k, v in (data["paths"] or {}).items():
                    f.write(f"{k} = \"{v}\"\n")
                f.write("\n")
            # viewer section
            f.write("[viewer]\n")
            if "pdf_viewer" in viewer:
                f.write(f"pdf_viewer = \"{viewer['pdf_viewer']}\"\n")
            if "pdf_viewer_path" in viewer:
                f.write(f"pdf_viewer_path = \"{viewer['pdf_viewer_path']}\"\n")
            if "pdf_open_debug" in viewer:
                f.write(f"pdf_open_debug = {str(viewer['pdf_open_debug']).lower()}\n")
