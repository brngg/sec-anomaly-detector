"""Run detectors and issuer risk scoring as a standalone analysis step."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.analysis.build_risk_scores import run_risk_scoring
from src.detection.run_all import run_all_detections

REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_disabled(value: str) -> bool:
    return value.strip().lower() in {"0", "false", "no", "n"}


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=True)
    enable_risk_scoring = not _is_disabled(os.getenv("POLL_ENABLE_RISK_SCORING", "1"))

    print("Running anomaly detectors...")
    run_all_detections()

    if not enable_risk_scoring:
        print("Risk scoring disabled by POLL_ENABLE_RISK_SCORING.")
        return 0

    print("Running issuer risk scoring...")
    try:
        score_stats = run_risk_scoring()
    except Exception as e:
        print(f"Risk scoring failed: {e}")
        return 1

    print(
        "Risk scoring summary: "
        f"issuers_scored={score_stats['issuers_scored']} "
        f"snapshots_upserted={score_stats['snapshots_upserted']} "
        f"scores_upserted={score_stats['scores_upserted']} "
        f"source_alerts={score_stats['source_alerts']} "
        f"as_of_date={score_stats['as_of_date']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
