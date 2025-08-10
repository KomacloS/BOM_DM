"""In-memory API client using :class:`fastapi.testclient.TestClient`."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi.testclient import TestClient

from .base import BaseClient


class LocalClient(BaseClient):
    """Client that runs the FastAPI app in-memory.

    This allows the GUI to call the same endpoints without requiring an HTTP
    server to be running.  The first instantiation performs the database
    initialisation by calling :func:`app.main.init_db`.
    """

    def __init__(self) -> None:
        super().__init__()
        from app import main

        # Ensure the database and application are initialised
        main.init_db()
        self._app = main.app
        self._client = TestClient(self._app)

    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ):
        headers = self._auth_header()
        return self._client.request(
            method,
            path,
            params=params,
            data=data,
            json=json,
            files=files,
            headers=headers,
        )

    # ------------------------------------------------------------------
    def is_local(self) -> bool:  # pragma: no cover - trivial
        return True
