from __future__ import annotations

import html
import json
import os
from typing import Any

import altair as alt
import pandas as pd
import requests
import streamlit as st

DEFAULT_API_BASE_URL = os.getenv("DASHBOARD_API_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_API_KEY = os.getenv("DASHBOARD_API_KEY") or os.getenv("DEMO_API_KEY") or os.getenv("API_KEY") or ""
DEFAULT_LIMIT = int(os.getenv("DASHBOARD_DEFAULT_LIMIT", "25"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("DASHBOARD_REQUEST_TIMEOUT_SECONDS", "20"))
VISIBLE_LEADERBOARD_ROWS = 10
LEADERBOARD_COLUMN_SPECS = [0.32, 0.62, 1.95, 1.25]


st.set_page_config(
    page_title="SEC Review Priority Dashboard",
    page_icon="S",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.html(
    """
    <style>
      :root {
        --bg: #030406;
        --bg-soft: #06080d;
        --panel: #0a0d12;
        --panel-strong: #11161d;
        --panel-soft: #161d27;
        --line: rgba(91, 108, 255, 0.24);
        --line-soft: rgba(110, 124, 142, 0.18);
        --text: #eef2f7;
        --muted: #8f9bab;
        --accent: #5b6cff;
        --accent-soft: rgba(91, 108, 255, 0.14);
        --ice: #7ec8ff;
        --amber: #a8b4ff;
        --warn: #ff889d;
      }

      @keyframes fadeUp {
        from {
          opacity: 0;
          transform: translateY(8px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      .stApp {
        background: var(--bg);
        color: var(--text);
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      }

      [data-testid="stSidebar"] {
        background: var(--bg-soft);
        border-right: 1px solid rgba(91, 108, 255, 0.18);
      }

      [data-testid="stHeader"] {
        background: transparent;
      }

      h1, h2, h3, h4 {
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
        letter-spacing: 0.03em;
      }

      .shell,
      .panel,
      div[data-testid="stVerticalBlockBorderWrapper"],
      div[data-testid="stVerticalBlockBorderWrapper"] > div[data-testid="stVerticalBlock"] {
        animation: fadeUp 180ms ease-out;
      }

      .shell {
        border: 1px solid rgba(91, 108, 255, 0.20);
        border-radius: 8px;
        background: var(--panel);
        padding: 1rem 1rem 0.85rem 1rem;
        box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
      }

      .shell-topline {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 1rem;
        padding-bottom: 1rem;
        border-bottom: 1px solid var(--line-soft);
      }

      .eyebrow {
        color: var(--accent);
        font-size: 0.72rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }

      .title {
        margin-top: 0.3rem;
        font-size: 1.55rem;
        color: var(--text);
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      .subtitle {
        color: var(--muted);
        margin-top: 0.35rem;
        max-width: 42rem;
        font-size: 0.88rem;
        line-height: 1.5;
      }

      .timestamp {
        color: var(--accent);
        font-size: 0.72rem;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        white-space: nowrap;
      }

      .selection-note {
        color: var(--muted);
        font-size: 0.74rem;
        line-height: 1.45;
        margin-top: 0.75rem;
      }

      .panel {
        border: 1px solid rgba(91, 108, 255, 0.18);
        border-radius: 6px;
        background: var(--panel);
        padding: 0.85rem 0.85rem 0.8rem 0.85rem;
        box-shadow: 0 8px 18px rgba(0, 0, 0, 0.14);
        transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
      }

      .panel:hover {
        transform: translateY(-1px);
        border-color: rgba(91, 108, 255, 0.30);
        box-shadow: 0 10px 22px rgba(0, 0, 0, 0.18);
      }

      .panel-title {
        color: var(--accent);
        font-size: 0.74rem;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        margin-bottom: 0.7rem;
      }

      .stat-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.7rem;
      }

      .stat {
        border: 1px solid rgba(110, 124, 142, 0.16);
        border-radius: 6px;
        padding: 0.62rem 0.72rem;
        background: var(--panel-strong);
        min-height: 78px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
      }

      .stat-label {
        color: var(--muted);
        font-size: 0.68rem;
        letter-spacing: 0.14em;
        text-transform: uppercase;
      }

      .stat-value {
        color: var(--text);
        font-size: 0.98rem;
        margin-top: 0.3rem;
        font-weight: 700;
        line-height: 1.18;
        word-break: break-word;
      }

      .panel-copy {
        color: var(--muted);
        font-size: 0.8rem;
        line-height: 1.45;
        margin-top: 0.75rem;
      }

      .signal-list {
        margin: 0;
        padding: 0;
        list-style: none;
      }

      .signal-item {
        display: flex;
        justify-content: space-between;
        gap: 0.8rem;
        padding: 0.48rem 0;
        border-bottom: 1px solid rgba(110, 124, 142, 0.16);
      }

      .signal-item:last-child {
        border-bottom: 0;
        padding-bottom: 0;
      }

      .signal-name {
        color: var(--text);
      }

      .signal-meta {
        color: var(--muted);
        white-space: normal;
        text-align: right;
      }

      .guardrail-list {
        margin: 0;
        padding-left: 1rem;
        color: var(--muted);
      }

      .guardrail-list li {
        margin-bottom: 0.45rem;
      }

      .tier {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 0.2rem 0.52rem;
        font-size: 0.68rem;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        border: 1px solid transparent;
      }

      .tier-high {
        color: var(--warn);
        background: rgba(255, 125, 135, 0.10);
        border-color: rgba(255, 125, 135, 0.22);
      }

      .tier-medium {
        color: var(--amber);
        background: rgba(168, 180, 255, 0.10);
        border-color: rgba(168, 180, 255, 0.22);
      }

      .tier-low {
        color: var(--accent);
        background: rgba(91, 108, 255, 0.10);
        border-color: rgba(91, 108, 255, 0.22);
      }

      div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid rgba(91, 108, 255, 0.16);
        border-radius: 6px;
        background: var(--panel);
        box-shadow: 0 6px 14px rgba(0, 0, 0, 0.12);
      }

      .board-header {
        display: grid;
        grid-template-columns: 0.32fr 0.62fr 1.95fr 1.25fr;
        gap: 0.18rem;
        padding: 0.02rem 0 0.16rem 0;
        border-bottom: 1px solid rgba(91, 108, 255, 0.16);
        color: var(--accent);
        font-size: 0.62rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
      }

      div[data-testid="stButton"] {
        margin-bottom: 0.02rem !important;
      }

      .stButton > button {
        background: var(--panel-strong);
        color: var(--text);
        border: 1px solid rgba(110, 124, 142, 0.14);
        transition: transform 140ms ease, border-color 140ms ease, background-color 140ms ease, box-shadow 140ms ease;
      }

      .stButton > button:hover {
        transform: translateY(-1px);
        border-color: rgba(91, 108, 255, 0.28);
        background: var(--panel-soft);
      }

      .stButton > button[kind="secondary"],
      .stButton > button[kind="primary"] {
        width: 100%;
        max-width: 100%;
        justify-content: flex-start;
        min-height: 22px !important;
        height: 22px !important;
        border-radius: 2px;
        padding: 0.01rem 0.02rem !important;
        margin: 0 !important;
        text-align: left;
        box-sizing: border-box !important;
        min-width: 0 !important;
        overflow: hidden !important;
      }

      .stButton > button > div,
      .stButton > button > div > div,
      .stButton [data-testid="stMarkdownContainer"] {
        width: 100% !important;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        box-sizing: border-box !important;
        min-width: 0 !important;
        overflow: hidden !important;
      }

      .stButton > button > div {
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center;
      }

      .stButton [data-testid="stMarkdownContainer"] {
        flex: 1 1 auto;
      }

      .stButton > button[kind="secondary"] p,
      .stButton > button[kind="primary"] p {
        white-space: pre;
        text-align: left;
        width: 100% !important;
        max-width: 100% !important;
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
        font-size: 0.61rem !important;
        line-height: 0.92 !important;
        letter-spacing: 0.004em;
        font-variant-numeric: tabular-nums;
        margin: 0;
        box-sizing: border-box !important;
        overflow: hidden !important;
        text-overflow: ellipsis;
      }

      .leaderboard-cell-center .stButton > button > div {
        justify-content: center !important;
      }

      .leaderboard-cell-right .stButton > button > div {
        justify-content: flex-end !important;
      }

      .stButton > button[kind="primary"] {
        background: rgba(91, 108, 255, 0.10);
        border-color: rgba(91, 108, 255, 0.42);
        box-shadow: inset 2px 0 0 rgba(91, 108, 255, 0.98);
      }

      .stButton > button[kind="primary"]:hover {
        background: rgba(91, 108, 255, 0.14);
      }

      .stButton > button[kind="tertiary"] {
        background: var(--panel-strong);
        color: var(--text);
        border: 1px solid rgba(110, 124, 142, 0.18);
        border-radius: 4px;
      }

      div[data-baseweb="input"] > div,
      div[data-baseweb="select"] > div,
      .stTextInput input {
        background: var(--panel-strong);
        border-color: rgba(110, 124, 142, 0.18);
        color: var(--text);
        border-radius: 4px;
      }

      div[data-baseweb="input"] > div:focus-within,
      div[data-baseweb="select"] > div:focus-within {
        border-color: rgba(91, 108, 255, 0.32);
        box-shadow: none;
      }

      .stCaption,
      .stMarkdown p,
      .stMarkdown li,
      .stMarkdown label {
        color: var(--muted);
      }

      @media (max-width: 1100px) {
        .shell-topline {
          flex-direction: column;
          align-items: flex-start;
        }
      }
    </style>
    """,
)


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()
    return headers


def _request_json(
    base_url: str,
    api_key: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    response = requests.get(
        url,
        params=params,
        headers=_headers(api_key),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        detail = response.text.strip()
        raise RuntimeError(f"{response.status_code} {response.reason}: {detail or 'request failed'}")
    return response.json()


@st.cache_data(ttl=120, show_spinner=False)
def load_health(base_url: str, api_key: str) -> dict[str, Any]:
    return _request_json(base_url, api_key, "/health")


@st.cache_data(ttl=120, show_spinner=False)
def load_top(base_url: str, api_key: str, limit: int, min_score: float | None) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "include_evidence": "false"}
    if min_score is not None:
        params["min_score"] = min_score
    return _request_json(base_url, api_key, "/risk/top", params=params)


@st.cache_data(ttl=120, show_spinner=False)
def load_history(base_url: str, api_key: str, cik: int, limit: int) -> dict[str, Any]:
    return _request_json(
        base_url,
        api_key,
        f"/risk/{cik}/history",
        params={"limit": limit, "include_evidence": "false"},
    )


@st.cache_data(ttl=120, show_spinner=False)
def load_explain(base_url: str, api_key: str, cik: int) -> dict[str, Any]:
    return _request_json(base_url, api_key, f"/risk/{cik}/explain")


def _format_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def _format_score(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.3f}"


LABEL_OVERRIDES = {
    "v2_monthly_abnormal": "v2 Monthly Abnormal",
    "v1_alert_composite": "v1 Alert Composite",
    "PERSISTENT_PRIORITY": "Persistent Priority",
    "SPIKING_PRIORITY": "Spiking Priority",
    "STABLE_PRIORITY": "Stable Priority",
    "NT_FILING": "NT Filing",
    "8K_SPIKE": "8-K Spike",
    "FRIDAY_BURYING": "Friday Burying",
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
}


def _humanize_label(value: Any) -> str:
    if value is None:
        return "N/A"
    text = str(value).strip()
    if not text:
        return "N/A"
    if text in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[text]
    normalized = text.replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in normalized.split())


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."


def _score_tier(score: Any) -> tuple[str, str]:
    score_value = float(score or 0.0)
    if score_value >= 0.7:
        return "Elevated", "tier-high"
    if score_value >= 0.4:
        return "Monitor", "tier-medium"
    return "Routine", "tier-low"


def _company_label(item: dict[str, Any]) -> str:
    name = item.get("company_name") or "Unknown Issuer"
    ticker = item.get("company_ticker") or "N/A"
    rank = item.get("risk_rank") or "?"
    return f"#{rank} {ticker} | {_truncate_text(str(name), 30)}"


def _history_dataframe(history_items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "as_of_date": item.get("as_of_date"),
            "risk_score": float(item.get("risk_score") or 0.0),
            "risk_rank": item.get("risk_rank"),
        }
        for item in history_items
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"])
    frame = frame.sort_values("as_of_date")
    return frame


def _leaderboard_height(row_count: int) -> int:
    visible_rows = min(max(row_count, 1), VISIBLE_LEADERBOARD_ROWS)
    return 6 + (visible_rows * 24)


def _leaderboard_header_html(as_of_date: str | None) -> str:
    updated = html.escape(as_of_date or "N/A")
    return f"""
    <div class="shell">
      <div class="shell-topline">
        <div>
          <div class="eyebrow">Sec Review Priority</div>
          <div class="title">Issuer Leaderboard</div>
          <div class="subtitle">
            Daily ranked issuers based on filing-behavior abnormality. Click a row to focus an issuer,
            keep the sidebar picker for quick jumps, and use the context panels to understand what is
            driving the current review queue.
          </div>
        </div>
        <div class="timestamp">Updated: {updated}</div>
      </div>
      <div class="selection-note">
        The board shows up to 10 rows at once and scrolls internally for the rest of the watchlist.
      </div>
    </div>
    """


def _leaderboard_columns_html() -> str:
    return """
    <div class="board-header">
      <div>#</div>
      <div>Ticker</div>
      <div>Issuer</div>
      <div>Score / Pct / Tier</div>
    </div>
    """


def _leaderboard_button_label(item: dict[str, Any]) -> str:
    rank = f"#{str(item.get('risk_rank') or '')}"
    ticker = _truncate_text(str(item.get("company_ticker") or "N/A"), 6)
    issuer = _truncate_text(str(item.get("company_name") or "Unknown Issuer"), 26)
    score = _format_score(item.get("risk_score"))
    percentile = _format_percent(item.get("percentile"))
    tier_label, _ = _score_tier(item.get("risk_score"))
    return f"{score}  {percentile}  {tier_label}"


def _leaderboard_row_cells(item: dict[str, Any]) -> list[str]:
    rank = f"#{str(item.get('risk_rank') or '')}"
    ticker = _truncate_text(str(item.get("company_ticker") or "N/A"), 6)
    issuer = _truncate_text(str(item.get("company_name") or "Unknown Issuer"), 26)
    data_points = _leaderboard_button_label(item)
    return [rank, ticker, issuer, data_points]


def _top_signals(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    signals = evidence.get("top_signals_monthly") or evidence.get("top_signals_30d") or []
    return signals if isinstance(signals, list) else []


def _contributors_dataframe(evidence: dict[str, Any]) -> pd.DataFrame:
    contributors = evidence.get("top_contributing_alerts_30d") or []
    if not isinstance(contributors, list) or not contributors:
        return pd.DataFrame()
    rows = [
        {
            "Signal": _humanize_label(item.get("anomaly_type")),
            "Severity": item.get("severity_score"),
            "Contribution": item.get("contribution_proxy"),
            "Event At": item.get("event_at") or item.get("created_at"),
            "Description": item.get("description") or "",
        }
        for item in contributors
    ]
    return pd.DataFrame(rows)


def _history_insight(frame: pd.DataFrame) -> tuple[str, str]:
    if frame.empty or len(frame.index) < 2:
        return "Limited", "Single snapshot"
    latest = float(frame.iloc[-1]["risk_score"])
    earliest = float(frame.iloc[0]["risk_score"])
    delta = latest - earliest
    if delta >= 0.08:
        return "Rising", f"{delta:+.3f} in visible window"
    if delta <= -0.08:
        return "Cooling", f"{delta:+.3f} in visible window"
    return "Steady", f"{delta:+.3f} in visible window"


def _system_context_html(health: dict[str, Any], top_payload: dict[str, Any]) -> str:
    health_status = html.escape(str(health.get("status", "unknown")).upper())
    model_version = html.escape(_humanize_label(top_payload.get("model_version") or "N/A"))
    as_of_date = html.escape(top_payload.get("as_of_date") or "N/A")
    total_rows = html.escape(str(top_payload.get("total", 0)))
    return f"""
    <div class="panel">
      <div class="panel-title">System Context</div>
      <div class="stat-grid">
        <div class="stat">
          <div class="stat-label">Health</div>
          <div class="stat-value">{health_status}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Model</div>
          <div class="stat-value">{model_version}</div>
        </div>
        <div class="stat">
          <div class="stat-label">As Of</div>
          <div class="stat-value">{as_of_date}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Rows</div>
          <div class="stat-value">{total_rows}</div>
        </div>
      </div>
    </div>
    """


def _selected_snapshot_html(score: dict[str, Any], evidence: dict[str, Any], history_frame: pd.DataFrame) -> str:
    tier_label, tier_class = _score_tier(score.get("risk_score"))
    rank_stability = evidence.get("rank_stability") or {}
    uncertainty = evidence.get("uncertainty") or {}
    trend_label, trend_detail = _history_insight(history_frame)
    reason_summary = html.escape(
        _truncate_text(
            str(
                evidence.get("reason_summary")
                or "Priority is driven by the latest anomaly mix and issuer-relative baseline."
            ),
            140,
        )
    )
    return f"""
    <div class="panel">
      <div class="panel-title">Selected Issuer</div>
      <div class="stat-grid">
        <div class="stat">
          <div class="stat-label">Ticker</div>
          <div class="stat-value">{html.escape(score.get("company_ticker") or "N/A")}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Rank</div>
          <div class="stat-value">{html.escape(str(score.get("risk_rank") or "N/A"))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Risk Score</div>
          <div class="stat-value">{html.escape(_format_score(score.get("risk_score")))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Percentile</div>
          <div class="stat-value">{html.escape(_format_percent(score.get("percentile")))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Priority Tier</div>
          <div class="stat-value"><span class="tier {tier_class}">{html.escape(tier_label)}</span></div>
        </div>
        <div class="stat">
          <div class="stat-label">Confidence</div>
          <div class="stat-value">{html.escape(_humanize_label(uncertainty.get("uncertainty_band") or "N/A"))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Stability</div>
          <div class="stat-value">{html.escape(_humanize_label(rank_stability.get("state") or "N/A"))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Calibrated</div>
          <div class="stat-value">{html.escape(_format_score(score.get("calibrated_review_priority")))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Visible Trend</div>
          <div class="stat-value">{html.escape(trend_label)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">History Span</div>
          <div class="stat-value">{html.escape(trend_detail)}</div>
        </div>
      </div>
      <div class="panel-copy">{reason_summary}</div>
    </div>
    """


def _signals_html(evidence: dict[str, Any]) -> str:
    signals = _top_signals(evidence)
    if not signals:
        return """
        <div class="panel">
          <div class="panel-title">Signal Stack</div>
          <div class="subtitle">No signal breakdown is available for this issuer yet.</div>
        </div>
        """

    items: list[str] = []
    for signal in signals[:5]:
        name = html.escape(_humanize_label(signal.get("signal") or "Signal"))
        component = html.escape(_format_score(signal.get("component")))
        count = html.escape(str(signal.get("count") or 0))
        items.append(
            f"""
            <li class="signal-item">
              <div class="signal-name">{name}</div>
              <div class="signal-meta">component {component}  |  count {count}</div>
            </li>
            """
        )
    return f"""
    <div class="panel">
      <div class="panel-title">Signal Stack</div>
      <ul class="signal-list">
        {''.join(items)}
      </ul>
    </div>
    """


def _guardrails_html() -> str:
    return """
    <div class="panel">
      <div class="panel-title">Operator Notes</div>
      <ul class="guardrail-list">
        <li>This is a review-priority leaderboard, not a fraud or liability score.</li>
        <li>Counts describe active modeled alerts, not the issuer's total filing count.</li>
        <li>Use issuer history and explainability before making escalation decisions.</li>
      </ul>
    </div>
    """


def _history_chart(frame: pd.DataFrame):
    if frame.empty:
        return None
    area = (
        alt.Chart(frame)
        .mark_area(color="rgba(91, 108, 255, 0.12)")
        .encode(
            x=alt.X("as_of_date:T", title="As Of Date"),
            y=alt.Y("risk_score:Q", title="Risk Score", scale=alt.Scale(domain=[0, 1])),
        )
    )
    line = (
        alt.Chart(frame)
        .mark_line(color="#5b6cff", size=2.0)
        .encode(
            x=alt.X("as_of_date:T", title="As Of Date", axis=alt.Axis(labelColor="#8f9bab", titleColor="#8f9bab")),
            y=alt.Y(
                "risk_score:Q",
                title="Risk Score",
                scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(labelColor="#8f9bab", titleColor="#8f9bab"),
            ),
            tooltip=["as_of_date:T", "risk_score:Q", "risk_rank:Q"],
        )
    )
    return (
        (area + line)
        .properties(height=260)
        .configure_view(stroke=None)
        .configure(background="transparent")
        .configure_axis(gridColor="rgba(110, 124, 142, 0.16)")
    )


with st.sidebar:
    st.header("Controls")
    api_base_url = st.text_input("API Base URL", value=DEFAULT_API_BASE_URL)
    api_key = st.text_input("API Key", value=DEFAULT_API_KEY, type="password")
    leaderboard_limit = st.slider("Leaderboard size", min_value=10, max_value=100, value=DEFAULT_LIMIT, step=5)
    use_min_score = st.checkbox("Filter minimum score", value=False)
    min_score = None
    if use_min_score:
        min_score = st.slider("Minimum score", min_value=0.0, max_value=1.0, value=0.25, step=0.05)
    history_limit = st.slider("History points", min_value=10, max_value=180, value=60, step=10)
    if st.button("Refresh Data", use_container_width=True, type="tertiary"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Click rows in the leaderboard to focus issuers. The picker below stays available for fast jumps.")


try:
    health = load_health(api_base_url, api_key)
    top_payload = load_top(api_base_url, api_key, leaderboard_limit, min_score)
except Exception as exc:
    st.error(f"Dashboard could not reach the API: {exc}")
    st.stop()

top_items = top_payload.get("items") or []
if not top_items:
    st.warning("No leaderboard rows were returned by /risk/top. Check the daily pipeline and API filters.")
    st.stop()

item_by_cik = {int(item.get("cik")): item for item in top_items}
available_ciks = [int(item.get("cik")) for item in top_items]
label_by_cik = {int(item.get("cik")): _company_label(item) for item in top_items}

current_selected_cik = st.session_state.get("selected_cik")
picker_state = st.session_state.get("issuer_picker")
if picker_state is not None and int(picker_state) in item_by_cik:
    st.session_state["selected_cik"] = int(picker_state)
elif current_selected_cik is None or int(current_selected_cik) not in item_by_cik:
    st.session_state["selected_cik"] = available_ciks[0]
    st.session_state["issuer_picker"] = available_ciks[0]

header_col, _ = st.columns([2.45, 1.0], gap="large")

with header_col:
    st.html(_leaderboard_header_html(top_payload.get("as_of_date")))

with st.sidebar:
    picker_index = available_ciks.index(int(st.session_state["selected_cik"]))
    picker_cik = st.selectbox(
        "Issuer Picker",
        options=available_ciks,
        index=picker_index,
        format_func=lambda cik: label_by_cik.get(int(cik), str(cik)),
        key="issuer_picker",
    )
    if int(picker_cik) != int(st.session_state["selected_cik"]):
        st.session_state["selected_cik"] = int(picker_cik)


@st.fragment
def render_dashboard_fragment() -> None:
    main_col, side_col = st.columns([2.45, 1.0], gap="large")

    with main_col:
        with st.container(border=True):
            st.html(_leaderboard_columns_html())
            with st.container(height=_leaderboard_height(len(top_items)), border=False):
                for item in top_items:
                    cik = int(item.get("cik"))
                    is_selected = cik == int(st.session_state["selected_cik"])
                    row_cells = _leaderboard_row_cells(item)
                    row_cols = st.columns(LEADERBOARD_COLUMN_SPECS, gap=None, vertical_alignment="center")
                    for idx, (col, value) in enumerate(zip(row_cols, row_cells)):
                        with col:
                            if st.button(
                                value,
                                key=f"leaderboard_tile_{cik}_{idx}",
                                type="primary" if is_selected else "secondary",
                                use_container_width=True,
                            ):
                                st.session_state["selected_cik"] = cik
                                st.session_state["issuer_picker"] = cik
                                st.rerun(scope="fragment")
            st.caption(
                "Lean leaderboard reads omit heavy evidence payloads. Scroll inside the board for rows beyond the first 10."
            )

    selected_cik = int(st.session_state["selected_cik"])

    try:
        history_payload = load_history(api_base_url, api_key, selected_cik, history_limit)
        explain_payload = load_explain(api_base_url, api_key, selected_cik)
    except Exception as exc:
        st.error(f"Issuer drilldown failed: {exc}")
        st.stop()

    score = explain_payload.get("score") or {}
    evidence = score.get("evidence") or {}
    history_items = history_payload.get("items") or []
    history_frame = _history_dataframe(history_items)

    with side_col:
        st.html(_system_context_html(health, top_payload))
        st.html(_selected_snapshot_html(score, evidence, history_frame))
        st.html(_signals_html(evidence))
        st.html(_guardrails_html())

    with main_col:
        with st.container(border=True):
            st.markdown("### Selected Issuer History")
            if history_frame.empty:
                st.info("No history points were returned for this issuer.")
            else:
                chart = _history_chart(history_frame)
                if chart is not None:
                    st.altair_chart(chart, use_container_width=True)
            st.caption(
                score.get("reason_summary")
                or evidence.get("reason_summary")
                or "Use explainability and history together before escalating an issuer."
            )

        contributors_frame = _contributors_dataframe(evidence)
        with st.container(border=True):
            st.markdown("### Contributing Alerts")
            if contributors_frame.empty:
                st.info("No contributing alert detail is available.")
            else:
                st.dataframe(contributors_frame, use_container_width=True, hide_index=True)

    with st.expander("Raw Explain Payload"):
        st.code(json.dumps(explain_payload, indent=2, sort_keys=True), language="json")


render_dashboard_fragment()
