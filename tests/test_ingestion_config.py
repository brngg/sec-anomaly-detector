import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.backfill import load_tickers


def test_load_tickers_valid(tmp_path: Path) -> None:
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nAAPL\nmsft\nAAPL\n\n")

    tickers = load_tickers(csv_path)
    assert tickers == ["AAPL", "MSFT"]


def test_load_tickers_missing_header(tmp_path: Path) -> None:
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("symbol\nAAPL\n")

    with pytest.raises(SystemExit):
        load_tickers(csv_path)
