from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    mdb_path_value = os.environ.get("CE_MDB_PATH")
    if not mdb_path_value:
        print(
            "CE fallback runner requires CE_MDB_PATH to point to the Complex Editor database.",
            file=sys.stderr,
        )
        sys.exit(2)

    mdb_path = Path(mdb_path_value).expanduser()
    if not mdb_path.exists():
        print(
            f"CE fallback runner could not find CE_MDB_PATH at {mdb_path}.",
            file=sys.stderr,
        )
        sys.exit(2)

    from ce_bridge_service.app import create_app  # lazy import to avoid optional dependency cost

    app = create_app(
        get_mdb_path=lambda: mdb_path,
        allow_headless_exports=_env_truthy(os.environ.get("CE_ALLOW_HEADLESS_EXPORTS")),
        auth_token=os.environ.get("CE_AUTH_TOKEN", ""),
    )

    host = os.environ.get("CE_BRIDGE_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("CE_BRIDGE_PORT", "8765"))
    except ValueError:
        print(
            f"Invalid CE_BRIDGE_PORT value: {os.environ.get('CE_BRIDGE_PORT')}",
            file=sys.stderr,
        )
        sys.exit(2)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover - manual invocation path
    main()
