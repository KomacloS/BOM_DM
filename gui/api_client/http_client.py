"""HTTP client backend using :mod:`httpx`."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .base import BaseClient


class HTTPClient(BaseClient):
    """Client that talks to a running API server via HTTP."""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=10.0)

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
        try:
            return self._client.request(
                method,
                path,
                params=params,
                data=data,
                json=json,
                files=files,
                headers=headers,
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network errors
            class _Error:
                def __init__(self, message: str) -> None:
                    self.status_code = 0
                    self.text = message
                    self.content = b""

                def json(self) -> Dict[str, Any]:
                    return {}

            return _Error(str(exc))

    def close(self) -> None:
        self._client.close()
