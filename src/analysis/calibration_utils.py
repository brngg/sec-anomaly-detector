"""Utilities for loading and applying isotonic calibration artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

STATUS_APPLIED = "APPLIED"
STATUS_STALE_WARNING = "STALE_WARNING"
STATUS_STALE_EXPIRED = "STALE_EXPIRED"
STATUS_MISSING_ARTIFACT = "MISSING_ARTIFACT"
STATUS_MALFORMED_ARTIFACT = "MALFORMED_ARTIFACT"
STATUS_INSUFFICIENT_TRAINING = "INSUFFICIENT_TRAINING"
STATUS_INSUFFICIENT_CLASS_SUPPORT = "INSUFFICIENT_CLASS_SUPPORT"

ERROR_NONE = None
ERROR_NO_FILES = "NO_ARTIFACT_FILES"
ERROR_NO_PRIOR_ENTRY = "NO_PRIOR_ENTRY"
ERROR_PARSE = "ARTIFACT_PARSE_ERROR"
ERROR_SCHEMA = "INVALID_SCHEMA"
ERROR_BLOCKS = "INVALID_BLOCKS"
ERROR_STALE = "STALE_EXPIRED"
ERROR_INSUFFICIENT = "INSUFFICIENT_TRAINING"
ERROR_INSUFFICIENT_CLASS_SUPPORT = "INSUFFICIENT_CLASS_SUPPORT"


@dataclass(frozen=True)
class CalibrationEntry:
    as_of_date: str
    as_of_day: date
    train_samples: int
    train_positives: int | None
    train_negatives: int | None
    min_class_support: int | None
    used_isotonic: bool
    isotonic_blocks: list[dict[str, float]]
    artifact_path: str
    artifact_schema_version: int


@dataclass(frozen=True)
class CalibrationContext:
    entries: list[CalibrationEntry]
    parse_errors: list[str]
    has_files: bool
    calibration_dir: str


@dataclass(frozen=True)
class CalibrationDecision:
    calibrated_score: float | None
    metadata: dict[str, Any]


def _parse_day(value: str) -> date:
    return date.fromisoformat(value.strip())


def _validate_blocks(raw_blocks: Any) -> list[dict[str, float]] | None:
    if not isinstance(raw_blocks, list) or not raw_blocks:
        return None

    validated: list[dict[str, float]] = []
    for block in raw_blocks:
        if not isinstance(block, dict):
            return None
        try:
            min_x = float(block["min_x"])
            max_x = float(block["max_x"])
            value = float(block["value"])
        except (KeyError, TypeError, ValueError):
            return None

        if min_x > max_x:
            return None
        if value < 0.0 or value > 1.0:
            return None

        validated.append({"min_x": min_x, "max_x": max_x, "value": value})

    validated.sort(key=lambda item: (item["min_x"], item["max_x"]))
    return validated


def _predict_isotonic(blocks: list[dict[str, float]], score: float) -> float:
    x = float(score)
    if x <= blocks[0]["max_x"]:
        return float(blocks[0]["value"])
    for block in blocks:
        if block["min_x"] <= x <= block["max_x"]:
            return float(block["value"])
    return float(blocks[-1]["value"])


def load_calibration_context(calibration_dir: Path) -> CalibrationContext:
    paths = sorted(calibration_dir.glob("isotonic_calibration_*.json"))
    entries: list[CalibrationEntry] = []
    errors: list[str] = []

    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive parse failure path
            errors.append(f"{path}: {ERROR_PARSE}: {exc}")
            continue

        if not isinstance(payload, dict):
            errors.append(f"{path}: {ERROR_SCHEMA}: payload not object")
            continue

        schema_version = payload.get("artifact_schema_version", 1)
        try:
            schema_version_int = int(schema_version)
        except (TypeError, ValueError):
            schema_version_int = 1

        calibration_rows = payload.get("calibration")
        if not isinstance(calibration_rows, list):
            errors.append(f"{path}: {ERROR_SCHEMA}: missing calibration list")
            continue

        for row in calibration_rows:
            if not isinstance(row, dict):
                errors.append(f"{path}: {ERROR_SCHEMA}: calibration row not object")
                continue

            as_of_date = row.get("as_of_date")
            if not isinstance(as_of_date, str):
                errors.append(f"{path}: {ERROR_SCHEMA}: missing as_of_date")
                continue

            try:
                as_of_day = _parse_day(as_of_date)
            except Exception:
                errors.append(f"{path}: {ERROR_SCHEMA}: invalid as_of_date {as_of_date}")
                continue

            try:
                train_samples = int(row.get("train_samples", 0))
            except (TypeError, ValueError):
                train_samples = 0

            try:
                train_positives_raw = row.get("train_positives")
                train_positives = (
                    int(train_positives_raw)
                    if train_positives_raw not in {None, ""}
                    else None
                )
            except (TypeError, ValueError):
                train_positives = None

            try:
                train_negatives_raw = row.get("train_negatives")
                train_negatives = (
                    int(train_negatives_raw)
                    if train_negatives_raw not in {None, ""}
                    else None
                )
            except (TypeError, ValueError):
                train_negatives = None

            try:
                min_class_support_raw = row.get("min_class_support")
                min_class_support = (
                    int(min_class_support_raw)
                    if min_class_support_raw not in {None, ""}
                    else None
                )
            except (TypeError, ValueError):
                min_class_support = None

            used_isotonic = bool(row.get("used_isotonic", False))
            raw_blocks = row.get("isotonic_blocks", [])
            blocks = _validate_blocks(raw_blocks)
            if used_isotonic and blocks is None:
                errors.append(f"{path}: {ERROR_BLOCKS}: as_of={as_of_date}")
                continue

            entries.append(
                CalibrationEntry(
                    as_of_date=as_of_date,
                    as_of_day=as_of_day,
                    train_samples=train_samples,
                    train_positives=train_positives,
                    train_negatives=train_negatives,
                    min_class_support=min_class_support,
                    used_isotonic=used_isotonic,
                    isotonic_blocks=blocks or [],
                    artifact_path=str(path),
                    artifact_schema_version=schema_version_int,
                )
            )

    entries.sort(key=lambda item: (item.as_of_day, item.artifact_path))
    return CalibrationContext(
        entries=entries,
        parse_errors=errors,
        has_files=bool(paths),
        calibration_dir=str(calibration_dir),
    )


def _base_metadata(
    status: str,
    warn_days: int,
    expire_days: int,
    artifact_path: str | None = None,
    artifact_as_of_date: str | None = None,
    artifact_age_days: int | None = None,
    train_samples: int | None = None,
    train_positives: int | None = None,
    train_negatives: int | None = None,
    min_class_support: int | None = None,
    used_prior_fallback: bool | None = None,
    artifact_schema_version: int | None = None,
    error_code: str | None = ERROR_NONE,
) -> dict[str, Any]:
    return {
        "status": status,
        "artifact_path": artifact_path,
        "artifact_as_of_date": artifact_as_of_date,
        "artifact_age_days": artifact_age_days,
        "train_samples": train_samples,
        "train_positives": train_positives,
        "train_negatives": train_negatives,
        "min_class_support": min_class_support,
        "used_prior_fallback": used_prior_fallback,
        "artifact_schema_version": artifact_schema_version,
        "warn_days": warn_days,
        "expire_days": expire_days,
        "error_code": error_code,
    }


def calibrate_raw_score(
    raw_score: float,
    as_of_date: str,
    context: CalibrationContext,
    warn_days: int = 14,
    expire_days: int = 30,
) -> CalibrationDecision:
    as_of_day = _parse_day(as_of_date)

    if not context.has_files:
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_MISSING_ARTIFACT,
                warn_days=warn_days,
                expire_days=expire_days,
                error_code=ERROR_NO_FILES,
            ),
        )

    if not context.entries:
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_MALFORMED_ARTIFACT,
                warn_days=warn_days,
                expire_days=expire_days,
                error_code=ERROR_PARSE if context.parse_errors else ERROR_SCHEMA,
            ),
        )

    candidates = [entry for entry in context.entries if entry.as_of_day <= as_of_day]
    if not candidates:
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_MISSING_ARTIFACT,
                warn_days=warn_days,
                expire_days=expire_days,
                error_code=ERROR_NO_PRIOR_ENTRY,
            ),
        )

    chosen = max(candidates, key=lambda item: (item.as_of_day, item.artifact_path))
    age_days = (as_of_day - chosen.as_of_day).days
    used_prior_fallback = chosen.as_of_date != as_of_date

    base_kwargs = {
        "warn_days": warn_days,
        "expire_days": expire_days,
        "artifact_path": chosen.artifact_path,
        "artifact_as_of_date": chosen.as_of_date,
        "artifact_age_days": age_days,
        "train_samples": chosen.train_samples,
        "train_positives": chosen.train_positives,
        "train_negatives": chosen.train_negatives,
        "min_class_support": chosen.min_class_support,
        "used_prior_fallback": used_prior_fallback,
        "artifact_schema_version": chosen.artifact_schema_version,
    }

    if not chosen.used_isotonic:
        has_support_threshold = (
            chosen.train_positives is not None
            and chosen.train_negatives is not None
            and chosen.min_class_support is not None
        )
        if has_support_threshold and (
            chosen.train_positives < int(chosen.min_class_support)
            or chosen.train_negatives < int(chosen.min_class_support)
        ):
            return CalibrationDecision(
                calibrated_score=None,
                metadata=_base_metadata(
                    status=STATUS_INSUFFICIENT_CLASS_SUPPORT,
                    error_code=ERROR_INSUFFICIENT_CLASS_SUPPORT,
                    **base_kwargs,
                ),
            )
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_INSUFFICIENT_TRAINING,
                error_code=ERROR_INSUFFICIENT,
                **base_kwargs,
            ),
        )

    blocks = _validate_blocks(chosen.isotonic_blocks)
    if blocks is None:
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_MALFORMED_ARTIFACT,
                error_code=ERROR_BLOCKS,
                **base_kwargs,
            ),
        )

    if age_days > expire_days:
        return CalibrationDecision(
            calibrated_score=None,
            metadata=_base_metadata(
                status=STATUS_STALE_EXPIRED,
                error_code=ERROR_STALE,
                **base_kwargs,
            ),
        )

    calibrated = max(0.0, min(1.0, _predict_isotonic(blocks, raw_score)))
    if age_days >= warn_days:
        return CalibrationDecision(
            calibrated_score=calibrated,
            metadata=_base_metadata(
                status=STATUS_STALE_WARNING,
                error_code=ERROR_NONE,
                **base_kwargs,
            ),
        )

    return CalibrationDecision(
        calibrated_score=calibrated,
        metadata=_base_metadata(
            status=STATUS_APPLIED,
            error_code=ERROR_NONE,
            **base_kwargs,
        ),
    )
