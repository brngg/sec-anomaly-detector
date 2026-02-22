"""Filing endpoints."""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_db
from ..schemas import FilingEvent, FilingList

router = APIRouter(tags=["filings"])


@router.get("/companies/{cik}/filings", response_model=FilingList)
def list_company_filings(
    cik: int,
    filing_type: Optional[str] = Query(None, description="Filter by filing type (e.g., 8-K)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> FilingList:
    where = ["cik = ?"]
    params: list[object] = [cik]

    if filing_type:
        where.append("filing_type = ?")
        params.append(filing_type)

    where_sql = " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) AS count FROM filing_events WHERE {where_sql}",
        tuple(params),
    ).fetchone()["count"]

    params_with_page = params + [limit, offset]
    rows = db.execute(
        f"""
        SELECT accession_id, cik, filing_type, filed_at, filed_date, primary_document
        FROM filing_events
        WHERE {where_sql}
        ORDER BY filed_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params_with_page),
    ).fetchall()

    items = [FilingEvent(**dict(row)) for row in rows]
    return FilingList(items=items, total=total, limit=limit, offset=offset)


@router.get("/filings/{accession_id}", response_model=FilingEvent)
def get_filing(
    accession_id: str,
    db: sqlite3.Connection = Depends(get_db),
) -> FilingEvent:
    row = db.execute(
        """
        SELECT accession_id, cik, filing_type, filed_at, filed_date, primary_document
        FROM filing_events
        WHERE accession_id = ?
        """,
        (accession_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Filing not found")

    return FilingEvent(**dict(row))
