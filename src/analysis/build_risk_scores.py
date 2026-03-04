"""Build issuer-level review-priority scores from alert signals."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    # Fallback for `python src/analysis/build_risk_scores.py`.
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.analysis import calibration_utils  # noqa: E402
from src.db import db_utils  # noqa: E402

LOOKBACK_WINDOWS = (30, 90)
WINDOW_WEIGHTS = {30: 0.65, 90: 0.35}
SCORING_MODE_ALERT_COMPOSITE = "alert_composite"
SCORING_MODE_MONTHLY_ABNORMAL = "monthly_abnormal"
SUPPORTED_SCORING_MODES = {
    SCORING_MODE_ALERT_COMPOSITE,
    SCORING_MODE_MONTHLY_ABNORMAL,
}

MODEL_VERSION_ALERT_COMPOSITE = "v1_alert_composite"
MODEL_VERSION_MONTHLY_ABNORMAL = "v2_monthly_abnormal"
DEFAULT_SCORING_MODE = SCORING_MODE_MONTHLY_ABNORMAL
DEFAULT_MODEL_VERSION = MODEL_VERSION_MONTHLY_ABNORMAL
# Backwards-compatible alias used by tests/importers.
MODEL_VERSION = DEFAULT_MODEL_VERSION
RECENCY_HALFLIFE_DAYS = 30.0
TOP_ALERT_CONTRIBUTORS_LIMIT = 10

RANK_STABILITY_LOOKBACK_DAYS = 7
TOP_QUARTILE_RATIO = 0.25
SPIKE_ABSOLUTE_MIN = 5
SPIKE_RELATIVE_RATIO = 0.15

UNCERTAINTY_RECENT_DAYS = 7
CONFIDENCE_HIGH_THRESHOLD = 0.75
CONFIDENCE_MEDIUM_THRESHOLD = 0.45

CALIBRATION_WARN_DAYS = 14
CALIBRATION_EXPIRE_DAYS = 30
DEFAULT_CALIBRATION_DIR = Path(__file__).resolve().parents[2] / "docs" / "reports" / "calibration"

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

MONTHLY_BASELINE_RELATIVE_DENOM_FLOOR = 0.05
MONTHLY_BASELINE_STD_FLOOR = 0.05
MONTHLY_LIFT_SATURATION = 3.0
MONTHLY_Z_SATURATION = 3.0
MONTHLY_SCORE_BLEND = {
    "current_month": 0.35,
    "relative_lift": 0.35,
    "zscore": 0.30,
}


def _normalize_as_of_date(as_of_date: str | None) -> str:
    if as_of_date is None:
        return datetime.now(timezone.utc).date().isoformat()
    return date.fromisoformat(as_of_date).isoformat()


def _normalize_month_start(value: date | str) -> date:
    if isinstance(value, date):
        return value.replace(day=1)
    return date.fromisoformat(str(value)[:10]).replace(day=1)


def _add_months(month_start: date, months: int) -> date:
    month_index = (month_start.year * 12 + (month_start.month - 1)) + int(months)
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year=year, month=month, day=1)


def _iter_month_starts(start_month: date, end_month: date) -> list[date]:
    months: list[date] = []
    current = _normalize_month_start(start_month)
    normalized_end = _normalize_month_start(end_month)
    while current <= normalized_end:
        months.append(current)
        current = _add_months(current, 1)
    return months


def _parse_int_env(name: str, default: int | None = None) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def _resolve_scoring_mode(scoring_mode: str | None) -> str:
    candidate = (scoring_mode or os.getenv("RISK_SCORING_MODE") or DEFAULT_SCORING_MODE).strip().lower()
    if candidate not in SUPPORTED_SCORING_MODES:
        supported = ", ".join(sorted(SUPPORTED_SCORING_MODES))
        raise ValueError(f"Unsupported scoring mode '{candidate}'. Use one of: {supported}")
    return candidate


def _resolve_model_version(model_version: str | None, scoring_mode: str) -> str:
    explicit = (model_version or os.getenv("RISK_MODEL_VERSION") or "").strip()
    if explicit:
        return explicit
    if scoring_mode == SCORING_MODE_ALERT_COMPOSITE:
        return MODEL_VERSION_ALERT_COMPOSITE
    return MODEL_VERSION_MONTHLY_ABNORMAL


def _resolve_monthly_history_months(monthly_history_months: int | None) -> int | None:
    if monthly_history_months is not None:
        if monthly_history_months <= 0:
            return None
        return int(monthly_history_months)

    from_env = _parse_int_env("RISK_MONTHLY_HISTORY_MONTHS", default=None)
    if from_env is None or from_env <= 0:
        return None
    return int(from_env)


def _parse_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _validate_severity(value: Any) -> float:
    severity = float(value)
    if math.isnan(severity):
        raise ValueError("severity_score cannot be NaN")
    if not 0.0 <= severity <= 1.0:
        raise ValueError(f"severity_score out of expected range [0,1]: {severity}")
    return severity


def _event_time(row: Mapping[str, Any]) -> datetime:
    raw = row.get("event_at") or row.get("created_at")
    return _parse_timestamp(str(raw))


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _recency_weight(age_days: float) -> float:
    if age_days <= 0:
        return 1.0
    decay = math.log(2) * (age_days / RECENCY_HALFLIFE_DAYS)
    return math.exp(-decay)


def _fetch_tracked_ciks(conn) -> list[int]:
    rows = conn.execute("SELECT cik FROM companies ORDER BY cik").fetchall()
    return [int(row["cik"]) for row in rows]


def _fetch_alert_rows(conn, as_of_date: str, max_lookback_days: int) -> list[Mapping[str, Any]]:
    as_of_day = date.fromisoformat(as_of_date)
    start_day = as_of_day - timedelta(days=max_lookback_days)
    start_ts = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    end_ts = datetime.combine(as_of_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).isoformat()

    rows = conn.execute(
        """
        SELECT
            a.alert_id AS alert_id,
            a.accession_id AS accession_id,
            f.cik AS cik,
            f.filing_type AS filing_type,
            f.filed_at AS filed_at,
            a.anomaly_type AS anomaly_type,
            a.severity_score AS severity_score,
            a.description AS description,
            a.event_at AS event_at,
            a.created_at AS created_at
        FROM alerts a
        JOIN filing_events f ON f.accession_id = a.accession_id
        WHERE COALESCE(a.event_at, a.created_at) >= ?
          AND COALESCE(a.event_at, a.created_at) < ?
        ORDER BY COALESCE(a.event_at, a.created_at) DESC
        """,
        (start_ts, end_ts),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_prior_ranks(
    conn,
    as_of_date: str,
    model_version: str,
    lookback_days: int = RANK_STABILITY_LOOKBACK_DAYS,
) -> dict[int, dict[str, int]]:
    start_date = (date.fromisoformat(as_of_date) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT cik, as_of_date, risk_rank
        FROM issuer_risk_scores
        WHERE model_version = ?
          AND as_of_date >= ?
          AND as_of_date < ?
          AND risk_rank IS NOT NULL
        ORDER BY as_of_date DESC
        """,
        (model_version, start_date, as_of_date),
    ).fetchall()

    result: dict[int, dict[str, int]] = {}
    for row in rows:
        cik = int(row["cik"])
        result.setdefault(cik, {})[str(row["as_of_date"])] = int(row["risk_rank"])
    return result


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


def _build_features_for_all_windows(
    ciks: Iterable[int],
    alert_rows: Iterable[Mapping[str, Any]],
    as_of_date: str,
    lookback_windows: Iterable[int],
) -> dict[int, dict[int, dict[str, float | int]]]:
    sorted_windows = sorted(set(int(days) for days in lookback_windows))
    feature_maps: dict[int, dict[int, dict[str, float | int]]] = {
        window: {cik: _empty_feature_row() for cik in ciks}
        for window in sorted_windows
    }
    as_of_day = date.fromisoformat(as_of_date)

    for row in alert_rows:
        cik = int(row["cik"])
        if not sorted_windows or cik not in feature_maps[sorted_windows[0]]:
            continue

        event_at = _event_time(row)
        # Day-level aging avoids intra-day bias for events on the same calendar date.
        age_days = (as_of_day - event_at.date()).days
        if age_days < 0:
            continue

        anomaly_type = str(row["anomaly_type"])
        prefix = ANOMALY_PREFIX.get(anomaly_type)
        if prefix is None:
            continue

        severity = _validate_severity(row["severity_score"])
        recency = _recency_weight(age_days)
        for lookback_days in sorted_windows:
            if age_days > lookback_days:
                continue
            features = feature_maps[lookback_days][cik]
            features["total_alerts"] = int(features["total_alerts"]) + 1
            features[f"{prefix}_count"] = int(features[f"{prefix}_count"]) + 1
            features[f"{prefix}_weighted_severity"] = float(features[f"{prefix}_weighted_severity"]) + (
                severity * recency
            )

    for lookback_days in sorted_windows:
        for cik, features in feature_maps[lookback_days].items():
            components: dict[str, float] = {}
            for anomaly_type, prefix in ANOMALY_PREFIX.items():
                weighted = float(features[f"{prefix}_weighted_severity"])
                scale = ANOMALY_COMPONENT_SCALES[anomaly_type]
                component = min(weighted / scale, 1.0)
                features[f"{prefix}_component"] = component
                components[anomaly_type] = component
            features["window_score"] = _score_from_components(components)
            feature_maps[lookback_days][cik] = features

    return feature_maps


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


def _build_component_breakdown(
    features: Mapping[str, float | int],
    lookback_days: int,
) -> dict[str, Any]:
    signal_components: dict[str, dict[str, float | int | str]] = {}
    for anomaly_type, prefix in ANOMALY_PREFIX.items():
        count = int(features[f"{prefix}_count"])
        weighted = float(features[f"{prefix}_weighted_severity"])
        scale = float(ANOMALY_COMPONENT_SCALES[anomaly_type])
        component = float(features[f"{prefix}_component"])
        anomaly_weight = float(ANOMALY_TYPE_WEIGHTS[anomaly_type])
        signal_components[anomaly_type] = {
            "signal": anomaly_type,
            "count": count,
            "weighted_severity": weighted,
            "scale": scale,
            "component": component,
            "anomaly_weight": anomaly_weight,
            "weight_contribution": component * anomaly_weight,
        }

    return {
        "lookback_days": lookback_days,
        "window_weight": float(WINDOW_WEIGHTS.get(lookback_days, 0.0)),
        "window_score": float(features["window_score"]),
        "signal_components": signal_components,
    }


def _calculate_confidence_score(
    effective_alert_count: float,
    signal_diversity: float,
    recent_weight_share_7d: float,
) -> tuple[float, str]:
    epsilon = 1e-12
    score = _clamp(
        0.55 * min(effective_alert_count / 5.0, 1.0)
        + 0.30 * _clamp(signal_diversity)
        + 0.15 * _clamp(recent_weight_share_7d)
    )

    if score + epsilon >= CONFIDENCE_HIGH_THRESHOLD:
        return score, "HIGH"
    if score + epsilon >= CONFIDENCE_MEDIUM_THRESHOLD:
        return score, "MEDIUM"
    return score, "LOW"


def _build_uncertainty_by_cik(
    ciks: Iterable[int],
    alert_rows: Iterable[Mapping[str, Any]],
    as_of_date: str,
    lookback_days: int,
) -> dict[int, dict[str, float | int | str]]:
    as_of_day = date.fromisoformat(as_of_date)
    accum: dict[int, dict[str, Any]] = {
        int(cik): {
            "count": 0,
            "sum_w": 0.0,
            "sum_w2": 0.0,
            "sum_recent_7d": 0.0,
            "signals": set(),
        }
        for cik in ciks
    }

    for row in alert_rows:
        anomaly_type = str(row["anomaly_type"])
        if anomaly_type not in ANOMALY_TYPE_WEIGHTS:
            continue

        cik = int(row["cik"])
        if cik not in accum:
            continue

        event_at = _event_time(row)
        age_days = (as_of_day - event_at.date()).days
        if age_days < 0 or age_days > lookback_days:
            continue

        severity = _validate_severity(row["severity_score"])
        weight = severity * _recency_weight(age_days)

        entry = accum[cik]
        entry["count"] += 1
        entry["sum_w"] += weight
        entry["sum_w2"] += weight * weight
        if age_days <= UNCERTAINTY_RECENT_DAYS:
            entry["sum_recent_7d"] += weight
        entry["signals"].add(anomaly_type)

    result: dict[int, dict[str, float | int | str]] = {}
    total_signal_types = float(len(ANOMALY_TYPE_WEIGHTS))

    for cik in ciks:
        entry = accum[int(cik)]
        sum_w = float(entry["sum_w"])
        sum_w2 = float(entry["sum_w2"])
        if sum_w > 0.0:
            ess = (sum_w * sum_w) / max(sum_w2, 1e-9)
            recent_share = float(entry["sum_recent_7d"]) / sum_w
        else:
            ess = 0.0
            recent_share = 0.0

        signal_diversity = len(entry["signals"]) / total_signal_types
        confidence_score, band = _calculate_confidence_score(
            effective_alert_count=ess,
            signal_diversity=signal_diversity,
            recent_weight_share_7d=recent_share,
        )

        result[int(cik)] = {
            "alert_count_90d": int(entry["count"]),
            "effective_alert_count_90d": ess,
            "signal_diversity": signal_diversity,
            "recent_weight_share_7d": recent_share,
            "confidence_score": confidence_score,
            "uncertainty_band": band,
            "formula": (
                "confidence=clamp(0.55*min(ESS/5,1)+0.30*signal_diversity+"
                "0.15*recent_weight_share_7d,0,1)"
            ),
        }

    return result


def _classify_rank_stability(
    cik: int,
    rank_today: int,
    prior_ranks: Mapping[int, Mapping[str, int]],
    as_of_date: str,
    universe_size: int,
) -> dict[str, int | str | None | dict[str, int]]:
    prior_for_cik = dict(prior_ranks.get(cik, {}))
    as_of_day = date.fromisoformat(as_of_date)
    prior_day_key = (as_of_day - timedelta(days=1)).isoformat()

    rank_1d_ago = prior_for_cik.get(prior_day_key)
    top_quartile_rank_max = max(1, math.ceil(TOP_QUARTILE_RATIO * universe_size))
    spike_min_rank_improvement = max(SPIKE_ABSOLUTE_MIN, math.ceil(SPIKE_RELATIVE_RATIO * universe_size))

    historical_ranks = list(prior_for_cik.values())
    best_rank_7d = min([*historical_ranks, rank_today]) if historical_ranks else rank_today
    worst_rank_7d = max([*historical_ranks, rank_today]) if historical_ranks else rank_today

    top_days_7d = sum(1 for rank in historical_ranks if rank <= top_quartile_rank_max)
    if rank_today <= top_quartile_rank_max:
        top_days_7d += 1

    rank_delta_1d = (rank_today - rank_1d_ago) if rank_1d_ago is not None else None
    moved_up_1d = (rank_1d_ago - rank_today) if rank_1d_ago is not None else None

    if not historical_ranks:
        state = "NEW_PRIORITY"
    else:
        is_spike = (
            moved_up_1d is not None
            and moved_up_1d >= spike_min_rank_improvement
            and rank_today <= top_quartile_rank_max
        )
        if is_spike:
            state = "SPIKING_PRIORITY"
        elif rank_today <= top_quartile_rank_max and top_days_7d >= 3:
            state = "PERSISTENT_PRIORITY"
        else:
            state = "STABLE_PRIORITY"

    return {
        "state": state,
        "universe_size": universe_size,
        "rank_today": rank_today,
        "rank_1d_ago": rank_1d_ago,
        "rank_delta_1d": rank_delta_1d,
        "top_days_7d": top_days_7d,
        "best_rank_7d": best_rank_7d,
        "worst_rank_7d": worst_rank_7d,
        "thresholds": {
            "top_quartile_rank_max": top_quartile_rank_max,
            "spike_min_rank_improvement": spike_min_rank_improvement,
        },
    }


def _build_reason_summary(
    top_signals: list[dict[str, float | int | str]],
    stability_state: str,
    uncertainty_band: str,
) -> str:
    top_non_zero = [signal["signal"] for signal in top_signals if float(signal["component"]) > 0.0][:2]
    if not top_non_zero:
        return (
            "No elevated anomaly signals in the recent review windows. "
            f"Stability={stability_state}, Confidence={uncertainty_band}."
        )
    return (
        "Top drivers: "
        + ", ".join(str(signal) for signal in top_non_zero)
        + f". Stability={stability_state}, Confidence={uncertainty_band}."
    )


def _build_top_contributing_alerts(
    alert_rows: Iterable[Mapping[str, Any]],
    as_of_date: str,
    lookback_days: int,
) -> dict[int, list[dict[str, float | int | str | None]]]:
    as_of_day = date.fromisoformat(as_of_date)
    candidates: dict[int, list[dict[str, float | int | str | None]]] = {}

    for row in alert_rows:
        anomaly_type = str(row["anomaly_type"])
        if anomaly_type not in ANOMALY_TYPE_WEIGHTS:
            continue

        event_at = _event_time(row)
        age_days = (as_of_day - event_at.date()).days
        if age_days < 0 or age_days > lookback_days:
            continue

        severity = _validate_severity(row["severity_score"])
        recency = _recency_weight(age_days)
        weighted_severity = severity * recency
        contribution_proxy = (
            weighted_severity
            * float(ANOMALY_TYPE_WEIGHTS[anomaly_type])
            / float(ANOMALY_COMPONENT_SCALES[anomaly_type])
        )

        cik = int(row["cik"])
        candidates.setdefault(cik, []).append(
            {
                "alert_id": int(row["alert_id"]),
                "accession_id": str(row["accession_id"]),
                "anomaly_type": anomaly_type,
                "severity_score": severity,
                "recency_weight": recency,
                "weighted_severity": weighted_severity,
                "contribution_proxy": contribution_proxy,
                "event_at": str(row.get("event_at") or row.get("created_at")),
                "created_at": str(row["created_at"]),
                "filing_type": row.get("filing_type"),
                "filed_at": str(row["filed_at"]) if row.get("filed_at") is not None else None,
                "description": row.get("description"),
            }
        )

    for cik, rows in candidates.items():
        rows.sort(
            key=lambda item: (
                float(item["contribution_proxy"]),
                float(item["weighted_severity"]),
                str(item["created_at"]),
            ),
            reverse=True,
        )
        candidates[cik] = rows[:TOP_ALERT_CONTRIBUTORS_LIMIT]

    return candidates


def _month_bucket_expression(conn) -> str:
    backend = db_utils.get_backend(conn)
    if backend == db_utils.BACKEND_POSTGRES:
        return "DATE_TRUNC('month', COALESCE(a.event_at, a.created_at))::date"
    return "DATE(COALESCE(a.event_at, a.created_at), 'start of month')"


def _fetch_monthly_alert_aggregates(
    conn,
    as_of_date: str,
    *,
    history_months: int | None = None,
) -> list[dict[str, Any]]:
    as_of_day = date.fromisoformat(as_of_date)
    month_expr = _month_bucket_expression(conn)

    where = ["COALESCE(a.event_at, a.created_at) < ?"]
    end_ts = datetime.combine(
        as_of_day + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    ).isoformat()
    params: list[Any] = [end_ts]

    if history_months is not None:
        current_month = as_of_day.replace(day=1)
        start_month = _add_months(current_month, -(history_months - 1))
        start_ts = datetime.combine(start_month, datetime.min.time(), tzinfo=timezone.utc).isoformat()
        where.insert(0, "COALESCE(a.event_at, a.created_at) >= ?")
        params.insert(0, start_ts)

    rows = conn.execute(
        f"""
        SELECT
            f.cik AS cik,
            {month_expr} AS month_start,
            a.anomaly_type AS anomaly_type,
            COUNT(*) AS alert_count,
            COALESCE(SUM(a.severity_score), 0.0) AS severity_sum
        FROM alerts a
        JOIN filing_events f ON f.accession_id = a.accession_id
        WHERE {" AND ".join(where)}
        GROUP BY f.cik, {month_expr}, a.anomaly_type
        ORDER BY month_start ASC, f.cik ASC, a.anomaly_type ASC
        """,
        tuple(params),
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        raw_month = row["month_start"]
        month_start = _normalize_month_start(raw_month)
        result.append(
            {
                "cik": int(row["cik"]),
                "month_start": month_start,
                "anomaly_type": str(row["anomaly_type"]),
                "alert_count": int(row["alert_count"] or 0),
                "severity_sum": float(row["severity_sum"] or 0.0),
            }
        )
    return result


def _monthly_components_from_sums(
    counts_by_type: Mapping[str, int],
    severity_by_type: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    components: dict[str, float] = {}
    weighted_severity: dict[str, float] = {}
    counts: dict[str, int] = {}

    for anomaly_type, prefix in ANOMALY_PREFIX.items():
        severity = float(severity_by_type.get(anomaly_type, 0.0))
        count = int(counts_by_type.get(anomaly_type, 0))
        scale = float(ANOMALY_COMPONENT_SCALES[anomaly_type])
        components[anomaly_type] = _clamp(min(severity / scale, 1.0))
        weighted_severity[prefix] = severity
        counts[prefix] = count

    return components, weighted_severity, counts


def _build_monthly_abnormal_metrics_by_cik(
    ciks: Iterable[int],
    monthly_rows: Iterable[Mapping[str, Any]],
    as_of_date: str,
    *,
    history_months: int | None = None,
    current_score_by_cik: Mapping[int, float] | None = None,
) -> dict[int, dict[str, Any]]:
    as_of_day = date.fromisoformat(as_of_date)
    current_month = as_of_day.replace(day=1)

    row_list = list(monthly_rows)
    if history_months is not None:
        start_month = _add_months(current_month, -(history_months - 1))
    elif row_list:
        start_month = min(_normalize_month_start(row["month_start"]) for row in row_list)
    else:
        start_month = current_month

    month_axis = _iter_month_starts(start_month, current_month)
    month_set = set(month_axis)

    by_cik_month: dict[int, dict[date, dict[str, dict[str, float | int]]]] = {
        int(cik): {
            month: {
                "count": {anomaly: 0 for anomaly in ANOMALY_TYPE_WEIGHTS},
                "severity": {anomaly: 0.0 for anomaly in ANOMALY_TYPE_WEIGHTS},
            }
            for month in month_axis
        }
        for cik in ciks
    }

    for row in row_list:
        anomaly_type = str(row["anomaly_type"])
        if anomaly_type not in ANOMALY_TYPE_WEIGHTS:
            continue
        cik = int(row["cik"])
        if cik not in by_cik_month:
            continue
        month_start = _normalize_month_start(row["month_start"])
        if month_start not in month_set:
            continue

        bucket = by_cik_month[cik][month_start]
        bucket["count"][anomaly_type] = int(bucket["count"][anomaly_type]) + int(row["alert_count"])
        bucket["severity"][anomaly_type] = float(bucket["severity"][anomaly_type]) + float(
            row["severity_sum"]
        )

    result: dict[int, dict[str, Any]] = {}
    for cik in ciks:
        cik_key = int(cik)
        month_scores: dict[date, float] = {}
        month_feature_rows: dict[date, dict[str, Any]] = {}

        for month_start in month_axis:
            bucket = by_cik_month[cik_key][month_start]
            counts_by_type = {k: int(v) for k, v in dict(bucket["count"]).items()}
            severity_by_type = {k: float(v) for k, v in dict(bucket["severity"]).items()}
            components, weighted_severity, counts = _monthly_components_from_sums(
                counts_by_type=counts_by_type,
                severity_by_type=severity_by_type,
            )
            month_score = _score_from_components(components)
            month_scores[month_start] = month_score
            month_feature_rows[month_start] = {
                "nt_count": counts["nt"],
                "friday_count": counts["friday"],
                "spike_count": counts["spike"],
                "nt_weighted_severity": weighted_severity["nt"],
                "friday_weighted_severity": weighted_severity["friday"],
                "spike_weighted_severity": weighted_severity["spike"],
                "nt_component": components["NT_FILING"],
                "friday_component": components["FRIDAY_BURYING"],
                "spike_component": components["8K_SPIKE"],
            }

        current_month_score = float(month_scores[current_month])
        current_score = float(
            current_score_by_cik.get(cik_key, current_month_score)
            if current_score_by_cik is not None
            else current_month_score
        )
        prior_scores = [float(month_scores[month]) for month in month_axis if month < current_month]
        history_month_count = len(prior_scores)
        if prior_scores:
            baseline_avg = sum(prior_scores) / history_month_count
            variance = sum((score - baseline_avg) ** 2 for score in prior_scores) / history_month_count
            baseline_std = math.sqrt(max(0.0, variance))
            delta = current_score - baseline_avg
            relative_lift = delta / max(MONTHLY_BASELINE_RELATIVE_DENOM_FLOOR, baseline_avg)
            zscore = delta / max(MONTHLY_BASELINE_STD_FLOOR, baseline_std)
            lift_component = _clamp(max(0.0, relative_lift) / MONTHLY_LIFT_SATURATION)
            zscore_component = _clamp(max(0.0, zscore) / MONTHLY_Z_SATURATION)
            final_score = _clamp(
                (MONTHLY_SCORE_BLEND["current_month"] * current_score)
                + (MONTHLY_SCORE_BLEND["relative_lift"] * lift_component)
                + (MONTHLY_SCORE_BLEND["zscore"] * zscore_component)
            )
        else:
            baseline_avg = current_month_score
            baseline_std = 0.0
            delta = 0.0
            relative_lift = 0.0
            zscore = 0.0
            lift_component = 0.0
            zscore_component = 0.0
            final_score = _clamp(current_score)

        current_features = {
            **month_feature_rows[current_month],
            "total_alerts": int(
                month_feature_rows[current_month]["nt_count"]
                + month_feature_rows[current_month]["friday_count"]
                + month_feature_rows[current_month]["spike_count"]
            ),
            "window_score": current_score,
        }

        month_history_recent = []
        for month_start in month_axis[-12:]:
            feature_row = month_feature_rows[month_start]
            month_history_recent.append(
                {
                    "month_start": month_start.isoformat(),
                    "score": float(month_scores[month_start]),
                    "nt_count": int(feature_row["nt_count"]),
                    "friday_count": int(feature_row["friday_count"]),
                    "spike_count": int(feature_row["spike_count"]),
                }
            )

        result[cik_key] = {
            "start_month": start_month.isoformat(),
            "current_month": current_month.isoformat(),
            "current_month_score": current_month_score,
            "current_interval_score": current_score,
            "baseline_avg": baseline_avg,
            "baseline_std": baseline_std,
            "delta_vs_baseline": delta,
            "relative_lift": relative_lift,
            "zscore": zscore,
            "relative_lift_component": lift_component,
            "zscore_component": zscore_component,
            "history_month_count": history_month_count,
            "final_score": final_score,
            "top_signals_monthly": _build_top_signals(current_features),
            "month_history_recent": month_history_recent,
        }

    return result


def _compute_dense_rank_percentile_map(scores: Iterable[float]) -> dict[float, tuple[int, float]]:
    unique_scores = sorted(set(float(score) for score in scores), reverse=True)
    if not unique_scores:
        return {}

    result: dict[float, tuple[int, float]] = {}
    if len(unique_scores) == 1:
        only = unique_scores[0]
        result[only] = (1, 1.0)
        return result

    for index, score in enumerate(unique_scores, start=1):
        percentile = 1.0 - ((index - 1) / (len(unique_scores) - 1))
        result[score] = (index, percentile)
    return result


def run_risk_scoring(
    path: Path | None = None,
    as_of_date: str | None = None,
    calibration_dir: Path = DEFAULT_CALIBRATION_DIR,
    calibration_warn_days: int = CALIBRATION_WARN_DAYS,
    calibration_expire_days: int = CALIBRATION_EXPIRE_DAYS,
    scoring_mode: str | None = None,
    model_version: str | None = None,
    monthly_history_months: int | None = None,
) -> dict[str, Any]:
    """Compute and persist issuer-level review-priority scores from alert history."""
    normalized_date = _normalize_as_of_date(as_of_date)
    resolved_mode = _resolve_scoring_mode(scoring_mode)
    resolved_model_version = _resolve_model_version(model_version, resolved_mode)
    resolved_monthly_history_months = _resolve_monthly_history_months(monthly_history_months)
    lookback_windows = tuple(sorted(set(int(days) for days in LOOKBACK_WINDOWS)))
    max_lookback_days = max(lookback_windows)
    short_window = min(lookback_windows)
    long_window = max(lookback_windows)

    calibration_context = calibration_utils.load_calibration_context(calibration_dir)

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
        prior_ranks = _fetch_prior_ranks(
            conn,
            as_of_date=normalized_date,
            model_version=resolved_model_version,
            lookback_days=RANK_STABILITY_LOOKBACK_DAYS,
        )

        window_features: dict[int, dict[int, dict[str, float | int]]] = _build_features_for_all_windows(
            ciks=ciks,
            alert_rows=alert_rows,
            as_of_date=normalized_date,
            lookback_windows=lookback_windows,
        )
        top_contributing_alerts = _build_top_contributing_alerts(
            alert_rows=alert_rows,
            as_of_date=normalized_date,
            lookback_days=short_window,
        )
        uncertainty_by_cik = _build_uncertainty_by_cik(
            ciks=ciks,
            alert_rows=alert_rows,
            as_of_date=normalized_date,
            lookback_days=long_window,
        )
        monthly_metrics_by_cik: dict[int, dict[str, Any]] = {}
        if resolved_mode == SCORING_MODE_MONTHLY_ABNORMAL:
            monthly_rows = _fetch_monthly_alert_aggregates(
                conn,
                normalized_date,
                history_months=resolved_monthly_history_months,
            )
            current_score_by_cik = {
                int(cik): float(window_features[short_window][cik]["window_score"])
                for cik in ciks
            }
            monthly_metrics_by_cik = _build_monthly_abnormal_metrics_by_cik(
                ciks=ciks,
                monthly_rows=monthly_rows,
                as_of_date=normalized_date,
                history_months=resolved_monthly_history_months,
                current_score_by_cik=current_score_by_cik,
            )

        snapshots_upserted = 0

        for lookback_days in lookback_windows:
            for cik in ciks:
                features = window_features[lookback_days][cik]
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
                for lookback_days in lookback_windows
            }
            raw_alert_composite_score = _combine_window_scores(per_window)
            if resolved_mode == SCORING_MODE_MONTHLY_ABNORMAL:
                monthly_metrics = monthly_metrics_by_cik.get(int(cik))
                if monthly_metrics is None:
                    score_by_cik[cik] = _clamp(raw_alert_composite_score)
                else:
                    score_by_cik[cik] = _clamp(float(monthly_metrics["final_score"]))
            else:
                score_by_cik[cik] = _clamp(raw_alert_composite_score)

        # Deterministic output order while preserving identical ranks for identical scores.
        sorted_scores = sorted(score_by_cik.items(), key=lambda item: (-item[1], item[0]))
        rank_map = _compute_dense_rank_percentile_map(score_by_cik.values())
        issuers_count = len(ciks)
        scores_upserted = 0

        calibration_status_counts: dict[str, int] = {}
        stability_state_counts: dict[str, int] = {}
        uncertainty_band_counts: dict[str, int] = {}

        for cik, raw_final_score in sorted_scores:
            final_score = _clamp(raw_final_score)
            rank, percentile = rank_map[float(raw_final_score)]

            window_score_map = {
                str(lookback_days): float(window_features[lookback_days][cik]["window_score"])
                for lookback_days in lookback_windows
            }
            top_signals_key = f"top_signals_{short_window}d"
            source_alerts_key = f"source_alerts_{long_window}d"
            component_breakdown = [
                _build_component_breakdown(window_features[lookback_days][cik], lookback_days)
                for lookback_days in lookback_windows
            ]
            top_signals = _build_top_signals(window_features[short_window][cik])
            monthly_top_signals: list[dict[str, float | int | str]] = []
            monthly_baseline_payload: dict[str, Any] | None = None
            final_score_formula = "sum(window_score * window_weight) / sum(window_weight)"
            if resolved_mode == SCORING_MODE_MONTHLY_ABNORMAL:
                monthly_metrics = monthly_metrics_by_cik.get(int(cik), {})
                monthly_top_signals = top_signals
                monthly_baseline_payload = {
                    "history_start_month": monthly_metrics.get("start_month"),
                    "current_month": monthly_metrics.get("current_month"),
                    "history_month_count": int(monthly_metrics.get("history_month_count", 0)),
                    "current_month_score": float(monthly_metrics.get("current_month_score", 0.0)),
                    "current_interval_score_30d": float(monthly_metrics.get("current_interval_score", 0.0)),
                    "baseline_avg": float(monthly_metrics.get("baseline_avg", 0.0)),
                    "baseline_std": float(monthly_metrics.get("baseline_std", 0.0)),
                    "delta_vs_baseline": float(monthly_metrics.get("delta_vs_baseline", 0.0)),
                    "relative_lift": float(monthly_metrics.get("relative_lift", 0.0)),
                    "zscore": float(monthly_metrics.get("zscore", 0.0)),
                    "relative_lift_component": float(
                        monthly_metrics.get("relative_lift_component", 0.0)
                    ),
                    "zscore_component": float(monthly_metrics.get("zscore_component", 0.0)),
                    "month_history_recent": list(monthly_metrics.get("month_history_recent", [])),
                }
                final_score_formula = (
                    "0.35*current_interval_score_30d + 0.35*relative_lift_component + 0.30*zscore_component"
                )

            stability = _classify_rank_stability(
                cik=cik,
                rank_today=rank,
                prior_ranks=prior_ranks,
                as_of_date=normalized_date,
                universe_size=issuers_count,
            )
            uncertainty = uncertainty_by_cik[int(cik)]

            calibration_decision = calibration_utils.calibrate_raw_score(
                raw_score=final_score,
                as_of_date=normalized_date,
                context=calibration_context,
                warn_days=calibration_warn_days,
                expire_days=calibration_expire_days,
            )
            calibration_metadata = dict(calibration_decision.metadata)
            calibration_metadata["parse_errors_count"] = len(calibration_context.parse_errors)
            if calibration_context.parse_errors:
                calibration_metadata["parse_error_example"] = calibration_context.parse_errors[0]

            status = str(calibration_metadata["status"])
            calibration_status_counts[status] = calibration_status_counts.get(status, 0) + 1

            stability_state = str(stability["state"])
            stability_state_counts[stability_state] = stability_state_counts.get(stability_state, 0) + 1

            uncertainty_band = str(uncertainty["uncertainty_band"])
            uncertainty_band_counts[uncertainty_band] = uncertainty_band_counts.get(uncertainty_band, 0) + 1

            reason_signals = monthly_top_signals if monthly_top_signals else top_signals
            evidence = {
                "model_version": resolved_model_version,
                "scoring_mode": resolved_mode,
                "as_of_date": normalized_date,
                "window_weights": {str(window): float(weight) for window, weight in WINDOW_WEIGHTS.items()},
                "anomaly_weights": {name: float(weight) for name, weight in ANOMALY_TYPE_WEIGHTS.items()},
                "anomaly_component_scales": {
                    name: float(scale) for name, scale in ANOMALY_COMPONENT_SCALES.items()
                },
                "window_scores": window_score_map,
                top_signals_key: top_signals,
                "lookback_windows_days": list(lookback_windows),
                source_alerts_key: int(window_features[long_window][cik]["total_alerts"]),
                "component_breakdown": component_breakdown,
                "score_math": {
                    "recency_halflife_days": float(RECENCY_HALFLIFE_DAYS),
                    "window_score_formula": "weighted anomaly components normalized by total anomaly weight",
                    "final_score_formula": final_score_formula,
                    "final_score_raw": float(raw_final_score),
                    "final_score_clamped": final_score,
                },
                "top_contributing_alerts_30d": top_contributing_alerts.get(cik, []),
                "rank_stability": stability,
                "uncertainty": uncertainty,
                "calibrated_review_priority": calibration_decision.calibrated_score,
                "calibration_metadata": calibration_metadata,
                "reason_summary": _build_reason_summary(
                    reason_signals,
                    stability_state=stability_state,
                    uncertainty_band=uncertainty_band,
                ),
            }
            if monthly_baseline_payload is not None:
                evidence["top_signals_monthly"] = monthly_top_signals
                evidence["monthly_baseline"] = monthly_baseline_payload

            db_utils.upsert_issuer_risk_score(
                conn=conn,
                cik=cik,
                as_of_date=normalized_date,
                model_version=resolved_model_version,
                risk_score=final_score,
                risk_rank=rank,
                percentile=percentile,
                evidence=evidence,
            )
            scores_upserted += 1

    return {
        "as_of_date": normalized_date,
        "scoring_mode": resolved_mode,
        "model_version": resolved_model_version,
        "issuers_scored": issuers_count,
        "snapshots_upserted": snapshots_upserted,
        "scores_upserted": scores_upserted,
        "source_alerts": len(alert_rows),
        "monthly_history_months": resolved_monthly_history_months,
        "calibration_status_distribution": calibration_status_counts,
        "uncertainty_band_distribution": uncertainty_band_counts,
        "stability_state_distribution": stability_state_counts,
        "calibration_applied_coverage": (
            calibration_status_counts.get(calibration_utils.STATUS_APPLIED, 0)
            + calibration_status_counts.get(calibration_utils.STATUS_STALE_WARNING, 0)
        )
        / max(issuers_count, 1),
        "spiking_priority_count": stability_state_counts.get("SPIKING_PRIORITY", 0),
        "persistent_priority_count": stability_state_counts.get("PERSISTENT_PRIORITY", 0),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build issuer review-priority scores from alert history.")
    parser.add_argument(
        "--as-of-date",
        dest="as_of_date",
        default=None,
        help="As-of date in YYYY-MM-DD. Defaults to current UTC date.",
    )
    parser.add_argument(
        "--calibration-dir",
        dest="calibration_dir",
        default=str(DEFAULT_CALIBRATION_DIR),
        help="Directory containing isotonic calibration artifacts.",
    )
    parser.add_argument(
        "--calibration-warn-days",
        dest="calibration_warn_days",
        type=int,
        default=CALIBRATION_WARN_DAYS,
        help="Artifact staleness threshold for warning status.",
    )
    parser.add_argument(
        "--calibration-expire-days",
        dest="calibration_expire_days",
        type=int,
        default=CALIBRATION_EXPIRE_DAYS,
        help="Artifact staleness threshold for disabling calibration.",
    )
    parser.add_argument(
        "--scoring-mode",
        dest="scoring_mode",
        choices=sorted(SUPPORTED_SCORING_MODES),
        default=None,
        help="Scoring mode: monthly history-relative or legacy alert composite.",
    )
    parser.add_argument(
        "--model-version",
        dest="model_version",
        default=None,
        help="Optional explicit model version label for writes.",
    )
    parser.add_argument(
        "--monthly-history-months",
        dest="monthly_history_months",
        type=int,
        default=None,
        help="Optional monthly history window; unset means all available history.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = run_risk_scoring(
        as_of_date=args.as_of_date,
        calibration_dir=Path(args.calibration_dir),
        calibration_warn_days=args.calibration_warn_days,
        calibration_expire_days=args.calibration_expire_days,
        scoring_mode=args.scoring_mode,
        model_version=args.model_version,
        monthly_history_months=args.monthly_history_months,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
