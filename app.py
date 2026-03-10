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
        --bg: #020406;
        --bg-soft: #07101a;
        --panel: rgba(7, 15, 24, 0.94);
        --panel-strong: rgba(4, 10, 16, 0.98);
        --line: rgba(43, 78, 110, 0.45);
        --line-soft: rgba(43, 78, 110, 0.20);
        --text: #d5deea;
        --muted: #73869d;
        --accent: #17f0a8;
        --accent-soft: rgba(23, 240, 168, 0.10);
        --ice: #71d8ff;
        --amber: #f2bf59;
        --warn: #ff6f7c;
      }

      .stApp {
        background:
          radial-gradient(circle at top right, rgba(23, 240, 168, 0.08), transparent 22%),
          radial-gradient(circle at top left, rgba(113, 216, 255, 0.06), transparent 20%),
          linear-gradient(180deg, #010305 0%, #02060a 100%);
        color: var(--text);
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      }

      [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(5, 9, 14, 0.98), rgba(4, 8, 13, 0.98));
        border-right: 1px solid var(--line-soft);
      }

      [data-testid="stHeader"] {
        background: transparent;
      }

      h1, h2, h3, h4 {
        font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
        letter-spacing: 0.03em;
      }

      .shell {
        border: 1px solid var(--line-soft);
        border-radius: 26px;
        background: linear-gradient(180deg, rgba(3, 8, 13, 0.92), rgba(3, 7, 12, 0.98));
        padding: 1.35rem 1.35rem 1.1rem 1.35rem;
        box-shadow: 0 22px 60px rgba(0, 0, 0, 0.36);
        overflow: hidden;
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
        color: var(--ice);
        font-size: 0.76rem;
        letter-spacing: 0.16em;
        text-transform: uppercase;
      }

      .title {
        margin-top: 0.35rem;
        font-size: 1.85rem;
        color: var(--text);
        font-family: "Georgia", "Times New Roman", serif;
        letter-spacing: 0.01em;
      }

      .subtitle {
        color: var(--muted);
        margin-top: 0.35rem;
        max-width: 34rem;
        font-size: 0.96rem;
        line-height: 1.65;
      }

      .timestamp {
        color: var(--muted);
        font-size: 0.76rem;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        white-space: nowrap;
      }

      .board {
        width: 100%;
        border-collapse: collapse;
        margin-top: 1rem;
      }

      .board th {
        color: var(--muted);
        font-size: 0.74rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        padding: 0.95rem 0.85rem;
        border-bottom: 1px solid var(--line);
        text-align: left;
      }

      .board td {
        padding: 1rem 0.85rem;
        border-bottom: 1px solid var(--line-soft);
        vertical-align: middle;
        font-size: 0.95rem;
      }

      .board tr:hover td {
        background: rgba(14, 29, 43, 0.34);
      }

      .board tr.selected-row td {
        background: linear-gradient(90deg, rgba(23, 240, 168, 0.12), rgba(23, 240, 168, 0.03));
        box-shadow: inset 3px 0 0 rgba(23, 240, 168, 0.6);
      }

      .rank-cell {
        color: var(--muted);
        width: 3rem;
      }

      .issuer-ticker {
        color: var(--ice);
        margin-right: 0.55rem;
      }

      .issuer-name {
        color: var(--text);
        font-weight: 700;
        letter-spacing: 0.02em;
      }

      .score-cell {
        color: var(--text);
        font-weight: 700;
      }

      .percentile-cell {
        color: var(--accent);
        font-weight: 700;
      }

      .tier {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 0.24rem 0.6rem;
        font-size: 0.74rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        border: 1px solid transparent;
      }

      .tier-high {
        color: var(--warn);
        background: rgba(255, 111, 124, 0.10);
        border-color: rgba(255, 111, 124, 0.22);
      }

      .tier-medium {
        color: var(--amber);
        background: rgba(242, 191, 89, 0.10);
        border-color: rgba(242, 191, 89, 0.22);
      }

      .tier-low {
        color: var(--accent);
        background: rgba(23, 240, 168, 0.10);
        border-color: rgba(23, 240, 168, 0.22);
      }

      .panel {
        border: 1px solid var(--line-soft);
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(5, 11, 17, 0.96), rgba(4, 9, 14, 0.98));
        padding: 1rem 1rem 0.95rem 1rem;
        box-shadow: 0 14px 32px rgba(0, 0, 0, 0.24);
        position: relative;
        overflow: hidden;
        transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
      }

      .panel::before {
        content: "";
        position: absolute;
        inset: 0 0 auto 0;
        height: 1px;
        background: linear-gradient(90deg, rgba(113, 216, 255, 0), rgba(23, 240, 168, 0.45), rgba(113, 216, 255, 0));
      }

      .panel:hover {
        transform: translateY(-2px);
        border-color: rgba(23, 240, 168, 0.26);
        box-shadow: 0 18px 38px rgba(0, 0, 0, 0.30);
      }

      .panel-title {
        color: var(--ice);
        font-size: 0.82rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-bottom: 0.8rem;
      }

      .stat-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.7rem;
      }

      .stat {
        border: 1px solid var(--line-soft);
        border-radius: 14px;
        padding: 0.75rem 0.8rem;
        background: rgba(10, 21, 33, 0.58);
        min-height: 92px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
      }

      .stat-label {
        color: var(--muted);
        font-size: 0.72rem;
        letter-spacing: 0.10em;
        text-transform: uppercase;
      }

      .stat-value {
        color: var(--text);
        font-size: 1.05rem;
        margin-top: 0.35rem;
        font-weight: 700;
        line-height: 1.18;
        word-break: break-word;
      }

      .panel-copy {
        color: var(--muted);
        font-size: 0.84rem;
        line-height: 1.55;
        margin-top: 0.85rem;
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
        padding: 0.55rem 0;
        border-bottom: 1px solid var(--line-soft);
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

      .footnote {
        color: var(--muted);
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.10em;
        margin-top: 1rem;
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


def _top_signals(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    signals = evidence.get("top_signals_monthly") or evidence.get("top_signals_30d") or []
    return signals if isinstance(signals, list) else []


def _contributors_dataframe(evidence: dict[str, Any]) -> pd.DataFrame:
    contributors = evidence.get("top_contributing_alerts_30d") or []
    if not isinstance(contributors, list) or not contributors:
        return pd.DataFrame()
    rows = [
        {
            "Signal": item.get("anomaly_type"),
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


def _leaderboard_table_html(items: list[dict[str, Any]], selected_cik: int | None, as_of_date: str | None) -> str:
    rows: list[str] = []
    for item in items:
        cik = int(item.get("cik"))
        issuer_name = html.escape(item.get("company_name") or "Unknown Issuer")
        ticker = html.escape(item.get("company_ticker") or "N/A")
        rank = html.escape(str(item.get("risk_rank") or ""))
        score = html.escape(_format_score(item.get("risk_score")))
        percentile = html.escape(_format_percent(item.get("percentile")))
        tier_label, tier_class = _score_tier(item.get("risk_score"))
        row_class = "selected-row" if selected_cik is not None and cik == selected_cik else ""
        rows.append(
            f"""
            <tr class="{row_class}">
              <td class="rank-cell">{rank}</td>
              <td>
                <span class="issuer-ticker">{ticker}</span>
                <span class="issuer-name">{issuer_name}</span>
              </td>
              <td class="score-cell">{score}</td>
              <td class="percentile-cell">{percentile}</td>
              <td><span class="tier {tier_class}">{html.escape(tier_label)}</span></td>
            </tr>
            """
        )

    updated = html.escape(as_of_date or "N/A")
    return f"""
    <div class="shell">
      <div class="shell-topline">
        <div>
          <div class="eyebrow">Sec Review Priority</div>
          <div class="title">Issuer Leaderboard</div>
          <div class="subtitle">
            Daily ranked issuers based on filing-behavior abnormality. Use the right-side context to inspect
            stability, confidence, and the signals driving the current priority tier.
          </div>
        </div>
        <div class="timestamp">Updated: {updated}</div>
      </div>
      <table class="board">
        <thead>
          <tr>
            <th>#</th>
            <th>Issuer</th>
            <th>Score</th>
            <th>Percentile</th>
            <th>Tier</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
      <div class="footnote">Leaderboard uses lean API reads for watchlist speed.</div>
    </div>
    """


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
            str(evidence.get("reason_summary") or "Priority is driven by the latest anomaly mix and issuer-relative baseline."),
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
        <li>Lean table rows intentionally omit heavy evidence payloads for speed.</li>
        <li>Use issuer history and explainability before making escalation decisions.</li>
      </ul>
    </div>
    """


def _history_chart(frame: pd.DataFrame):
    if frame.empty:
        return None
    return (
        alt.Chart(frame)
        .mark_area(
            line={"color": "#17f0a8", "size": 2.0},
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="rgba(23, 240, 168, 0.35)", offset=0),
                    alt.GradientStop(color="rgba(23, 240, 168, 0.02)", offset=1),
                ],
                x1=1,
                x2=1,
                y1=1,
                y2=0,
            ),
        )
        .encode(
            x=alt.X("as_of_date:T", title="As Of Date", axis=alt.Axis(labelColor="#73869d", titleColor="#73869d")),
            y=alt.Y(
                "risk_score:Q",
                title="Risk Score",
                scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(labelColor="#73869d", titleColor="#73869d"),
            ),
            tooltip=["as_of_date:T", "risk_score:Q", "risk_rank:Q"],
        )
        .properties(height=260)
        .configure_view(stroke=None)
        .configure(background="transparent")
        .configure_axis(gridColor="rgba(43, 78, 110, 0.18)")
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
    if st.button("Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Dashboard uses lean leaderboard/history reads and full explain only for the focused issuer.")


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

labels = [_company_label(item) for item in top_items]
default_cik = st.session_state.get("selected_cik")
default_index = 0
if default_cik is not None:
    for index, item in enumerate(top_items):
        if int(item.get("cik")) == int(default_cik):
            default_index = index
            break

main_col, side_col = st.columns([2.4, 1.0], gap="large")

with side_col:
    selected_label = st.selectbox("Focus Issuer", options=labels, index=default_index)
    selected_item = top_items[labels.index(selected_label)]
    selected_cik = int(selected_item["cik"])
    st.session_state["selected_cik"] = selected_cik

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

    st.html(_system_context_html(health, top_payload))
    st.html(_selected_snapshot_html(score, evidence, history_frame))
    st.html(_signals_html(evidence))
    st.html(_guardrails_html())

with main_col:
    st.html(
        _leaderboard_table_html(
            top_items,
            selected_cik=selected_cik,
            as_of_date=top_payload.get("as_of_date"),
        )
    )

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
