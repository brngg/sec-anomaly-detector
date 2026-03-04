"""Pydantic models for API responses and requests."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    event_at: Optional[str] = None
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


class SignalSummary(BaseModel):
    signal: str
    component: float
    count: int


class SignalComponentBreakdown(BaseModel):
    signal: str
    count: int
    weighted_severity: float
    scale: float
    component: float
    anomaly_weight: float
    weight_contribution: float


class WindowComponentBreakdown(BaseModel):
    lookback_days: int
    window_weight: float
    window_score: float
    signal_components: dict[str, SignalComponentBreakdown]


class AlertContribution(BaseModel):
    alert_id: int
    accession_id: str
    anomaly_type: str
    severity_score: float
    recency_weight: float
    weighted_severity: float
    contribution_proxy: float
    event_at: Optional[str] = None
    created_at: str
    filing_type: Optional[str] = None
    filed_at: Optional[str] = None
    description: Optional[str] = None


class RankStabilityThresholds(BaseModel):
    top_quartile_rank_max: int
    spike_min_rank_improvement: int


class RankStabilityEvidence(BaseModel):
    state: str
    universe_size: int
    rank_today: int
    rank_1d_ago: Optional[int] = None
    rank_delta_1d: Optional[int] = None
    top_days_7d: int
    best_rank_7d: int
    worst_rank_7d: int
    thresholds: RankStabilityThresholds


class UncertaintyEvidence(BaseModel):
    alert_count_90d: int
    effective_alert_count_90d: float
    signal_diversity: float
    recent_weight_share_7d: float
    confidence_score: float
    uncertainty_band: str
    formula: str


class CalibrationMetadata(BaseModel):
    status: str
    artifact_path: Optional[str] = None
    artifact_as_of_date: Optional[str] = None
    artifact_age_days: Optional[int] = None
    train_samples: Optional[int] = None
    train_positives: Optional[int] = None
    train_negatives: Optional[int] = None
    min_class_support: Optional[int] = None
    used_prior_fallback: Optional[bool] = None
    artifact_schema_version: Optional[int] = None
    warn_days: int
    expire_days: int
    error_code: Optional[str] = None
    parse_errors_count: Optional[int] = None
    parse_error_example: Optional[str] = None


class ReviewPriorityEvidence(BaseModel):
    model_version: Optional[str] = None
    as_of_date: Optional[str] = None
    window_weights: dict[str, float] = Field(default_factory=dict)
    anomaly_weights: dict[str, float] = Field(default_factory=dict)
    anomaly_component_scales: dict[str, float] = Field(default_factory=dict)
    window_scores: dict[str, float] = Field(default_factory=dict)
    lookback_windows_days: list[int] = Field(default_factory=list)
    top_signals_30d: list[SignalSummary] = Field(default_factory=list)
    source_alerts_90d: int = 0
    component_breakdown: list[WindowComponentBreakdown] = Field(default_factory=list)
    score_math: dict[str, str | float | dict[str, float]] = Field(default_factory=dict)
    top_contributing_alerts_30d: list[AlertContribution] = Field(default_factory=list)
    rank_stability: Optional[RankStabilityEvidence] = None
    uncertainty: Optional[UncertaintyEvidence] = None
    reason_summary: Optional[str] = None
    calibrated_review_priority: Optional[float] = None
    calibration_metadata: Optional[CalibrationMetadata] = None
    model_config = ConfigDict(extra="allow")


class RiskScore(BaseModel):
    score_id: int
    cik: int
    as_of_date: str
    model_version: str
    risk_score: float
    risk_rank: Optional[int] = None
    percentile: Optional[float] = None
    calibrated_review_priority: Optional[float] = None
    evidence: ReviewPriorityEvidence
    created_at: str
    updated_at: str
    company_name: Optional[str] = None
    company_ticker: Optional[str] = None


class RiskScoreList(Pagination):
    items: list[RiskScore]
    as_of_date: Optional[str] = None
    model_version: Optional[str] = None


class RiskScoreHistory(Pagination):
    cik: int
    items: list[RiskScore]
    model_version: Optional[str] = None


class RiskExplanation(BaseModel):
    score: RiskScore
