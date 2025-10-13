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

from __future__ import annotations

import atexit
from pathlib import Path
import os
import sys
import copy
from contextlib import suppress
from typing import Any, Dict, Mapping, Optional
from sqlmodel import create_engine
from sqlalchemy.engine import Engine, make_url

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

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

PORTABLE_INTERNAL_DIRNAME = "internal"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return REPO_ROOT

RUNTIME_ROOT = _runtime_root()

def _is_dir_writable(path: Path) -> bool:
    test_file = path / '.__bom_write_test'
    try:
        path.mkdir(parents=True, exist_ok=True)
        with open(test_file, 'w', encoding='utf-8') as handle:
            handle.write('ok')
        return True
    except Exception:
        return False
    finally:
        with suppress(Exception):
            test_file.unlink()

def _portable_internal_root() -> Path:
    """Return the folder that should contain portable runtime assets."""

    return RUNTIME_ROOT / PORTABLE_INTERNAL_DIRNAME


def _resolve_writable_runtime_root() -> Path:
    if getattr(sys, 'frozen', False):
        internal_root = _portable_internal_root()
        if _is_dir_writable(internal_root):
            return internal_root
        runtime_root = RUNTIME_ROOT
        if _is_dir_writable(runtime_root):
            return runtime_root
    fallback = Path.home() / '.bom_platform'
    _is_dir_writable(fallback)
    return fallback

APP_STORAGE_ROOT = _resolve_writable_runtime_root()

def _determine_settings_path() -> Path:
    override = os.getenv("BOM_SETTINGS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    runtime_settings = RUNTIME_ROOT / "settings.toml"
    if getattr(sys, "frozen", False):
        return (APP_STORAGE_ROOT / "settings.toml").resolve()
    if runtime_settings.exists():
        return runtime_settings
    return (Path.home() / ".bom_platform" / "settings.toml").resolve()

SETTINGS_PATH = _determine_settings_path()

def _ensure_sqlite_directory(url: str) -> str:
    try:
        url_obj = make_url(url)
    except Exception:
        return url
    if url_obj.get_backend_name() != "sqlite":
        return url
    database = url_obj.database or ""
    if not database or database == ":memory:" or database.startswith("file:"):
        return url
    db_path = Path(database)
    if not db_path.is_absolute():
        base_dir = SETTINGS_PATH.parent
        db_path = (base_dir / db_path).resolve()
        url_obj = url_obj.set(database=db_path.as_posix())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(url_obj)

if getattr(sys, "frozen", False):
    _default_db_path = (APP_STORAGE_ROOT / "app.db").resolve()
else:
    _default_db_path = (APP_STORAGE_ROOT / "bom_dev.db").resolve()
DEFAULT_URL = _ensure_sqlite_directory(f"sqlite:///{_default_db_path.as_posix()}")

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

def _read_settings_dict() -> Dict[str, Any]:
    _ensure_settings()
    if not SETTINGS_PATH.exists():
        return {}
    try:
        if _toml_reader is not None:
            with open(SETTINGS_PATH, "rb") as handle:
                return _toml_reader.load(handle)  # type: ignore[arg-type]
        if _toml_rw is not None:
            return _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    except Exception:
        return {}
    return {}

_BOOL_TRUE_VALUES = {"1", "true", "yes", "on"}


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, (int, float)):
            candidate = int(value)
        else:
            candidate = int(float(str(value).strip()))
        if candidate > 0:
            return candidate
    except Exception:
        pass
    return default


def _load_max_datasheet_mb(default: int = 25) -> int:
    env_value = os.getenv("BOM_MAX_DS_MB")
    if env_value is not None:
        return _coerce_positive_int(env_value, default)
    data = _read_settings_dict().get("datasheets")
    if isinstance(data, Mapping):
        for key in ("max_datasheet_mb", "max_size_mb", "max_mb"):
            if key in data:
                return _coerce_positive_int(data[key], default)
    return default

def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    text_value = str(value)
    escaped = text_value.replace('\\', '\\').replace('"', '\"')
    return f"\"{escaped}\""

def _write_settings_data(data: Dict[str, Any]) -> None:
    """Persist `data` into SETTINGS_PATH using TOML representation.
    When the optional `toml` writer is unavailable we emit a very small
    TOML subset that supports nested tables containing primitive values.
    Unknown value types are ignored to avoid corrupting the settings file.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _toml_rw is not None:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as handle:
            _toml_rw.dump(data, handle)  # type: ignore[attr-defined]
        return
    def _write_table(handle, table_name: str, table_data: Mapping[str, Any]) -> None:
        simple: Dict[str, Any] = {}
        nested: Dict[str, Mapping[str, Any]] = {}
        for key, value in table_data.items():
            if isinstance(value, Mapping):
                nested[str(key)] = value  # type: ignore[assignment]
            else:
                simple[str(key)] = value
        handle.write(f'[{table_name}]\n')
        for key, value in simple.items():
            scalar = _toml_scalar(value)
            if not scalar:
                continue
            handle.write(f"{key} = {scalar}\n")
        handle.write('\n')
        for key, value in nested.items():
            _write_table(handle, f"{table_name}.{key}", value)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as handle:
        for key, value in data.items():
            if isinstance(value, Mapping):
                _write_table(handle, str(key), value)
                handle.write('\n')

def _merge_settings_section(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(target)
    for key, value in updates.items():
        if value is None:
            continue
        merged[key] = value
    return merged

def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _BOOL_TRUE_VALUES
    if isinstance(value, (int, float)):
        return bool(value)
    return default

_COMPLEX_EDITOR_DEFAULTS: Dict[str, Any] = {
    "ui_enabled": True,
    "exe_path": "",
    "config_path": "",
    "auto_start_bridge": True,
    "auto_stop_bridge_on_exit": False,
    "bridge": {
        "enabled": True,
        "base_url": "http://127.0.0.1:8765",
        "auth_token": "",
        "request_timeout_seconds": 10,
    },
    "note_or_link": "",
}

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
    raw_url = url or DEFAULT_URL
    return _ensure_sqlite_directory(raw_url)

DATABASE_URL = load_settings()
_ENGINE: Engine = create_engine(DATABASE_URL, echo=False)


def dispose_engine() -> None:
    """Dispose the global engine, releasing any pooled connections."""
    global _ENGINE
    try:
        _ENGINE.dispose()
    except Exception:
        pass


atexit.register(dispose_engine)

def get_engine(url: Optional[str] = None) -> Engine:
    """Return engine, recreating if the URL changed."""
    global _ENGINE, DATABASE_URL
    new_url = _ensure_sqlite_directory(url) if url is not None else load_settings()
    if new_url != DATABASE_URL:
        DATABASE_URL = new_url
        _ENGINE.dispose()
        _ENGINE = create_engine(DATABASE_URL, echo=False)
    return _ENGINE

def reload_settings() -> None:
    """Reload settings from disk and rebuild engine if needed."""
    global MAX_DATASHEET_MB
    get_engine(load_settings())
    refresh_paths()
    MAX_DATASHEET_MB = _load_max_datasheet_mb()

def _from_settings(section: str, key: str, default: str) -> str:
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

# ------------------------- Path configuration -------------------------
# A single place to control on-disk locations for files/folders so that
# the program can run locally while data is on a server or cloud share.

def _value_from_env_or_settings(env: str, section: str, key: str, default: str) -> str:
    return os.getenv(env) or _from_settings(section, key, default)

def _compute_paths() -> dict[str, Path]:
    _ensure_settings()
    data_root_default = str((APP_STORAGE_ROOT / "data") if getattr(sys, "frozen", False) else (REPO_ROOT / "data"))
    data_root = Path(
        _value_from_env_or_settings("BOM_DATA_ROOT", "paths", "data_root", data_root_default)
    ).expanduser().resolve()
    datasheets_default = str(data_root / "datasheets")
    datasheets_dir = Path(
        _value_from_env_or_settings("BOM_DATASHEETS_DIR", "paths", "datasheets_dir", datasheets_default)
    ).expanduser().resolve()
    log_dir_default = str(data_root / "logs")
    log_dir = Path(
        _value_from_env_or_settings("BOM_LOG_DIR", "paths", "log_dir", log_dir_default)
    ).expanduser().resolve()
    ai_log_default = str(log_dir / "ai_ops.ndjson")
    ai_log = Path(
        _value_from_env_or_settings("BOM_AI_LOG", "paths", "ai_log", ai_log_default)
    ).expanduser().resolve()
    traceback_default = str(log_dir / "tracebacks.log")
    trace_log = Path(
        _value_from_env_or_settings("BOM_TRACEBACK_LOG", "paths", "traceback_log", traceback_default)
    ).expanduser().resolve()
    agents_default = str(
        (APP_STORAGE_ROOT / "agents.local.toml") if getattr(sys, "frozen", False) else (REPO_ROOT / "agents.local.toml")
    )
    agents_file = Path(
        _value_from_env_or_settings("BOM_AGENTS_FILE", "paths", "agents_file", agents_default)
    ).expanduser().resolve()
    return {
        "data_root": data_root,
        "datasheets_dir": datasheets_dir,
        "log_dir": log_dir,
        "ai_log": ai_log,
        "trace_log": trace_log,
        "agents_file": agents_file,
    }

_paths = _compute_paths()
DATA_ROOT: Path = _paths["data_root"]
DATASHEETS_DIR: Path = _paths["datasheets_dir"]
LOG_DIR: Path = _paths["log_dir"]
AI_LOG_PATH: Path = _paths["ai_log"]
TRACEBACK_LOG_PATH: Path = _paths["trace_log"]
AGENTS_FILE_PATH: Path = _paths["agents_file"]
DATA_ROOT.mkdir(parents=True, exist_ok=True)
DATASHEETS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

def refresh_paths() -> None:
    global DATA_ROOT, DATASHEETS_DIR, LOG_DIR, AI_LOG_PATH, TRACEBACK_LOG_PATH, AGENTS_FILE_PATH
    paths = _compute_paths()
    DATA_ROOT = paths["data_root"]
    DATASHEETS_DIR = paths["datasheets_dir"]
    LOG_DIR = paths["log_dir"]
    AI_LOG_PATH = paths["ai_log"]
    TRACEBACK_LOG_PATH = paths["trace_log"]
    AGENTS_FILE_PATH = paths["agents_file"]
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    DATASHEETS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

def get_agents_file_path() -> Path:
    return AGENTS_FILE_PATH

def save_paths_config(
    data_root: Optional[Path] = None,
    datasheets_dir: Optional[Path] = None,
    agents_file: Optional[Path] = None,
) -> None:
    """Persist path configuration into settings.toml.
    Any value left as None is preserved.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as handle:
            data = _toml_reader.load(handle)  # type: ignore[arg-type]
    elif _toml_rw is not None and SETTINGS_PATH.exists():
        data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    else:
        data = {}
    paths = dict(data.get("paths", {}))
    if data_root is not None:
        paths["data_root"] = str(Path(data_root).expanduser().resolve())
    if datasheets_dir is not None:
        paths["datasheets_dir"] = str(Path(datasheets_dir).expanduser().resolve())
    if agents_file is not None:
        paths["agents_file"] = str(Path(agents_file).expanduser().resolve())
    data["paths"] = paths
    _write_settings_data(data)
    refresh_paths()

def save_database_url(url: str) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as handle:
            data = _toml_reader.load(handle)  # type: ignore[arg-type]
    elif _toml_rw is not None and SETTINGS_PATH.exists():
        data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    else:
        data = {}
    database = dict(data.get("database", {}))
    database["url"] = _ensure_sqlite_directory(url)
    data["database"] = database
    _write_settings_data(data)
    reload_settings()

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

MAX_DATASHEET_MB = _load_max_datasheet_mb()

def get_complex_editor_settings() -> Dict[str, Any]:
    """Return Complex Editor UI/bridge configuration with defaults applied."""
    settings = copy.deepcopy(_COMPLEX_EDITOR_DEFAULTS)
    data = _read_settings_dict().get("complex_editor")
    if isinstance(data, dict):
        settings["ui_enabled"] = _coerce_bool(data.get("ui_enabled"), settings["ui_enabled"])
        exe_path = data.get("exe_path")
        if exe_path is not None:
            settings["exe_path"] = str(exe_path).strip()
        config_path = data.get("config_path")
        if config_path is not None:
            settings["config_path"] = str(config_path).strip()
        settings["auto_start_bridge"] = _coerce_bool(
            data.get("auto_start_bridge"), settings["auto_start_bridge"]
        )
        settings["auto_stop_bridge_on_exit"] = _coerce_bool(
            data.get("auto_stop_bridge_on_exit"), settings["auto_stop_bridge_on_exit"]
        )
        note = data.get("note_or_link")
        if note is not None:
            settings["note_or_link"] = str(note).strip()
        bridge_cfg = data.get("bridge")
        target_bridge = settings["bridge"]
        if isinstance(bridge_cfg, dict):
            target_bridge["enabled"] = _coerce_bool(
                bridge_cfg.get("enabled"), target_bridge["enabled"]
            )
            base_url = bridge_cfg.get("base_url")
            if base_url is not None:
                base = str(base_url).strip()
                if base:
                    target_bridge["base_url"] = base
            token = bridge_cfg.get("auth_token")
            if token is not None:
                target_bridge["auth_token"] = str(token).strip()
            timeout = bridge_cfg.get("request_timeout_seconds")
            try:
                timeout_val = int(timeout)
            except (TypeError, ValueError):
                timeout_val = None
            if timeout_val and timeout_val > 0:
                target_bridge["request_timeout_seconds"] = timeout_val
    return settings
def save_complex_editor_settings(
    *,
    exe_path: Optional[str] = None,
    config_path: Optional[str] = None,
    auto_start_bridge: Optional[bool] = None,
    auto_stop_bridge_on_exit: Optional[bool] = None,
    bridge_enabled: Optional[bool] = None,
    bridge_base_url: Optional[str] = None,
    bridge_auth_token: Optional[str] = None,
    bridge_request_timeout_seconds: Optional[int] = None,
    note_or_link: Optional[str] = None,
    ui_enabled: Optional[bool] = None,
) -> None:
    """Persist Complex Editor settings into settings.toml."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as handle:
            data = _toml_reader.load(handle)  # type: ignore[arg-type]
    elif _toml_rw is not None and SETTINGS_PATH.exists():
        data = _toml_rw.load(SETTINGS_PATH)  # type: ignore[call-arg]
    else:
        data = {}
    ce_cfg = dict(data.get("complex_editor", {}))
    if ui_enabled is not None:
        ce_cfg["ui_enabled"] = bool(ui_enabled)
    if exe_path is not None:
        ce_cfg["exe_path"] = str(exe_path).strip()
    if config_path is not None:
        ce_cfg["config_path"] = str(config_path).strip()
    if auto_start_bridge is not None:
        ce_cfg["auto_start_bridge"] = bool(auto_start_bridge)
    if auto_stop_bridge_on_exit is not None:
        ce_cfg["auto_stop_bridge_on_exit"] = bool(auto_stop_bridge_on_exit)
    if note_or_link is not None:
        ce_cfg["note_or_link"] = str(note_or_link).strip()
    bridge_cfg = dict(ce_cfg.get("bridge", {}))
    if bridge_enabled is not None:
        bridge_cfg["enabled"] = bool(bridge_enabled)
    if bridge_base_url is not None:
        bridge_cfg["base_url"] = str(bridge_base_url).strip()
    if bridge_auth_token is not None:
        bridge_cfg["auth_token"] = str(bridge_auth_token).strip()
    if bridge_request_timeout_seconds is not None:
        try:
            timeout_val = int(bridge_request_timeout_seconds)
        except (TypeError, ValueError):
            timeout_val = None
        else:
            if timeout_val > 0:
                bridge_cfg["request_timeout_seconds"] = timeout_val
    ce_cfg["bridge"] = bridge_cfg
    data["complex_editor"] = ce_cfg
    _write_settings_data(data)

def save_viewer_config(
    pdf_viewer: Optional[str] = None,
    pdf_viewer_path: Optional[str] = None,
    pdf_open_debug: Optional[bool] = None,
) -> None:
    """Persist viewer preferences into settings.toml.
    Any value left as None is preserved.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _toml_reader is not None and SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "rb") as handle:
            data = _toml_reader.load(handle)  # type: ignore[arg-type]
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
    _write_settings_data(data)

