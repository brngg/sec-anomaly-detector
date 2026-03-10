"""API dependency helpers for FastAPI endpoints."""

from __future__ import annotations

import os
import secrets
from typing import Generator

from fastapi import Header, HTTPException

from ..db.db_utils import get_conn


def get_db() -> Generator:
    """Yield DB connection for API routes.

    Uses read-only DSN when `API_DATABASE_URL` / `DATABASE_URL_RO` is configured.
    """
    api_dsn = os.getenv("API_DATABASE_URL") or os.getenv("DATABASE_URL_RO")
    with get_conn(path=None, dsn=api_dsn, read_only=True) as conn:
        yield conn


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_api_auth_enabled() -> bool:
    return _is_truthy(os.getenv("API_AUTH_ENABLED"))


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not is_api_auth_enabled():
        return

    expected_api_key = (os.getenv("API_KEY") or "").strip()
    if not expected_api_key:
        raise HTTPException(status_code=500, detail="API auth misconfigured")

    if not x_api_key or not secrets.compare_digest(x_api_key, expected_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
