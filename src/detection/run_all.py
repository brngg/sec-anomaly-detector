"""Run all anomaly detectors in one shot."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.detection.friday_detection import run_friday_detection
from src.detection.nt_detection import run_nt_detection
from src.detection.spike_8k_detection import run_8k_spike_detection


def run_all_detections() -> Dict[str, Tuple[int, int]]:
    results: Dict[str, Tuple[int, int]] = {}

    results["NT_FILING"] = run_nt_detection()
    results["FRIDAY_BURYING"] = run_friday_detection()
    results["8K_SPIKE"] = run_8k_spike_detection()

    print("\nDetection summary:")
    for key, (total, inserted) in results.items():
        print(f"  {key}: total={total}, inserted={inserted}")

    return results


if __name__ == "__main__":
    run_all_detections()
