"""Verify outcome candidate rows against SEC filing content.

This verifier supports multiple outcome families with confidence tiers.

Statuses:
- VERIFIED_HIGH
- VERIFIED_MEDIUM
- POSSIBLE
- REJECTED

Outputs:
- review CSV with status/reason and signal columns
- verified CSV containing rows that meet configured minimum confidence
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.db import db_utils

STATUS_VERIFIED_HIGH = "VERIFIED_HIGH"
STATUS_VERIFIED_MEDIUM = "VERIFIED_MEDIUM"
STATUS_POSSIBLE = "POSSIBLE"
STATUS_REJECTED = "REJECTED"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

CONFIDENCE_ORDER = {
    CONFIDENCE_LOW: 1,
    CONFIDENCE_MEDIUM: 2,
    CONFIDENCE_HIGH: 3,
}

OUTCOME_FAMILY_PRIORITY = {
    "ITEM_402_NON_RELIANCE": 100,
    "ITEM_103_BANKRUPTCY": 90,
    "ITEM_301_DELISTING": 80,
    "ITEM_206_IMPAIRMENT": 70,
    "ITEM_401_AUDITOR_CHANGE": 60,
    "AMENDMENT_RESTATEMENT": 50,
}

OUTCOME_TYPE_BY_FAMILY = {
    "ITEM_402_NON_RELIANCE": "RESTATEMENT_DISCLOSURE",
    "ITEM_103_BANKRUPTCY": "BANKRUPTCY_DISCLOSURE",
    "ITEM_301_DELISTING": "DELISTING_NOTICE_DISCLOSURE",
    "ITEM_206_IMPAIRMENT": "IMPAIRMENT_DISCLOSURE",
    "ITEM_401_AUDITOR_CHANGE": "AUDITOR_CHANGE_DISCLOSURE",
    "AMENDMENT_RESTATEMENT": "RESTATEMENT_DISCLOSURE",
}

ITEM_BY_FAMILY = {
    "ITEM_402_NON_RELIANCE": "4.02",
    "ITEM_103_BANKRUPTCY": "1.03",
    "ITEM_301_DELISTING": "3.01",
    "ITEM_206_IMPAIRMENT": "2.06",
    "ITEM_401_AUDITOR_CHANGE": "4.01",
    "AMENDMENT_RESTATEMENT": "",
}

ITEM_103_RE = re.compile(r"item\s*1\.?0?3", re.IGNORECASE)
ITEM_206_RE = re.compile(r"item\s*2\.?0?6", re.IGNORECASE)
ITEM_301_RE = re.compile(r"item\s*3\.?0?1", re.IGNORECASE)
ITEM_401_RE = re.compile(r"item\s*4\.?0?1", re.IGNORECASE)
ITEM_402_RE = re.compile(r"item\s*4\.?0?2", re.IGNORECASE)

NON_RELIANCE_PHRASES = [
    "non-reliance on previously issued financial statements",
    "non-reliance on previously issued",
    "should no longer be relied upon",
    "should no longer rely upon",
]
RESTATEMENT_PHRASES = [
    "restatement",
    "restate",
    "material misstatement",
]
AUDITOR_CHANGE_PHRASES = [
    "independent registered public accounting firm",
    "dismissed",
    "resigned",
    "declined to stand for re-election",
    "disagreement",
]
DELISTING_PHRASES = [
    "delist",
    "deficiency notice",
    "listing standards",
    "listing qualification",
    "non-compliance",
    "noncompliance",
    "nasdaq",
    "nyse",
]
IMPAIRMENT_PHRASES = [
    "material impairment",
    "impairment charge",
    "impairment charges",
]
BANKRUPTCY_PHRASES = [
    "bankruptcy",
    "chapter 11",
    "chapter 7",
    "receivership",
]


def _empty_signals() -> dict[str, bool]:
    return {
        "has_item_103": False,
        "has_item_206": False,
        "has_item_301": False,
        "has_item_401": False,
        "has_item_402": False,
        "has_non_reliance": False,
        "has_restatement_phrase": False,
        "has_auditor_change_phrase": False,
        "has_delisting_phrase": False,
        "has_impairment_phrase": False,
        "has_bankruptcy_phrase": False,
    }


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _normalize_form(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().upper()


def _extract_accession_nodash(row: dict[str, str]) -> str | None:
    accession = (row.get("accession_id") or "").strip()
    if accession:
        return accession.replace("-", "")

    dedupe = (row.get("dedupe_key") or "").strip()
    if not dedupe:
        return None

    token = dedupe.rsplit(":", 1)[-1]
    if token.isdigit() and len(token) >= 12:
        return token
    return None


def _to_accession_with_dashes(token: str) -> str:
    if "-" in token:
        return token
    if len(token) != 18:
        return token
    return f"{token[:10]}-{token[10:12]}-{token[12:]}"


def _lookup_primary_document(conn, cik: int, accession_id: str, event_date: str) -> str:
    row = conn.execute(
        """
        SELECT primary_document
        FROM filing_events
        WHERE cik = ?
          AND (accession_id = ? OR REPLACE(accession_id, '-', '') = ?)
        LIMIT 1
        """,
        (cik, accession_id, accession_id.replace("-", "")),
    ).fetchone()
    if row and row["primary_document"]:
        return str(row["primary_document"])

    fallback = conn.execute(
        """
        SELECT primary_document
        FROM filing_events
        WHERE cik = ?
          AND filed_date = ?
          AND filing_type IN ('8-K','8-K/A')
        ORDER BY accession_id DESC
        LIMIT 1
        """,
        (cik, event_date),
    ).fetchone()
    if fallback and fallback["primary_document"]:
        return str(fallback["primary_document"])
    return ""


def _normalized_existing_url(existing_url: str) -> str:
    existing = existing_url.strip()
    if not existing:
        return ""

    parsed = urlparse(existing)
    if "ixviewer/ix.html" not in parsed.path:
        return existing

    # SEC ixviewer wraps the real filing path in a "doc" query parameter.
    doc_paths = parse_qs(parsed.query).get("doc", [])
    if not doc_paths:
        return existing

    resolved = unquote(doc_paths[0]).strip()
    if not resolved:
        return existing
    if resolved.startswith("/"):
        return f"https://www.sec.gov{resolved}"
    return resolved


def _unique_urls(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        candidate = raw.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _build_url_candidates(
    cik: int,
    accession_nodash: str,
    primary_document: str,
    existing_url: str,
) -> list[str]:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/"
    accession_id = _to_accession_with_dashes(accession_nodash)
    existing = _normalized_existing_url(existing_url)

    urls: list[str] = []
    if existing:
        urls.append(existing)

    # Primary document is typically the most reliable when present.
    if primary_document:
        urls.append(f"{base}{primary_document}")

    # Fallbacks for sparse metadata cases.
    urls.append(f"{base}{accession_nodash}.txt")
    urls.append(f"{base}{accession_id}-index.html")
    urls.append(f"{base}index.json")
    urls.append(f"{base}index.html")
    urls.append(base)
    return _unique_urls(urls)


def _extract_candidate_docs_from_index_json(index_json_url: str, payload: str) -> list[str]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []

    directory = parsed.get("directory")
    if not isinstance(directory, dict):
        return []
    items = directory.get("item")
    if not isinstance(items, list):
        return []

    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered.endswith("/"):
            continue
        if not lowered.endswith((".htm", ".html", ".txt", ".xml")):
            continue
        names.append(name)

    def _doc_rank(name: str) -> tuple[int, int, str]:
        lowered = name.lower()
        score = 0
        if lowered.endswith((".htm", ".html", ".txt")):
            score -= 3
        if "8k" in lowered or "10q" in lowered or "10k" in lowered:
            score -= 2
        if "exhibit" in lowered or lowered.startswith("ex"):
            score += 2
        if lowered.endswith(".xml"):
            score += 3
        return (score, len(lowered), lowered)

    names.sort(key=_doc_rank)
    return _unique_urls(urljoin(index_json_url, name) for name in names)


def _extract_candidate_docs_from_index_html(index_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        lowered = href.lower()
        if not lowered.endswith((".htm", ".html", ".txt", ".xml")):
            continue
        links.append(urljoin(index_url, href))
    return _unique_urls(links)


def _looks_like_filing_index_page(text: str) -> bool:
    normalized = text.lower()
    return (
        "filing detail" in normalized
        or "document format files" in normalized
        or "data files" in normalized
    )


def _fetch_filing_text(
    session: requests.Session,
    url_candidates: Iterable[str],
    timeout_seconds: int,
) -> tuple[str, str]:
    pending = list(url_candidates)
    seen: set[str] = set()
    errors: list[str] = []

    while pending:
        url = pending.pop(0)
        normalized = url.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        try:
            response = session.get(normalized, timeout=timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            errors.append(f"{normalized} -> {exc}")
            continue

        content_type = (response.headers.get("Content-Type") or "").lower()
        body = response.text or ""

        if normalized.endswith("index.json") or "application/json" in content_type:
            pending = _extract_candidate_docs_from_index_json(normalized, body) + pending
            continue

        if _looks_like_filing_index_page(body):
            pending = _extract_candidate_docs_from_index_html(normalized, body) + pending
            continue

        cleaned = _clean_text(body)
        if cleaned:
            return cleaned, normalized
        errors.append(f"{normalized} -> empty response body")

    if errors:
        raise requests.RequestException(" | ".join(errors))
    raise requests.RequestException("No URL candidates available")


def _confidence_to_status(confidence: str | None) -> str:
    if confidence == CONFIDENCE_HIGH:
        return STATUS_VERIFIED_HIGH
    if confidence == CONFIDENCE_MEDIUM:
        return STATUS_VERIFIED_MEDIUM
    if confidence == CONFIDENCE_LOW:
        return STATUS_POSSIBLE
    return STATUS_REJECTED


def _confidence_from_status(status: str | None) -> str | None:
    if status == STATUS_VERIFIED_HIGH:
        return CONFIDENCE_HIGH
    if status == STATUS_VERIFIED_MEDIUM:
        return CONFIDENCE_MEDIUM
    if status == STATUS_POSSIBLE:
        return CONFIDENCE_LOW
    return None


def _confidence_meets_threshold(confidence: str | None, min_confidence: str) -> bool:
    value = CONFIDENCE_ORDER.get((confidence or "").upper(), 0)
    threshold = CONFIDENCE_ORDER[min_confidence.upper()]
    return value >= threshold


def _pick_best_match(matches: list[dict[str, str]]) -> dict[str, str] | None:
    if not matches:
        return None

    confidence_rank = {
        CONFIDENCE_HIGH: 3,
        CONFIDENCE_MEDIUM: 2,
        CONFIDENCE_LOW: 1,
    }

    def _key(item: dict[str, str]) -> tuple[int, int]:
        return (
            confidence_rank.get(item["confidence_band"], 0),
            OUTCOME_FAMILY_PRIORITY.get(item["outcome_family"], 0),
        )

    return max(matches, key=_key)


def _verify_text(
    text: str,
    filing_form: str,
) -> tuple[str, str | None, str | None, str | None, dict[str, bool], str]:
    normalized_text = text.lower()

    has_item_103 = bool(ITEM_103_RE.search(normalized_text))
    has_item_206 = bool(ITEM_206_RE.search(normalized_text))
    has_item_301 = bool(ITEM_301_RE.search(normalized_text))
    has_item_401 = bool(ITEM_401_RE.search(normalized_text))
    has_item_402 = bool(ITEM_402_RE.search(normalized_text))

    has_non_reliance = _contains_any(normalized_text, NON_RELIANCE_PHRASES)
    has_restatement = _contains_any(normalized_text, RESTATEMENT_PHRASES)
    has_auditor_change = _contains_any(normalized_text, AUDITOR_CHANGE_PHRASES)
    has_delisting = _contains_any(normalized_text, DELISTING_PHRASES)
    has_impairment = _contains_any(normalized_text, IMPAIRMENT_PHRASES)
    has_bankruptcy = _contains_any(normalized_text, BANKRUPTCY_PHRASES)

    normalized_form = _normalize_form(filing_form)
    is_amendment = normalized_form.endswith("/A")

    signals = {
        "has_item_103": has_item_103,
        "has_item_206": has_item_206,
        "has_item_301": has_item_301,
        "has_item_401": has_item_401,
        "has_item_402": has_item_402,
        "has_non_reliance": has_non_reliance,
        "has_restatement_phrase": has_restatement,
        "has_auditor_change_phrase": has_auditor_change,
        "has_delisting_phrase": has_delisting,
        "has_impairment_phrase": has_impairment,
        "has_bankruptcy_phrase": has_bankruptcy,
    }

    matches: list[dict[str, str]] = []

    if has_item_402:
        if has_non_reliance or has_restatement:
            matches.append(
                {
                    "outcome_family": "ITEM_402_NON_RELIANCE",
                    "confidence_band": CONFIDENCE_HIGH,
                    "reason": "Detected Item 4.02 with non-reliance/restatement wording",
                }
            )
        else:
            matches.append(
                {
                    "outcome_family": "ITEM_402_NON_RELIANCE",
                    "confidence_band": CONFIDENCE_MEDIUM,
                    "reason": "Detected Item 4.02 without explicit non-reliance phrase",
                }
            )

    if has_item_401:
        matches.append(
            {
                "outcome_family": "ITEM_401_AUDITOR_CHANGE",
                "confidence_band": CONFIDENCE_HIGH if has_auditor_change else CONFIDENCE_MEDIUM,
                "reason": "Detected Item 4.01 auditor change/disagreement pattern"
                if has_auditor_change
                else "Detected Item 4.01 with limited supporting wording",
            }
        )

    if has_item_301:
        matches.append(
            {
                "outcome_family": "ITEM_301_DELISTING",
                "confidence_band": CONFIDENCE_HIGH if has_delisting else CONFIDENCE_MEDIUM,
                "reason": "Detected Item 3.01 delisting/non-compliance pattern"
                if has_delisting
                else "Detected Item 3.01 with limited supporting wording",
            }
        )

    if has_item_206:
        matches.append(
            {
                "outcome_family": "ITEM_206_IMPAIRMENT",
                "confidence_band": CONFIDENCE_HIGH if has_impairment else CONFIDENCE_MEDIUM,
                "reason": "Detected Item 2.06 material impairment pattern"
                if has_impairment
                else "Detected Item 2.06 with limited supporting wording",
            }
        )

    if has_item_103:
        matches.append(
            {
                "outcome_family": "ITEM_103_BANKRUPTCY",
                "confidence_band": CONFIDENCE_HIGH if has_bankruptcy else CONFIDENCE_MEDIUM,
                "reason": "Detected Item 1.03 bankruptcy/receivership pattern"
                if has_bankruptcy
                else "Detected Item 1.03 with limited supporting wording",
            }
        )

    if is_amendment and has_restatement:
        matches.append(
            {
                "outcome_family": "AMENDMENT_RESTATEMENT",
                "confidence_band": CONFIDENCE_MEDIUM,
                "reason": "Detected amendment form with restatement wording",
            }
        )

    if not matches and has_restatement:
        matches.append(
            {
                "outcome_family": "AMENDMENT_RESTATEMENT",
                "confidence_band": CONFIDENCE_LOW,
                "reason": "Detected restatement wording without strong item-level evidence",
            }
        )

    best = _pick_best_match(matches)
    if best is None:
        return (
            STATUS_REJECTED,
            None,
            None,
            None,
            signals,
            "No supported adverse-outcome indicators found",
        )

    outcome_family = best["outcome_family"]
    confidence_band = best["confidence_band"]
    outcome_type = OUTCOME_TYPE_BY_FAMILY[outcome_family]
    status = _confidence_to_status(confidence_band)
    return status, confidence_band, outcome_family, outcome_type, signals, best["reason"]


def verify_candidates(
    input_csv: Path,
    review_csv: Path,
    verified_csv: Path,
    db_path: Path | None = None,
    sec_identity: str = "",
    timeout_seconds: int = 20,
    sleep_seconds: float = 0.2,
    max_rows: int | None = None,
    min_confidence_for_export: str = CONFIDENCE_HIGH,
) -> dict[str, Any]:
    user_agent = sec_identity.strip() or "ReviewPriorityVerifier/1.0 (local)"
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    with input_csv.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if max_rows is not None:
        rows = rows[:max_rows]

    review_rows: list[dict[str, str]] = []
    verified_rows: list[dict[str, str]] = []

    status_counts: dict[str, int] = {}

    with db_utils.get_conn(path=db_path) as conn:
        for row in rows:
            out = dict(row)
            try:
                cik = int((row.get("cik") or "").strip())
                event_date = (row.get("event_date") or "").strip()
                accession_nodash = _extract_accession_nodash(row)

                if not accession_nodash:
                    status = "MISSING_METADATA"
                    reason = "Missing accession identifier"
                    confidence_band = None
                    outcome_family = None
                    detected_outcome_type = None
                    url = (row.get("url") or "").strip()
                    signals = _empty_signals()
                else:
                    accession_id = _to_accession_with_dashes(accession_nodash)
                    primary_document = (row.get("primary_document") or "").strip() or _lookup_primary_document(
                        conn,
                        cik=cik,
                        accession_id=accession_id,
                        event_date=event_date,
                    )
                    url_candidates = _build_url_candidates(
                        cik=cik,
                        accession_nodash=accession_nodash,
                        primary_document=primary_document,
                        existing_url=row.get("url") or "",
                    )
                    if not url_candidates:
                        status = "MISSING_METADATA"
                        reason = "Missing URL/primary_document"
                        confidence_band = None
                        outcome_family = None
                        detected_outcome_type = None
                        url = (row.get("url") or "").strip()
                        signals = _empty_signals()
                    else:
                        text, resolved_url = _fetch_filing_text(
                            session=session,
                            url_candidates=url_candidates,
                            timeout_seconds=timeout_seconds,
                        )
                        url = resolved_url
                        status, confidence_band, outcome_family, detected_outcome_type, signals, reason = _verify_text(
                            text=text,
                            filing_form=str(row.get("form") or ""),
                        )

                out["verification_status"] = status
                out["verification_reason"] = reason
                out["url"] = url
                out["confidence_band"] = confidence_band or ""
                out["outcome_family"] = outcome_family or ""

                if detected_outcome_type:
                    out["outcome_type"] = detected_outcome_type
                if outcome_family and not (out.get("item") or "").strip():
                    out["item"] = ITEM_BY_FAMILY.get(outcome_family, "")

                out["has_item_103"] = str(signals["has_item_103"]).lower()
                out["has_item_206"] = str(signals["has_item_206"]).lower()
                out["has_item_301"] = str(signals["has_item_301"]).lower()
                out["has_item_401"] = str(signals["has_item_401"]).lower()
                out["has_item_402"] = str(signals["has_item_402"]).lower()
                out["has_non_reliance"] = str(signals["has_non_reliance"]).lower()
                out["has_restatement_phrase"] = str(signals["has_restatement_phrase"]).lower()
                out["has_auditor_change_phrase"] = str(signals["has_auditor_change_phrase"]).lower()
                out["has_delisting_phrase"] = str(signals["has_delisting_phrase"]).lower()
                out["has_impairment_phrase"] = str(signals["has_impairment_phrase"]).lower()
                out["has_bankruptcy_phrase"] = str(signals["has_bankruptcy_phrase"]).lower()

            except requests.RequestException as exc:
                out["verification_status"] = "FETCH_ERROR"
                out["verification_reason"] = f"HTTP error: {exc}"
                out.setdefault("url", (row.get("url") or "").strip())
                out["confidence_band"] = ""
                out["outcome_family"] = ""
                out["has_item_103"] = "false"
                out["has_item_206"] = "false"
                out["has_item_301"] = "false"
                out["has_item_401"] = "false"
                out["has_item_402"] = "false"
                out["has_non_reliance"] = "false"
                out["has_restatement_phrase"] = "false"
                out["has_auditor_change_phrase"] = "false"
                out["has_delisting_phrase"] = "false"
                out["has_impairment_phrase"] = "false"
                out["has_bankruptcy_phrase"] = "false"
            except Exception as exc:  # pragma: no cover - defensive catch
                out["verification_status"] = "ERROR"
                out["verification_reason"] = str(exc)
                out.setdefault("url", (row.get("url") or "").strip())
                out["confidence_band"] = ""
                out["outcome_family"] = ""
                out["has_item_103"] = "false"
                out["has_item_206"] = "false"
                out["has_item_301"] = "false"
                out["has_item_401"] = "false"
                out["has_item_402"] = "false"
                out["has_non_reliance"] = "false"
                out["has_restatement_phrase"] = "false"
                out["has_auditor_change_phrase"] = "false"
                out["has_delisting_phrase"] = "false"
                out["has_impairment_phrase"] = "false"
                out["has_bankruptcy_phrase"] = "false"

            review_rows.append(out)
            status = out["verification_status"]
            status_counts[status] = status_counts.get(status, 0) + 1

            confidence = (out.get("confidence_band") or "").upper() or _confidence_from_status(status)
            if _confidence_meets_threshold(confidence, min_confidence_for_export):
                verified_rows.append(out)

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    review_fields = list(rows[0].keys()) if rows else []
    for col in [
        "verification_status",
        "verification_reason",
        "confidence_band",
        "outcome_family",
        "has_item_103",
        "has_item_206",
        "has_item_301",
        "has_item_401",
        "has_item_402",
        "has_non_reliance",
        "has_restatement_phrase",
        "has_auditor_change_phrase",
        "has_delisting_phrase",
        "has_impairment_phrase",
        "has_bankruptcy_phrase",
    ]:
        if col not in review_fields:
            review_fields.append(col)

    with review_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        writer.writerows(review_rows)

    import_fields = [
        "cik",
        "event_date",
        "outcome_type",
        "source",
        "description",
        "dedupe_key",
        "form",
        "item",
        "url",
        "accession_id",
        "verification_status",
        "verification_reason",
        "confidence_band",
        "outcome_family",
    ]
    with verified_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=import_fields)
        writer.writeheader()
        for row in verified_rows:
            out = {field: row.get(field, "") for field in import_fields}
            writer.writerow(out)

    return {
        "input_csv": str(input_csv),
        "review_csv": str(review_csv),
        "verified_csv": str(verified_csv),
        "rows_processed": len(review_rows),
        "rows_exported": len(verified_rows),
        "rows_verified_high": status_counts.get(STATUS_VERIFIED_HIGH, 0),
        "status_counts": status_counts,
        "min_confidence_for_export": min_confidence_for_export,
        "user_agent": user_agent,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify SEC outcome candidates and emit import-ready labels.")
    parser.add_argument("--input", default="data/outcomes_candidates.csv", help="Candidate CSV path")
    parser.add_argument(
        "--review-output",
        default="data/outcomes_reviewed.csv",
        help="Output CSV with verification status per row",
    )
    parser.add_argument(
        "--verified-output",
        default="data/outcomes.csv",
        help="Output CSV containing rows meeting minimum confidence",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional sqlite DB path override. Leave unset to use DB_BACKEND + DATABASE_URL env.",
    )
    parser.add_argument(
        "--sec-identity",
        default="",
        help="SEC-compliant User-Agent identity (or set SEC_IDENTITY env and pass here)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument(
        "--min-confidence-for-export",
        default=CONFIDENCE_HIGH,
        choices=[CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW],
        help="Minimum confidence band exported into the import-ready CSV",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = verify_candidates(
        input_csv=Path(args.input),
        review_csv=Path(args.review_output),
        verified_csv=Path(args.verified_output),
        db_path=Path(args.db_path) if args.db_path else None,
        sec_identity=args.sec_identity,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=args.sleep_seconds,
        max_rows=args.max_rows,
        min_confidence_for_export=args.min_confidence_for_export,
    )
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
