"""API client abstraction for the debug GUI.

This module defines :class:`BaseClient` which wraps the small subset of
functionality required by the GUI.  Concrete implementations simply need to
implement :meth:`request` to perform an HTTP like call and return an object
with ``status_code``, ``json()``, ``text`` and ``content`` attributes (matching
``httpx.Response``/``requests.Response``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseClient(ABC):
    """Small facade used by the GUI panels.

    The interface purposely mirrors the common subset between ``requests`` and
    ``httpx`` responses so that the same widgets can operate with either the
    in-memory TestClient or a real HTTP client.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # request helpers
    def get(self, path: str, params: Optional[Dict[str, Any]] = None):
        return self.request("GET", path, params=params)

    def post(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ):
        return self.request(
            "POST", path, params=params, data=data, json=json, files=files
        )

    def patch(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ):
        return self.request("PATCH", path, params=params, data=data, json=json)

    def delete(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        return self.request("DELETE", path, params=params, data=data)

    # ------------------------------------------------------------------
    @abstractmethod
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
        """Perform a request and return the raw response object."""

    # ------------------------------------------------------------------
    def set_token(self, token: Optional[str]) -> None:
        """Set the bearer token to be used for subsequent requests."""

        self._token = token

    # ------------------------------------------------------------------
    def _auth_header(self) -> Dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    def is_local(self) -> bool:
        """Return ``True`` if this client talks to an in-memory API."""

        return False
