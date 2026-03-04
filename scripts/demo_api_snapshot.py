#!/usr/bin/env python3
"""Collect a concise API snapshot for demo rehearsals."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quick API snapshot for /risk endpoints.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("DEMO_URL", "http://127.0.0.1:8000"),
        help="Base API URL (for example http://127.0.0.1:8000).",
    )
    parser.add_argument("--limit", type=int, default=10, help="Top list limit.")
    parser.add_argument(
        "--cik",
        type=int,
        default=None,
        help="Optional explicit CIK; defaults to top-ranked CIK from /risk/top.",
    )
    return parser.parse_args()


def _get_json(url: str, params: dict[str, object] | None = None) -> dict:
    full_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(full_url, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {full_url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {full_url}: {exc}") from exc


def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/")

    top = _get_json(f"{base}/risk/top", params={"limit": args.limit})
    items = top.get("items", [])
    if not items:
        print(
            json.dumps(
                {"ok": False, "error": "/risk/top returned no items"},
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    chosen_cik = args.cik or int(items[0]["cik"])
    history = _get_json(f"{base}/risk/{chosen_cik}/history", params={"limit": 12})
    explain = _get_json(f"{base}/risk/{chosen_cik}/explain")

    output = {
        "ok": True,
        "base_url": base,
        "top": {
            "as_of_date": top.get("as_of_date"),
            "model_version": top.get("model_version"),
            "total": top.get("total"),
            "sample": [
                {
                    "cik": item.get("cik"),
                    "ticker": item.get("company_ticker"),
                    "risk_score": item.get("risk_score"),
                    "risk_rank": item.get("risk_rank"),
                }
                for item in items[: min(5, len(items))]
            ],
        },
        "issuer": {
            "cik": chosen_cik,
            "history_points": history.get("total"),
            "latest_history_item": (history.get("items") or [{}])[0],
            "explain_summary": {
                "as_of_date": explain.get("score", {}).get("as_of_date"),
                "risk_score": explain.get("score", {}).get("risk_score"),
                "risk_rank": explain.get("score", {}).get("risk_rank"),
                "model_version": explain.get("score", {}).get("model_version"),
                "reason_summary": (
                    explain.get("score", {})
                    .get("evidence", {})
                    .get("reason_summary")
                ),
            },
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
