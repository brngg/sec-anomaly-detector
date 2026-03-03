"""Walk-forward validation for issuer review-priority scoring."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.db import db_utils

DEFAULT_MODEL_VERSION = "v1_alert_composite"
DEFAULT_OUTCOME_TYPES = ("RESTATEMENT_DISCLOSURE",)
DEFAULT_HORIZON_DAYS = 90
DEFAULT_K_VALUES = (10, 20, 50)
DEFAULT_BOOTSTRAP_SAMPLES = 300
DEFAULT_RANDOM_SEED = 7
DEFAULT_MIN_CALIBRATION_SAMPLES = 30


@dataclass(frozen=True)
class ScoreRow:
    cik: int
    risk_score: float
    nt_component: float
    equal_weight_component: float


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_components(evidence: Mapping[str, Any]) -> tuple[float, float, float]:
    breakdown = evidence.get("component_breakdown")
    if not isinstance(breakdown, list):
        return (0.0, 0.0, 0.0)

    short_window = None
    for window in breakdown:
        if not isinstance(window, dict):
            continue
        lookback = window.get("lookback_days")
        if isinstance(lookback, int):
            if short_window is None or lookback < short_window.get("lookback_days", 10**9):
                short_window = window

    if not short_window:
        return (0.0, 0.0, 0.0)

    signal_components = short_window.get("signal_components")
    if not isinstance(signal_components, dict):
        return (0.0, 0.0, 0.0)

    nt = float(signal_components.get("NT_FILING", {}).get("component", 0.0))
    friday = float(signal_components.get("FRIDAY_BURYING", {}).get("component", 0.0))
    spike = float(signal_components.get("8K_SPIKE", {}).get("component", 0.0))
    return (nt, friday, spike)


def _fetch_as_of_dates(
    conn,
    model_version: str,
    date_from: str | None,
    date_to: str | None,
) -> list[str]:
    where = ["model_version = ?"]
    params: list[object] = [model_version]
    if date_from:
        where.append("as_of_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("as_of_date <= ?")
        params.append(date_to)

    where_sql = " WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"""
        SELECT DISTINCT as_of_date
        FROM issuer_risk_scores
        {where_sql}
        ORDER BY as_of_date ASC
        """,
        tuple(params),
    ).fetchall()
    return [str(row["as_of_date"]) for row in rows]


def _fetch_scores_for_date(conn, as_of_date: str, model_version: str) -> list[ScoreRow]:
    rows = conn.execute(
        """
        SELECT cik, risk_score, evidence
        FROM issuer_risk_scores
        WHERE as_of_date = ? AND model_version = ?
        """,
        (as_of_date, model_version),
    ).fetchall()

    results: list[ScoreRow] = []
    for row in rows:
        evidence = _parse_json(row["evidence"])
        nt, friday, spike = _extract_components(evidence)
        equal_weight_component = (nt + friday + spike) / 3.0
        results.append(
            ScoreRow(
                cik=int(row["cik"]),
                risk_score=float(row["risk_score"]),
                nt_component=nt,
                equal_weight_component=equal_weight_component,
            )
        )
    return results


def _fetch_positive_ciks(
    conn,
    as_of_date: str,
    horizon_days: int,
    outcome_types: Iterable[str],
) -> set[int]:
    outcome_types = tuple(outcome_types)
    if not outcome_types:
        return set()

    placeholders = ",".join("?" * len(outcome_types))
    rows = conn.execute(
        f"""
        SELECT DISTINCT cik
        FROM outcome_events
        WHERE event_date > ?
          AND event_date <= date(?, ?)
          AND outcome_type IN ({placeholders})
        """,
        (as_of_date, as_of_date, f"+{horizon_days} days", *outcome_types),
    ).fetchall()
    return {int(row["cik"]) for row in rows}


def _rank_model(scores: list[ScoreRow]) -> list[int]:
    return [row.cik for row in sorted(scores, key=lambda row: (-row.risk_score, row.cik))]


def _rank_nt_only(scores: list[ScoreRow]) -> list[int]:
    return [row.cik for row in sorted(scores, key=lambda row: (-row.nt_component, row.cik))]


def _rank_equal_weight(scores: list[ScoreRow]) -> list[int]:
    return [row.cik for row in sorted(scores, key=lambda row: (-row.equal_weight_component, row.cik))]


def _rank_random(scores: list[ScoreRow], seed: int) -> list[int]:
    ciks = [row.cik for row in scores]
    rng = random.Random(seed)
    rng.shuffle(ciks)
    return ciks


def _metric_row(
    ranked_ciks: list[int],
    positives: set[int],
    k: int,
    universe_size: int,
    total_positives: int,
) -> dict[str, float | int]:
    top = ranked_ciks[:k]
    hits = sum(1 for cik in top if cik in positives)
    precision = (hits / k) if k > 0 else 0.0
    base_rate = (total_positives / universe_size) if universe_size > 0 else 0.0
    lift = (precision / base_rate) if base_rate > 0 else 0.0
    recall = (hits / total_positives) if total_positives > 0 else 0.0
    return {
        "k": k,
        "hits": hits,
        "precision": precision,
        "lift": lift,
        "recall": recall,
        "base_rate": base_rate,
    }


def _bootstrap_ci(values: list[float], samples: int, seed: int) -> tuple[float, float] | None:
    if not values:
        return None
    if len(values) == 1:
        return (values[0], values[0])

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(samples):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        draws.append(mean(sample))

    draws.sort()
    low = draws[int(0.025 * (len(draws) - 1))]
    high = draws[int(0.975 * (len(draws) - 1))]
    return (low, high)


def _fit_isotonic(scores: list[float], labels: list[int]) -> list[dict[str, float]]:
    points = sorted(zip(scores, labels), key=lambda item: item[0])
    blocks: list[dict[str, float]] = []

    for score, label in points:
        blocks.append(
            {
                "min_x": float(score),
                "max_x": float(score),
                "sum_w": 1.0,
                "sum_y": float(label),
            }
        )
        while len(blocks) >= 2:
            prev = blocks[-2]
            curr = blocks[-1]
            prev_mean = prev["sum_y"] / prev["sum_w"]
            curr_mean = curr["sum_y"] / curr["sum_w"]
            if prev_mean <= curr_mean:
                break
            merged = {
                "min_x": prev["min_x"],
                "max_x": curr["max_x"],
                "sum_w": prev["sum_w"] + curr["sum_w"],
                "sum_y": prev["sum_y"] + curr["sum_y"],
            }
            blocks[-2:] = [merged]

    model: list[dict[str, float]] = []
    for block in blocks:
        model.append(
            {
                "min_x": block["min_x"],
                "max_x": block["max_x"],
                "value": block["sum_y"] / block["sum_w"],
            }
        )
    return model


def _predict_isotonic(model: list[dict[str, float]], score: float) -> float:
    if not model:
        return max(0.0, min(1.0, score))

    x = float(score)
    if x <= model[0]["max_x"]:
        return model[0]["value"]
    for block in model:
        if block["min_x"] <= x <= block["max_x"]:
            return block["value"]
    return model[-1]["value"]


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "unknown"


def _ensure_output_paths(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    calibration_dir = output_dir / "calibration"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, calibration_dir


def _build_markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Review Priority Validation Report",
        "",
        f"- Generated at: {summary['generated_at_utc']}",
        f"- Model version: {summary['model_version']}",
        f"- As-of dates evaluated: {summary['as_of_dates_evaluated']}",
        f"- Outcome window (days): {summary['horizon_days']}",
        f"- Outcome types: {', '.join(summary['outcome_types'])}",
        f"- Commit SHA: {summary['commit_sha']}",
        "",
        "## Aggregate Metrics (mean across as-of dates)",
        "",
        "| Method | K | Precision | Lift | Recall | Precision CI | Lift CI | Recall CI |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]

    for row in summary["aggregate_metrics"]:
        precision_ci = row.get("precision_ci")
        lift_ci = row.get("lift_ci")
        recall_ci = row.get("recall_ci")
        lines.append(
            "| {method} | {k} | {precision:.4f} | {lift:.4f} | {recall:.4f} | {p_ci} | {l_ci} | {r_ci} |".format(
                method=row["method"],
                k=row["k"],
                precision=row["precision_mean"],
                lift=row["lift_mean"],
                recall=row["recall_mean"],
                p_ci="-" if precision_ci is None else f"[{precision_ci[0]:.4f}, {precision_ci[1]:.4f}]",
                l_ci="-" if lift_ci is None else f"[{lift_ci[0]:.4f}, {lift_ci[1]:.4f}]",
                r_ci="-" if recall_ci is None else f"[{recall_ci[0]:.4f}, {recall_ci[1]:.4f}]",
            )
        )

    return "\n".join(lines) + "\n"


def evaluate_review_priority(
    path: Path = db_utils.DB_PATH,
    model_version: str = DEFAULT_MODEL_VERSION,
    outcome_types: Iterable[str] = DEFAULT_OUTCOME_TYPES,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    date_from: str | None = None,
    date_to: str | None = None,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    random_seed: int = DEFAULT_RANDOM_SEED,
    min_calibration_samples: int = DEFAULT_MIN_CALIBRATION_SAMPLES,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    outcome_types = tuple(outcome_types)
    k_values = tuple(sorted(set(int(k) for k in k_values if int(k) > 0)))

    with db_utils.get_conn(path=path) as conn:
        as_of_dates = _fetch_as_of_dates(
            conn,
            model_version=model_version,
            date_from=date_from,
            date_to=date_to,
        )

        metric_rows: list[dict[str, Any]] = []
        metric_values: dict[tuple[str, int, str], list[float]] = defaultdict(list)
        calibration_artifacts: list[dict[str, Any]] = []

        calibration_train_scores: list[float] = []
        calibration_train_labels: list[int] = []

        for as_of in as_of_dates:
            rows = _fetch_scores_for_date(conn, as_of, model_version=model_version)
            if not rows:
                continue

            positives = _fetch_positive_ciks(
                conn,
                as_of_date=as_of,
                horizon_days=horizon_days,
                outcome_types=outcome_types,
            )
            scored_ciks = {row.cik for row in rows}
            positives = {cik for cik in positives if cik in scored_ciks}

            universe_size = len(rows)
            total_positives = len(positives)

            as_of_seed = int(as_of.replace("-", ""))
            random_rank = _rank_random(rows, seed=random_seed + as_of_seed)
            rankings = {
                "model": _rank_model(rows),
                "nt_only": _rank_nt_only(rows),
                "equal_weight": _rank_equal_weight(rows),
                "random": random_rank,
            }

            calibration_model: list[dict[str, float]] = []
            if len(calibration_train_scores) >= min_calibration_samples:
                calibration_model = _fit_isotonic(calibration_train_scores, calibration_train_labels)

            calibrated_scores = {
                row.cik: _predict_isotonic(calibration_model, row.risk_score)
                for row in rows
            }
            calibration_artifacts.append(
                {
                    "as_of_date": as_of,
                    "train_samples": len(calibration_train_scores),
                    "used_isotonic": bool(calibration_model),
                    "isotonic_blocks": calibration_model,
                    "calibrated_scores": calibrated_scores,
                }
            )

            for k in k_values:
                effective_k = min(k, universe_size)
                if effective_k <= 0:
                    continue
                for method, ranking in rankings.items():
                    metrics = _metric_row(
                        ranked_ciks=ranking,
                        positives=positives,
                        k=effective_k,
                        universe_size=universe_size,
                        total_positives=total_positives,
                    )
                    record = {
                        "as_of_date": as_of,
                        "method": method,
                        "k": effective_k,
                        "universe_size": universe_size,
                        "total_positives": total_positives,
                        **metrics,
                    }
                    metric_rows.append(record)
                    metric_values[(method, effective_k, "precision")].append(float(metrics["precision"]))
                    metric_values[(method, effective_k, "lift")].append(float(metrics["lift"]))
                    metric_values[(method, effective_k, "recall")].append(float(metrics["recall"]))

            for row in rows:
                label = 1 if row.cik in positives else 0
                calibration_train_scores.append(row.risk_score)
                calibration_train_labels.append(label)

    aggregate_metrics: list[dict[str, Any]] = []
    methods = sorted({row["method"] for row in metric_rows})
    all_k = sorted({int(row["k"]) for row in metric_rows})
    for method in methods:
        for k in all_k:
            precision_values = metric_values.get((method, k, "precision"), [])
            lift_values = metric_values.get((method, k, "lift"), [])
            recall_values = metric_values.get((method, k, "recall"), [])
            if not precision_values:
                continue
            aggregate_metrics.append(
                {
                    "method": method,
                    "k": k,
                    "precision_mean": mean(precision_values),
                    "lift_mean": mean(lift_values),
                    "recall_mean": mean(recall_values),
                    "precision_ci": _bootstrap_ci(
                        precision_values,
                        samples=bootstrap_samples,
                        seed=random_seed + 11,
                    ),
                    "lift_ci": _bootstrap_ci(
                        lift_values,
                        samples=bootstrap_samples,
                        seed=random_seed + 29,
                    ),
                    "recall_ci": _bootstrap_ci(
                        recall_values,
                        samples=bootstrap_samples,
                        seed=random_seed + 47,
                    ),
                }
            )

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": model_version,
        "horizon_days": horizon_days,
        "k_values": list(k_values),
        "outcome_types": list(outcome_types),
        "as_of_dates_evaluated": len({row["as_of_date"] for row in metric_rows}),
        "rows_evaluated": len(metric_rows),
        "aggregate_metrics": sorted(aggregate_metrics, key=lambda row: (row["k"], row["method"])),
        "daily_metrics": metric_rows,
        "calibration": calibration_artifacts,
        "commit_sha": _git_sha(),
    }

    if output_dir is not None:
        reports_dir, calibration_dir = _ensure_output_paths(output_dir)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        report_json_path = reports_dir / f"review_priority_validation_{stamp}.json"
        report_md_path = reports_dir / f"review_priority_validation_{stamp}.md"
        calibration_path = calibration_dir / f"isotonic_calibration_{stamp}.json"

        report_json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        report_md_path.write_text(_build_markdown_report(summary), encoding="utf-8")
        calibration_payload = {
            "generated_at_utc": summary["generated_at_utc"],
            "model_version": model_version,
            "horizon_days": horizon_days,
            "calibration": calibration_artifacts,
        }
        calibration_path.write_text(json.dumps(calibration_payload, indent=2, sort_keys=True), encoding="utf-8")

        summary["report_json_path"] = str(report_json_path)
        summary["report_md_path"] = str(report_md_path)
        summary["calibration_path"] = str(calibration_path)

    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate review-priority ranking with walk-forward metrics.")
    parser.add_argument("--db-path", default=str(db_utils.DB_PATH), help="SQLite DB path")
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION, help="Scoring model version")
    parser.add_argument(
        "--outcome-types",
        default=",".join(DEFAULT_OUTCOME_TYPES),
        help="Comma-separated outcome types",
    )
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--k-values", default=",".join(str(k) for k in DEFAULT_K_VALUES))
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--random-seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--min-calibration-samples", type=int, default=DEFAULT_MIN_CALIBRATION_SAMPLES)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "reports"),
        help="Directory for validation reports/artifacts",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    summary = evaluate_review_priority(
        path=Path(args.db_path),
        model_version=args.model_version,
        outcome_types=tuple(x.strip() for x in args.outcome_types.split(",") if x.strip()),
        horizon_days=args.horizon_days,
        k_values=tuple(int(x) for x in args.k_values.split(",") if x.strip()),
        date_from=args.date_from,
        date_to=args.date_to,
        bootstrap_samples=args.bootstrap_samples,
        random_seed=args.random_seed,
        min_calibration_samples=args.min_calibration_samples,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
