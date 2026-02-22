"""Pydantic models for API responses and requests."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATED = "INVESTIGATED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class Company(BaseModel):
    cik: int
    name: Optional[str] = None
    ticker: Optional[str] = None
    industry: Optional[str] = None
    updated_at: str


class FilingEvent(BaseModel):
    accession_id: str
    cik: int
    filing_type: str
    filed_at: str
    filed_date: str
    primary_document: Optional[str] = None


class Alert(BaseModel):
    alert_id: int
    accession_id: str
    anomaly_type: str
    severity_score: float
    description: str
    details: Any
    status: AlertStatus
    dedupe_key: str
    created_at: str


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int


class CompanyList(Pagination):
    items: list[Company]


class FilingList(Pagination):
    items: list[FilingEvent]


class AlertList(Pagination):
    items: list[Alert]


class AlertStatusUpdate(BaseModel):
    status: AlertStatus


class AlertBulkStatusUpdate(BaseModel):
    alert_ids: list[int] = Field(min_length=1)
    status: AlertStatus


class AlertSummary(BaseModel):
    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_severity: dict[str, int]
    recent_count: int
    recent_days: int = 7
