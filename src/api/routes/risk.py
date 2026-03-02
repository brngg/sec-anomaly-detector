"""Issuer risk score endpoints."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_db
from ..schemas import RiskExplanation, RiskScore, RiskScoreHistory, RiskScoreList

router = APIRouter(tags=["risk"])


def _parse_json_text(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _row_to_risk_score(row: sqlite3.Row) -> RiskScore:
    data = dict(row)
    data["evidence"] = _parse_json_text(data.get("evidence"))
    return RiskScore(**data)


def _company_exists(db: sqlite3.Connection, cik: int) -> bool:
    row = db.execute("SELECT 1 FROM companies WHERE cik = ?", (cik,)).fetchone()
    return row is not None


def _resolve_latest_as_of_date(
    db: sqlite3.Connection,
    cik: Optional[int] = None,
    model_version: Optional[str] = None,
    min_score: Optional[float] = None,
) -> Optional[str]:
    where = []
    params: list[object] = []

    if cik is not None:
        where.append("cik = ?")
        params.append(cik)
    if model_version:
        where.append("model_version = ?")
        params.append(model_version)
    if min_score is not None:
        where.append("risk_score >= ?")
        params.append(min_score)

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    row = db.execute(
        f"SELECT MAX(as_of_date) AS as_of_date FROM issuer_risk_scores{where_sql}",
        tuple(params),
    ).fetchone()
    if row is None:
        return None
    return row["as_of_date"]


@router.get("/risk/top", response_model=RiskScoreList)
def list_top_risk(
    as_of_date: Optional[str] = Query(None, description="As-of date in YYYY-MM-DD"),
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> RiskScoreList:
    effective_as_of_date = as_of_date or _resolve_latest_as_of_date(
        db,
        model_version=model_version,
        min_score=min_score,
    )

    if effective_as_of_date is None:
        return RiskScoreList(
            items=[],
            total=0,
            limit=limit,
            offset=offset,
            as_of_date=None,
            model_version=model_version,
        )

    where = ["r.as_of_date = ?"]
    params: list[object] = [effective_as_of_date]

    if model_version:
        where.append("r.model_version = ?")
        params.append(model_version)
    if min_score is not None:
        where.append("r.risk_score >= ?")
        params.append(min_score)

    where_sql = " WHERE " + " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) AS count FROM issuer_risk_scores r{where_sql}",
        tuple(params),
    ).fetchone()["count"]

    rows = db.execute(
        f"""
        SELECT
            r.score_id,
            r.cik,
            r.as_of_date,
            r.model_version,
            r.risk_score,
            r.risk_rank,
            r.percentile,
            r.evidence,
            r.created_at,
            r.updated_at,
            c.name AS company_name,
            c.ticker AS company_ticker
        FROM issuer_risk_scores r
        LEFT JOIN companies c ON c.cik = r.cik
        {where_sql}
        ORDER BY
            CASE WHEN r.risk_rank IS NULL THEN 1 ELSE 0 END,
            r.risk_rank ASC,
            r.risk_score DESC,
            r.cik ASC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, limit, offset]),
    ).fetchall()

    items = [_row_to_risk_score(row) for row in rows]
    return RiskScoreList(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        as_of_date=effective_as_of_date,
        model_version=model_version,
    )


@router.get("/risk/{cik}/history", response_model=RiskScoreHistory)
def get_risk_history(
    cik: int,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    date_from: Optional[str] = Query(None, description="Inclusive start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Inclusive end date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> RiskScoreHistory:
    if not _company_exists(db, cik):
        raise HTTPException(status_code=404, detail="Company not found")

    where = ["r.cik = ?"]
    params: list[object] = [cik]

    if model_version:
        where.append("r.model_version = ?")
        params.append(model_version)
    if date_from:
        where.append("r.as_of_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("r.as_of_date <= ?")
        params.append(date_to)

    where_sql = " WHERE " + " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) AS count FROM issuer_risk_scores r{where_sql}",
        tuple(params),
    ).fetchone()["count"]

    rows = db.execute(
        f"""
        SELECT
            r.score_id,
            r.cik,
            r.as_of_date,
            r.model_version,
            r.risk_score,
            r.risk_rank,
            r.percentile,
            r.evidence,
            r.created_at,
            r.updated_at,
            c.name AS company_name,
            c.ticker AS company_ticker
        FROM issuer_risk_scores r
        LEFT JOIN companies c ON c.cik = r.cik
        {where_sql}
        ORDER BY
            r.as_of_date DESC,
            CASE WHEN r.risk_rank IS NULL THEN 1 ELSE 0 END,
            r.risk_rank ASC,
            r.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, limit, offset]),
    ).fetchall()

    items = [_row_to_risk_score(row) for row in rows]
    return RiskScoreHistory(
        cik=cik,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        model_version=model_version,
    )


@router.get("/risk/{cik}/explain", response_model=RiskExplanation)
def get_risk_explanation(
    cik: int,
    as_of_date: Optional[str] = Query(None, description="As-of date in YYYY-MM-DD"),
    model_version: Optional[str] = Query(None, description="Model version"),
    db: sqlite3.Connection = Depends(get_db),
) -> RiskExplanation:
    if not _company_exists(db, cik):
        raise HTTPException(status_code=404, detail="Company not found")

    effective_as_of_date = as_of_date or _resolve_latest_as_of_date(
        db,
        cik=cik,
        model_version=model_version,
    )
    if effective_as_of_date is None:
        raise HTTPException(status_code=404, detail="Risk score not found")

    where = ["r.cik = ?", "r.as_of_date = ?"]
    params: list[object] = [cik, effective_as_of_date]

    if model_version:
        where.append("r.model_version = ?")
        params.append(model_version)

    where_sql = " WHERE " + " AND ".join(where)
    row = db.execute(
        f"""
        SELECT
            r.score_id,
            r.cik,
            r.as_of_date,
            r.model_version,
            r.risk_score,
            r.risk_rank,
            r.percentile,
            r.evidence,
            r.created_at,
            r.updated_at,
            c.name AS company_name,
            c.ticker AS company_ticker
        FROM issuer_risk_scores r
        LEFT JOIN companies c ON c.cik = r.cik
        {where_sql}
        ORDER BY r.updated_at DESC, r.model_version DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Risk score not found")

    return RiskExplanation(score=_row_to_risk_score(row))
