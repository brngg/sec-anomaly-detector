"""Company endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_db
from ..schemas import Company, CompanyList

router = APIRouter(tags=["companies"])


@router.get("/companies", response_model=CompanyList)
def list_companies(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> CompanyList:
    total = db.execute("SELECT COUNT(*) AS count FROM companies").fetchone()["count"]

    rows = db.execute(
        """
        SELECT cik, name, ticker, industry, updated_at
        FROM companies
        ORDER BY cik
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    items = [Company(**dict(row)) for row in rows]
    return CompanyList(items=items, total=total, limit=limit, offset=offset)


@router.get("/companies/{cik}", response_model=Company)
def get_company(
    cik: int,
    db: sqlite3.Connection = Depends(get_db),
) -> Company:
    row = db.execute(
        """
        SELECT cik, name, ticker, industry, updated_at
        FROM companies
        WHERE cik = ?
        """,
        (cik,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")

    return Company(**dict(row))
