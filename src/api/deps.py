"""API dependency helpers for FastAPI endpoints.

Provides `get_db` dependency that yields a DB connection with foreign keys enabled.
"""
from typing import Generator

from fastapi import Depends

from ..db.db_utils import get_conn


def get_db() -> Generator:
    """FastAPI dependency that yields a sqlite3.Connection with FKs enabled."""
    with get_conn() as conn:
        yield conn
