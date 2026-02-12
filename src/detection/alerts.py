"""Alert helpers shared across detection modules."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping, Optional


def insert_alert(
    conn: sqlite3.Connection,
    accession_id: str,
    anomaly_type: str,
    severity_score: float,
    description: str,
    details: Mapping[str, Any] | str,
    status: str = "OPEN",
    dedupe_key: Optional[str] = None,
) -> bool:
    """Insert an alert if it doesn't already exist. Returns True if inserted."""
    if dedupe_key is None:
        dedupe_key = f"{anomaly_type}:{accession_id}"

    if isinstance(details, str):
        details_json = details
    else:
        details_json = json.dumps(details, sort_keys=True)

    changes_before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO alerts (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details_json,
            status,
            dedupe_key,
        ),
    )
    return conn.total_changes > changes_before
