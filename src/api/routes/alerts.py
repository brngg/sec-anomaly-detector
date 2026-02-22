"""Alert endpoints."""

from __future__ import annotations

import json
import sqlite3
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
    except json.JSONDecodeError:
        return raw


def _row_to_alert(row: sqlite3.Row) -> Alert:
    data = dict(row)
    data["details"] = _parse_details(data.get("details"))
    return Alert(**data)


@router.get("/alerts", response_model=AlertList)
def list_alerts(
    anomaly_type: Optional[str] = Query(None, description="Filter by anomaly type"),
    status: Optional[AlertStatus] = Query(None, description="Filter by status"),
    min_severity: Optional[float] = Query(None, ge=0.0, le=1.0),
    max_severity: Optional[float] = Query(None, ge=0.0, le=1.0),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> AlertList:
    where = []
    if min_severity is not None and max_severity is not None and min_severity > max_severity:
        raise HTTPException(status_code=400, detail="min_severity cannot be greater than max_severity")

    params: list[object] = []

    if anomaly_type:
        where.append("anomaly_type = ?")
        params.append(anomaly_type)
    if status:
        where.append("status = ?")
        params.append(status.value)
    if min_severity is not None:
        where.append("severity_score >= ?")
        params.append(min_severity)
    if max_severity is not None:
        where.append("severity_score <= ?")
        params.append(max_severity)

    where_sql = " WHERE " + " AND ".join(where) if where else ""

    total = db.execute(
        f"SELECT COUNT(*) AS count FROM alerts{where_sql}",
        tuple(params),
    ).fetchone()["count"]

    params_with_page = params + [limit, offset]
    rows = db.execute(
        f"""
        SELECT
            alert_id,
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key,
            created_at
        FROM alerts
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params_with_page),
    ).fetchall()

    items = [_row_to_alert(row) for row in rows]
    return AlertList(items=items, total=total, limit=limit, offset=offset)


@router.get("/alerts/{alert_id}", response_model=Alert)
def get_alert(
    alert_id: int,
    db: sqlite3.Connection = Depends(get_db),
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
    db: sqlite3.Connection = Depends(get_db),
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
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    alert_ids = payload.alert_ids
    placeholders = ", ".join(["?"] * len(alert_ids))
    params = [payload.status.value, *alert_ids]

    cur = db.execute(
        f"UPDATE alerts SET status = ? WHERE alert_id IN ({placeholders})",
        params,
    )

    return {"updated": cur.rowcount}


@router.get("/alerts/summary", response_model=AlertSummary)
def get_alert_summary(
    db: sqlite3.Connection = Depends(get_db),
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
    recent_count = db.execute(
        "SELECT COUNT(*) AS count FROM alerts WHERE created_at >= datetime('now', ?)",
        (f"-{recent_days} days",),
    ).fetchone()["count"]

    return AlertSummary(
        total=total,
        by_type=by_type,
        by_status=by_status,
        by_severity=by_severity,
        recent_count=recent_count,
        recent_days=recent_days,
    )
