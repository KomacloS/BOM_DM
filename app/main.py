from __future__ import annotations

"""Minimal FastAPI application entrypoint.

This module re-exports the API application and ensures the database schema
is migrated before serving requests. Legacy endpoints related to PDF parsing,
quoting, or traceability have been removed.
"""

from .api import app as app  # re-use existing API routes
from .database import ensure_schema
from .services import test_assets


@app.on_event("startup")
def _ensure_schema() -> None:
    ensure_schema()
    test_assets.ensure_base_dirs()
