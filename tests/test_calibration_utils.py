import json
from pathlib import Path

from src.analysis import calibration_utils


def _write_artifact(path: Path, entries: list[dict], schema_version: int = 1) -> None:
    payload = {
        "artifact_schema_version": schema_version,
        "model_version": "v1_alert_composite",
        "horizon_days": 90,
        "calibration": entries,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _blocks() -> list[dict[str, float]]:
    return [
        {"min_x": 0.0, "max_x": 0.5, "value": 0.2},
        {"min_x": 0.5, "max_x": 1.0, "value": 0.8},
    ]


def test_calibration_exact_date_apply(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir(parents=True)
    _write_artifact(
        calibration_dir / "isotonic_calibration_20260303_000000.json",
        entries=[
            {
                "as_of_date": "2026-02-23",
                "train_samples": 120,
                "used_isotonic": True,
                "isotonic_blocks": _blocks(),
            }
        ],
    )

    context = calibration_utils.load_calibration_context(calibration_dir)
    decision = calibration_utils.calibrate_raw_score(0.70, "2026-02-23", context)

    assert decision.metadata["status"] == calibration_utils.STATUS_APPLIED
    assert decision.metadata["used_prior_fallback"] is False
    assert decision.calibrated_score == 0.8


def test_calibration_prior_fallback_warning_and_expiry(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir(parents=True)
    _write_artifact(
        calibration_dir / "isotonic_calibration_20260303_000000.json",
        entries=[
            {
                "as_of_date": "2026-02-01",
                "train_samples": 80,
                "used_isotonic": True,
                "isotonic_blocks": _blocks(),
            }
        ],
    )

    context = calibration_utils.load_calibration_context(calibration_dir)

    warning = calibration_utils.calibrate_raw_score(0.2, "2026-02-15", context)
    assert warning.metadata["status"] == calibration_utils.STATUS_STALE_WARNING
    assert warning.metadata["artifact_age_days"] == 14
    assert warning.metadata["used_prior_fallback"] is True
    assert warning.calibrated_score == 0.2

    expired = calibration_utils.calibrate_raw_score(0.2, "2026-03-10", context)
    assert expired.metadata["status"] == calibration_utils.STATUS_STALE_EXPIRED
    assert expired.calibrated_score is None


def test_calibration_malformed_and_insufficient_training(tmp_path: Path) -> None:
    malformed_dir = tmp_path / "bad"
    malformed_dir.mkdir(parents=True)
    (malformed_dir / "isotonic_calibration_20260303_000000.json").write_text("{bad json", encoding="utf-8")
    malformed_context = calibration_utils.load_calibration_context(malformed_dir)
    malformed_decision = calibration_utils.calibrate_raw_score(0.5, "2026-02-23", malformed_context)
    assert malformed_decision.metadata["status"] == calibration_utils.STATUS_MALFORMED_ARTIFACT

    insufficient_dir = tmp_path / "insufficient"
    insufficient_dir.mkdir(parents=True)
    _write_artifact(
        insufficient_dir / "isotonic_calibration_20260303_000000.json",
        entries=[
            {
                "as_of_date": "2026-02-23",
                "train_samples": 5,
                "used_isotonic": False,
                "isotonic_blocks": [],
            }
        ],
    )
    insufficient_context = calibration_utils.load_calibration_context(insufficient_dir)
    insufficient_decision = calibration_utils.calibrate_raw_score(0.5, "2026-02-23", insufficient_context)
    assert insufficient_decision.metadata["status"] == calibration_utils.STATUS_INSUFFICIENT_TRAINING
    assert insufficient_decision.calibrated_score is None
