import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.verify_outcomes import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    STATUS_POSSIBLE,
    STATUS_VERIFIED_HIGH,
    STATUS_VERIFIED_MEDIUM,
    _build_url_candidates,
    _confidence_meets_threshold,
    _fetch_filing_text,
    _verify_text,
)


def test_verify_text_item_402_high_confidence() -> None:
    text = "Item 4.02 Non-reliance on previously issued financial statements."
    status, confidence, family, outcome_type, signals, reason = _verify_text(text, filing_form="8-K")

    assert status == STATUS_VERIFIED_HIGH
    assert confidence == CONFIDENCE_HIGH
    assert family == "ITEM_402_NON_RELIANCE"
    assert outcome_type == "RESTATEMENT_DISCLOSURE"
    assert signals["has_item_402"] is True
    assert "4.02" in reason


def test_verify_text_item_301_high_confidence() -> None:
    text = "Item 3.01 The company received a Nasdaq deficiency notice for non-compliance with listing standards."
    status, confidence, family, outcome_type, signals, _ = _verify_text(text, filing_form="8-K")

    assert status == STATUS_VERIFIED_HIGH
    assert confidence == CONFIDENCE_HIGH
    assert family == "ITEM_301_DELISTING"
    assert outcome_type == "DELISTING_NOTICE_DISCLOSURE"
    assert signals["has_item_301"] is True


def test_verify_text_item_401_medium_confidence() -> None:
    text = "Item 4.01 Changes in registrant's certifying accountant."
    status, confidence, family, outcome_type, signals, _ = _verify_text(text, filing_form="8-K")

    assert status == STATUS_VERIFIED_MEDIUM
    assert confidence == CONFIDENCE_MEDIUM
    assert family == "ITEM_401_AUDITOR_CHANGE"
    assert outcome_type == "AUDITOR_CHANGE_DISCLOSURE"
    assert signals["has_item_401"] is True


def test_verify_text_restatement_phrase_only_is_possible() -> None:
    text = "The company identified a restatement related to prior periods."
    status, confidence, family, outcome_type, signals, _ = _verify_text(text, filing_form="8-K")

    assert status == STATUS_POSSIBLE
    assert confidence == CONFIDENCE_LOW
    assert family == "AMENDMENT_RESTATEMENT"
    assert outcome_type == "RESTATEMENT_DISCLOSURE"
    assert signals["has_restatement_phrase"] is True


def test_confidence_threshold_comparison() -> None:
    assert _confidence_meets_threshold(CONFIDENCE_HIGH, "HIGH") is True
    assert _confidence_meets_threshold(CONFIDENCE_MEDIUM, "HIGH") is False
    assert _confidence_meets_threshold(CONFIDENCE_MEDIUM, "MEDIUM") is True
    assert _confidence_meets_threshold(CONFIDENCE_LOW, "MEDIUM") is False


class _FakeResponse:
    def __init__(self, url: str, status_code: int, text: str, content_type: str = "text/html") -> None:
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} for {self.url}")


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    def get(self, url: str, timeout: int):  # noqa: ARG002
        import requests

        response = self._responses.get(url)
        if response is None:
            raise requests.HTTPError(f"404 for {url}")
        return response


def test_build_url_candidates_prefers_ixviewer_doc_and_fallbacks() -> None:
    urls = _build_url_candidates(
        cik=320193,
        accession_nodash="000114036126006577",
        primary_document="ef20065513_8k.htm",
        existing_url="https://www.sec.gov/ixviewer/ix.html?doc=/Archives/edgar/data/320193/000114036126006577/ef20065513_8k.htm",
    )

    assert urls[0] == "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/ef20065513_8k.htm"
    assert "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/000114036126006577.txt" in urls
    assert "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/index.json" in urls


def test_fetch_filing_text_uses_index_json_document_fallback() -> None:
    base = "https://www.sec.gov/Archives/edgar/data/320193/000114036126006577/"
    txt_url = f"{base}000114036126006577.txt"
    index_json_url = f"{base}index.json"
    filing_doc_url = f"{base}ef20065513_8k.htm"

    session = _FakeSession(
        {
            txt_url: _FakeResponse(url=txt_url, status_code=404, text="not found"),
            index_json_url: _FakeResponse(
                url=index_json_url,
                status_code=200,
                text='{"directory":{"item":[{"name":"ef20065513_8k.htm"},{"name":"xbrl.xml"}]}}',
                content_type="application/json",
            ),
            filing_doc_url: _FakeResponse(
                url=filing_doc_url,
                status_code=200,
                text="<html><body>Item 4.02 Non-reliance on previously issued financial statements.</body></html>",
            ),
        }
    )

    cleaned, resolved_url = _fetch_filing_text(
        session=session,  # type: ignore[arg-type]
        url_candidates=[txt_url, index_json_url],
        timeout_seconds=5,
    )

    assert "item 4.02" in cleaned
    assert resolved_url == filing_doc_url
