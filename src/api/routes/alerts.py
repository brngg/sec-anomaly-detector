"""Alert endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_db
from ..schemas import (
    Alert,
    AlertBulkStatusUpdate,
    AlertList,
    AlertStatus,
    AlertStatusUpdate,
    AlertSummary,
)

router = APIRouter(tags=["alerts"])


def _parse_details(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _day_start(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _day_end_exclusive(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _row_to_alert(row: Any) -> Alert:
    data = dict(row)
    data["details"] = _parse_details(data.get("details"))
    return Alert(**data)


@router.get("/alerts", response_model=AlertList)
def list_alerts(
    cik: Optional[int] = Query(None, description="Filter by issuer CIK"),
    anomaly_type: Optional[str] = Query(None, description="Filter by anomaly type"),
    status: Optional[AlertStatus] = Query(None, description="Filter by status"),
    min_severity: Optional[float] = Query(None, ge=0.0, le=1.0),
    max_severity: Optional[float] = Query(None, ge=0.0, le=1.0),
    date_from: Optional[str] = Query(None, description="Inclusive start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Inclusive end date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
) -> AlertList:
    where = []
    if min_severity is not None and max_severity is not None and min_severity > max_severity:
        raise HTTPException(status_code=400, detail="min_severity cannot be greater than max_severity")

    params: list[object] = []

    if cik is not None:
        where.append("f.cik = ?")
        params.append(cik)
    if anomaly_type:
        where.append("a.anomaly_type = ?")
        params.append(anomaly_type)
    if status:
        where.append("a.status = ?")
        params.append(status.value)
    if min_severity is not None:
        where.append("a.severity_score >= ?")
        params.append(min_severity)
    if max_severity is not None:
        where.append("a.severity_score <= ?")
        params.append(max_severity)
    if date_from:
        where.append("COALESCE(a.event_at, a.created_at) >= ?")
        params.append(_day_start(date_from))
    if date_to:
        where.append("COALESCE(a.event_at, a.created_at) < ?")
        params.append(_day_end_exclusive(date_to))

    where_sql = " WHERE " + " AND ".join(where) if where else ""

    total = db.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM alerts a
        JOIN filing_events f ON f.accession_id = a.accession_id
        {where_sql}
        """,
        tuple(params),
    ).fetchone()["count"]

    params_with_page = [*params, limit, offset]
    rows = db.execute(
        f"""
        SELECT
            a.alert_id,
            a.accession_id,
            a.anomaly_type,
            a.severity_score,
            a.description,
            a.details,
            a.status,
            a.dedupe_key,
            a.event_at,
            a.created_at
        FROM alerts a
        JOIN filing_events f ON f.accession_id = a.accession_id
        {where_sql}
        ORDER BY COALESCE(a.event_at, a.created_at) DESC, a.alert_id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params_with_page),
    ).fetchall()

    items = [_row_to_alert(row) for row in rows]
    return AlertList(items=items, total=total, limit=limit, offset=offset)


@router.get("/alerts/summary", response_model=AlertSummary)
def get_alert_summary(
    db=Depends(get_db),
) -> AlertSummary:
    total = db.execute("SELECT COUNT(*) AS count FROM alerts").fetchone()["count"]

    by_type_rows = db.execute(
        "SELECT anomaly_type, COUNT(*) AS count FROM alerts GROUP BY anomaly_type"
    ).fetchall()
    by_type = {row["anomaly_type"]: row["count"] for row in by_type_rows}

    by_status_rows = db.execute(
        "SELECT status, COUNT(*) AS count FROM alerts GROUP BY status"
    ).fetchall()
    by_status = {row["status"]: row["count"] for row in by_status_rows}

    severity_row = db.execute(
        """
        SELECT
            SUM(CASE WHEN severity_score >= 0.8 THEN 1 ELSE 0 END) AS high,
            SUM(CASE WHEN severity_score >= 0.6 AND severity_score < 0.8 THEN 1 ELSE 0 END) AS medium,
            SUM(CASE WHEN severity_score < 0.6 THEN 1 ELSE 0 END) AS low
        FROM alerts
        """
    ).fetchone()
    by_severity = {
        "high": severity_row["high"] or 0,
        "medium": severity_row["medium"] or 0,
        "low": severity_row["low"] or 0,
    }

    recent_days = 7
    recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()
    recent_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM alerts
        WHERE COALESCE(event_at, created_at) >= ?
        """,
        (recent_cutoff,),
    ).fetchone()["count"]

    return AlertSummary(
        total=total,
        by_type=by_type,
        by_status=by_status,
        by_severity=by_severity,
        recent_count=recent_count,
        recent_days=recent_days,
    )


@router.get("/alerts/{alert_id}", response_model=Alert)
def get_alert(
    alert_id: int,
    db=Depends(get_db),
) -> Alert:
    row = db.execute(
        """
        SELECT
            alert_id,
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key,
            event_at,
            created_at
        FROM alerts
        WHERE alert_id = ?
        """,
        (alert_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    return _row_to_alert(row)


@router.patch("/alerts/{alert_id}/status", response_model=Alert)
def update_alert_status(
    alert_id: int,
    payload: AlertStatusUpdate,
    db=Depends(get_db),
) -> Alert:
    cur = db.execute(
        "UPDATE alerts SET status = ? WHERE alert_id = ?",
        (payload.status.value, alert_id),
    )

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alert not found")

    row = db.execute(
        """
        SELECT
            alert_id,
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key,
            event_at,
            created_at
        FROM alerts
        WHERE alert_id = ?
        """,
        (alert_id,),
    ).fetchone()

    return _row_to_alert(row)


@router.patch("/alerts/bulk-status")
def bulk_update_alert_status(
    payload: AlertBulkStatusUpdate,
    db=Depends(get_db),
) -> dict:
    alert_ids = payload.alert_ids
    placeholders = ", ".join(["?"] * len(alert_ids))
    params = [payload.status.value, *alert_ids]

    cur = db.execute(
        f"UPDATE alerts SET status = ? WHERE alert_id IN ({placeholders})",
        params,
    )

    return {"updated": cur.rowcount}
