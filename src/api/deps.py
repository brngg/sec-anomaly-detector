"""API dependency helpers for FastAPI endpoints."""

from __future__ import annotations

import os
from typing import Generator

from ..db.db_utils import get_conn


def get_db() -> Generator:
    """Yield DB connection for API routes.

    Uses read-only DSN when `API_DATABASE_URL` / `DATABASE_URL_RO` is configured.
    """
    api_dsn = os.getenv("API_DATABASE_URL") or os.getenv("DATABASE_URL_RO")
    with get_conn(path=None, dsn=api_dsn, read_only=True) as conn:
        yield conn
