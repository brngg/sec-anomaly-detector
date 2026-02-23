"""Build issuer-level disclosure-risk scores from existing alert signals."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.db import db_utils

LOOKBACK_WINDOWS = (30, 90)
WINDOW_WEIGHTS = {30: 0.65, 90: 0.35}
MODEL_VERSION = "v1_alert_composite"
RECENCY_HALFLIFE_DAYS = 30.0

ANOMALY_TYPE_WEIGHTS = {
    "NT_FILING": 0.45,
    "FRIDAY_BURYING": 0.20,
    "8K_SPIKE": 0.35,
}

# Higher scale means a signal needs more weighted severity before saturating at 1.0.
ANOMALY_COMPONENT_SCALES = {
    "NT_FILING": 1.5,
    "FRIDAY_BURYING": 2.5,
    "8K_SPIKE": 1.2,
}

ANOMALY_PREFIX = {
    "NT_FILING": "nt",
    "FRIDAY_BURYING": "friday",
    "8K_SPIKE": "spike",
}


def _normalize_as_of_date(as_of_date: str | None) -> str:
    if as_of_date is None:
        return datetime.now(timezone.utc).date().isoformat()
    return date.fromisoformat(as_of_date).isoformat()


def _parse_created_at(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recency_weight(age_days: float) -> float:
    if age_days <= 0:
        return 1.0
    decay = math.log(2) * (age_days / RECENCY_HALFLIFE_DAYS)
    return math.exp(-decay)


def _fetch_tracked_ciks(conn) -> list[int]:
    rows = conn.execute("SELECT cik FROM companies ORDER BY cik").fetchall()
    return [int(row["cik"]) for row in rows]


def _fetch_alert_rows(conn, as_of_date: str, max_lookback_days: int) -> list[Mapping[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            f.cik AS cik,
            a.anomaly_type AS anomaly_type,
            a.severity_score AS severity_score,
            a.created_at AS created_at
        FROM alerts a
        JOIN filing_events f ON f.accession_id = a.accession_id
        WHERE a.created_at >= datetime(?, ?)
          AND a.created_at < datetime(?, '+1 day')
        ORDER BY a.created_at DESC
        """,
        (as_of_date, f"-{max_lookback_days} days", as_of_date),
    ).fetchall()
    return [dict(row) for row in rows]


def _empty_feature_row() -> dict[str, float | int]:
    return {
        "total_alerts": 0,
        "nt_count": 0,
        "friday_count": 0,
        "spike_count": 0,
        "nt_weighted_severity": 0.0,
        "friday_weighted_severity": 0.0,
        "spike_weighted_severity": 0.0,
        "nt_component": 0.0,
        "friday_component": 0.0,
        "spike_component": 0.0,
        "window_score": 0.0,
    }


def _score_from_components(components: Mapping[str, float]) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for anomaly_type, weight in ANOMALY_TYPE_WEIGHTS.items():
        component = components.get(anomaly_type, 0.0)
        weighted_sum += component * weight
        weight_total += weight
    if weight_total == 0:
        return 0.0
    return weighted_sum / weight_total


def _build_window_features(
    ciks: Iterable[int],
    alert_rows: Iterable[Mapping[str, Any]],
    as_of_date: str,
    lookback_days: int,
) -> dict[int, dict[str, float | int]]:
    feature_map: dict[int, dict[str, float | int]] = {cik: _empty_feature_row() for cik in ciks}
    as_of_cutoff = datetime.fromisoformat(as_of_date).replace(tzinfo=timezone.utc) + timedelta(days=1)

    for row in alert_rows:
        cik = int(row["cik"])
        if cik not in feature_map:
            continue

        created_at = _parse_created_at(str(row["created_at"]))
        age_days = (as_of_cutoff - created_at).total_seconds() / 86400.0
        if age_days < 0 or age_days > lookback_days:
            continue

        features = feature_map[cik]
        features["total_alerts"] = int(features["total_alerts"]) + 1

        anomaly_type = str(row["anomaly_type"])
        prefix = ANOMALY_PREFIX.get(anomaly_type)
        if prefix is None:
            continue

        severity = float(row["severity_score"])
        recency = _recency_weight(age_days)
        features[f"{prefix}_count"] = int(features[f"{prefix}_count"]) + 1
        features[f"{prefix}_weighted_severity"] = float(features[f"{prefix}_weighted_severity"]) + (
            severity * recency
        )

    for cik, features in feature_map.items():
        components: dict[str, float] = {}
        for anomaly_type, prefix in ANOMALY_PREFIX.items():
            weighted = float(features[f"{prefix}_weighted_severity"])
            scale = ANOMALY_COMPONENT_SCALES[anomaly_type]
            component = min(weighted / scale, 1.0)
            features[f"{prefix}_component"] = component
            components[anomaly_type] = component
        features["window_score"] = _score_from_components(components)
        feature_map[cik] = features

    return feature_map


def _combine_window_scores(window_scores: Mapping[int, float]) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for lookback_days, weight in WINDOW_WEIGHTS.items():
        score = float(window_scores.get(lookback_days, 0.0))
        weighted_sum += score * weight
        weight_total += weight
    if weight_total == 0:
        return 0.0
    return weighted_sum / weight_total


def _build_top_signals(features: Mapping[str, float | int]) -> list[dict[str, float | int | str]]:
    signals = [
        {
            "signal": "NT_FILING",
            "component": float(features["nt_component"]),
            "count": int(features["nt_count"]),
        },
        {
            "signal": "FRIDAY_BURYING",
            "component": float(features["friday_component"]),
            "count": int(features["friday_count"]),
        },
        {
            "signal": "8K_SPIKE",
            "component": float(features["spike_component"]),
            "count": int(features["spike_count"]),
        },
    ]
    signals.sort(key=lambda item: (float(item["component"]), int(item["count"])), reverse=True)
    return signals


def run_risk_scoring(
    path: Path = db_utils.DB_PATH,
    as_of_date: str | None = None,
) -> dict[str, int | str]:
    """Compute and persist issuer-level risk scores from alert history."""
    normalized_date = _normalize_as_of_date(as_of_date)
    max_lookback_days = max(LOOKBACK_WINDOWS)

    with db_utils.get_conn(path=path) as conn:
        ciks = _fetch_tracked_ciks(conn)
        if not ciks:
            return {
                "as_of_date": normalized_date,
                "issuers_scored": 0,
                "snapshots_upserted": 0,
                "scores_upserted": 0,
                "source_alerts": 0,
            }

        alert_rows = _fetch_alert_rows(conn, normalized_date, max_lookback_days)
        window_features: dict[int, dict[int, dict[str, float | int]]] = {}
        snapshots_upserted = 0

        for lookback_days in LOOKBACK_WINDOWS:
            features_by_cik = _build_window_features(
                ciks=ciks,
                alert_rows=alert_rows,
                as_of_date=normalized_date,
                lookback_days=lookback_days,
            )
            window_features[lookback_days] = features_by_cik

            for cik in ciks:
                features = features_by_cik[cik]
                db_utils.upsert_feature_snapshot(
                    conn=conn,
                    cik=cik,
                    as_of_date=normalized_date,
                    lookback_days=lookback_days,
                    features=features,
                    source_alert_count=int(features["total_alerts"]),
                )
                snapshots_upserted += 1

        score_by_cik: dict[int, float] = {}
        for cik in ciks:
            per_window = {
                lookback_days: float(window_features[lookback_days][cik]["window_score"])
                for lookback_days in LOOKBACK_WINDOWS
            }
            score_by_cik[cik] = _combine_window_scores(per_window)

        sorted_scores = sorted(score_by_cik.items(), key=lambda item: (item[1], -item[0]), reverse=True)
        issuers_count = len(sorted_scores)
        scores_upserted = 0

        for rank, (cik, final_score) in enumerate(sorted_scores, start=1):
            if issuers_count == 1:
                percentile = 1.0
            else:
                percentile = 1.0 - ((rank - 1) / (issuers_count - 1))

            window_score_map = {
                str(lookback_days): float(window_features[lookback_days][cik]["window_score"])
                for lookback_days in LOOKBACK_WINDOWS
            }
            evidence = {
                "model_version": MODEL_VERSION,
                "as_of_date": normalized_date,
                "window_weights": WINDOW_WEIGHTS,
                "anomaly_weights": ANOMALY_TYPE_WEIGHTS,
                "window_scores": window_score_map,
                "top_signals_30d": _build_top_signals(window_features[30][cik]),
                "lookback_windows_days": list(LOOKBACK_WINDOWS),
                "source_alerts_90d": int(window_features[90][cik]["total_alerts"]),
            }

            db_utils.upsert_issuer_risk_score(
                conn=conn,
                cik=cik,
                as_of_date=normalized_date,
                model_version=MODEL_VERSION,
                risk_score=max(0.0, min(1.0, final_score)),
                risk_rank=rank,
                percentile=percentile,
                evidence=evidence,
            )
            scores_upserted += 1

    return {
        "as_of_date": normalized_date,
        "issuers_scored": issuers_count,
        "snapshots_upserted": snapshots_upserted,
        "scores_upserted": scores_upserted,
        "source_alerts": len(alert_rows),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build issuer risk scores from alert history.")
    parser.add_argument(
        "--as-of-date",
        dest="as_of_date",
        default=None,
        help="As-of date in YYYY-MM-DD. Defaults to current UTC date.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = run_risk_scoring(as_of_date=args.as_of_date)
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
