#!/usr/bin/env python3
"""Prune legacy Postgres data to stay within hosted DB storage limits."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from dotenv import dotenv_values

from src.db import db_utils

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEEP_MODEL = "v2_monthly_abnormal"
DEFAULT_DROP_MODEL = "v1_alert_composite"
TARGET_TABLES = (
    "issuer_risk_scores",
    "feature_snapshots",
    "alerts",
    "filing_events",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prune legacy data in Postgres. Defaults to a dry run that "
            "targets v1_alert_composite score rows."
        )
    )
    parser.add_argument(
        "--keep-model-version",
        default=DEFAULT_KEEP_MODEL,
        help="Model version to preserve when using --drop-non-keep.",
    )
    parser.add_argument(
        "--drop-model-version",
        action="append",
        default=[],
        help=(
            "Model version to delete from issuer_risk_scores. "
            "Can be repeated. Default target is v1_alert_composite."
        ),
    )
    parser.add_argument(
        "--drop-non-keep",
        action="store_true",
        help="Delete all score rows where model_version != --keep-model-version.",
    )
    parser.add_argument(
        "--feature-retention-days",
        type=int,
        default=None,
        help=(
            "Optional: delete feature_snapshots rows older than this many days. "
            "Example: 90 keeps only last 90 days."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute deletions. Without this flag, script is dry-run only.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path for the prune report.",
    )
    return parser.parse_args()


def _load_env() -> None:
    env = dotenv_values(REPO_ROOT / ".env")
    for key, value in env.items():
        if value is not None and key not in os.environ:
            os.environ[key] = value


def _table_sizes(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            c.relname AS table_name,
            pg_total_relation_size(c.oid) AS total_bytes,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS total_pretty
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relname IN ('issuer_risk_scores', 'feature_snapshots', 'alerts', 'filing_events')
        ORDER BY total_bytes DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _score_counts_by_model(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT model_version, COUNT(*) AS row_count
        FROM issuer_risk_scores
        GROUP BY model_version
        ORDER BY row_count DESC, model_version ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _feature_snapshot_stats(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS row_count,
            MIN(as_of_date) AS min_as_of,
            MAX(as_of_date) AS max_as_of
        FROM feature_snapshots
        """
    ).fetchone()
    return dict(row) if row else {"row_count": 0, "min_as_of": None, "max_as_of": None}


def _build_score_delete_target(args: argparse.Namespace) -> tuple[str, tuple[Any, ...], list[str]]:
    if args.drop_non_keep:
        where_sql = "model_version <> ?"
        params: tuple[Any, ...] = (args.keep_model_version,)
        targets = [f"all model versions except {args.keep_model_version}"]
        return where_sql, params, targets

    model_versions = [value.strip() for value in args.drop_model_version if value and value.strip()]
    if not model_versions:
        model_versions = [DEFAULT_DROP_MODEL]
    placeholders = ",".join("?" for _ in model_versions)
    where_sql = f"model_version IN ({placeholders})"
    params = tuple(model_versions)
    return where_sql, params, model_versions


def _estimate_deletions(conn, args: argparse.Namespace) -> dict[str, Any]:
    score_where, score_params, score_targets = _build_score_delete_target(args)
    score_row = conn.execute(
        f"SELECT COUNT(*) AS rows_to_delete FROM issuer_risk_scores WHERE {score_where}",
        score_params,
    ).fetchone()
    score_rows_to_delete = int(score_row["rows_to_delete"] if score_row else 0)

    estimate: dict[str, Any] = {
        "score_targets": score_targets,
        "score_rows_to_delete": score_rows_to_delete,
        "feature_rows_to_delete": 0,
        "feature_cutoff_date": None,
    }

    if args.feature_retention_days is not None:
        cutoff = date.today() - timedelta(days=args.feature_retention_days)
        feature_row = conn.execute(
            "SELECT COUNT(*) AS rows_to_delete FROM feature_snapshots WHERE as_of_date < ?",
            (cutoff.isoformat(),),
        ).fetchone()
        estimate["feature_rows_to_delete"] = int(feature_row["rows_to_delete"] if feature_row else 0)
        estimate["feature_cutoff_date"] = cutoff.isoformat()

    return estimate


def _apply_prune(conn, args: argparse.Namespace) -> dict[str, Any]:
    score_where, score_params, _ = _build_score_delete_target(args)
    score_cursor = conn.execute(
        f"DELETE FROM issuer_risk_scores WHERE {score_where}",
        score_params,
    )
    deleted_scores = int(score_cursor.rowcount if score_cursor.rowcount is not None else 0)

    deleted_features = 0
    feature_cutoff = None
    if args.feature_retention_days is not None:
        cutoff = date.today() - timedelta(days=args.feature_retention_days)
        feature_cutoff = cutoff.isoformat()
        feature_cursor = conn.execute(
            "DELETE FROM feature_snapshots WHERE as_of_date < ?",
            (feature_cutoff,),
        )
        deleted_features = int(feature_cursor.rowcount if feature_cursor.rowcount is not None else 0)

    return {
        "deleted_score_rows": deleted_scores,
        "deleted_feature_rows": deleted_features,
        "feature_cutoff_date": feature_cutoff,
    }


def main() -> int:
    _load_env()
    args = _parse_args()

    summary: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "db_backend_env": os.getenv("DB_BACKEND"),
        "keep_model_version": args.keep_model_version,
        "drop_non_keep": bool(args.drop_non_keep),
        "feature_retention_days": args.feature_retention_days,
        "targets": TARGET_TABLES,
    }

    with db_utils.get_conn(path=None, backend=db_utils.BACKEND_POSTGRES) as conn:
        if db_utils.get_backend(conn) != db_utils.BACKEND_POSTGRES:
            print(json.dumps({"ok": False, "error": "This script supports Postgres only."}, indent=2))
            return 1

        summary["before"] = {
            "table_sizes": _table_sizes(conn),
            "score_counts_by_model": _score_counts_by_model(conn),
            "feature_snapshot_stats": _feature_snapshot_stats(conn),
        }
        summary["estimated_deletions"] = _estimate_deletions(conn, args)

        if args.apply:
            summary["applied"] = _apply_prune(conn, args)
        else:
            summary["applied"] = None

    with db_utils.get_conn(path=None, backend=db_utils.BACKEND_POSTGRES) as conn:
        summary["after"] = {
            "table_sizes": _table_sizes(conn),
            "score_counts_by_model": _score_counts_by_model(conn),
            "feature_snapshot_stats": _feature_snapshot_stats(conn),
        }

    summary["ok"] = True
    rendered = json.dumps(summary, indent=2, sort_keys=True, default=str)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
