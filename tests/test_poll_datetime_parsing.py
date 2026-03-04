from datetime import date, datetime, timezone

from src.ingestion import poll


def test_parse_dt_accepts_datetime_instance() -> None:
    value = datetime(2026, 3, 4, 4, 25, 14, tzinfo=timezone.utc)
    parsed = poll._parse_dt(value)
    assert parsed == value


def test_parse_dt_accepts_date_instance() -> None:
    parsed = poll._parse_dt(date(2026, 3, 4))
    assert parsed == datetime(2026, 3, 4, 0, 0, tzinfo=timezone.utc)


def test_parse_dt_accepts_iso_string_and_z_suffix() -> None:
    parsed = poll._parse_dt("2026-03-04T04:25:14Z")
    assert parsed == datetime(2026, 3, 4, 4, 25, 14, tzinfo=timezone.utc)


def test_parse_dt_returns_none_for_invalid_input() -> None:
    assert poll._parse_dt("not-a-datetime") is None
