"""Alert helpers shared across detection modules."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from src.db import db_utils


def insert_alert(
    conn,
    accession_id: str,
    anomaly_type: str,
    severity_score: float,
    description: str,
    details: Mapping[str, Any] | str,
    status: str = "OPEN",
    dedupe_key: Optional[str] = None,
    event_at: Optional[str] = None,
) -> bool:
    """Insert an alert if it doesn't already exist. Returns True if inserted."""
    if dedupe_key is None:
        dedupe_key = f"{anomaly_type}:{accession_id}"

    if isinstance(details, str):
        details_json = details
    else:
        details_json = json.dumps(details, sort_keys=True, default=str)

    event_at_value = event_at
    cursor = conn.execute(
        """
        INSERT INTO alerts (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key,
            event_at,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            COALESCE(?, (SELECT filed_at FROM filing_events WHERE accession_id = ?), CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT(dedupe_key) DO NOTHING
        """,
        (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details_json,
            status,
            dedupe_key,
            event_at_value,
            accession_id,
        ),
    )
    return db_utils.row_was_affected(cursor)
