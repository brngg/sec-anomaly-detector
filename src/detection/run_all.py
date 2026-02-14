"""Run all anomaly detectors in one shot."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.detection.friday_detection import run_friday_detection
from src.detection.nt_detection import run_nt_detection
from src.detection.spike_8k_detection import run_8k_spike_detection


def run_all_detections() -> Dict[str, Tuple[int, int]]:
    """Run all detectors, catching errors so one failure doesn't block others."""
    results: Dict[str, Tuple[int, int]] = {}

    detectors: list[Tuple[str, Callable[[], Tuple[int, int]]]] = [
        ("NT_FILING", run_nt_detection),
        ("FRIDAY_BURYING", run_friday_detection),
        ("8K_SPIKE", run_8k_spike_detection),
    ]

    for name, detector in detectors:
        try:
            results[name] = detector()
        except Exception as e:
            print(f"Error running {name}: {e}")
            results[name] = (0, 0)

    print("\nDetection summary:")
    for key, (total, inserted) in results.items():
        print(f"  {key}: total={total}, inserted={inserted}")

    return results


if __name__ == "__main__":
    run_all_detections()
