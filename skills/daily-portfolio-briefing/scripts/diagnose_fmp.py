#!/usr/bin/env python3
"""FMP connectivity / plan diagnostic.

The daily briefing's intrinsic-value step came back empty ("0 priced") even
with FMP_API_KEY set, which usually means the DCF / price-target endpoints
aren't on your plan, or FMP moved them. This script probes each candidate
endpoint for one ticker and reports the HTTP status + a short snippet so we can
see exactly which ones your key can reach.

It NEVER prints your API key (it's redacted from any echoed URL/response).

Usage:
  uv run python skills/daily-portfolio-briefing/scripts/diagnose_fmp.py
  uv run python skills/daily-portfolio-briefing/scripts/diagnose_fmp.py --ticker NVDA
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


def _load_env_key() -> str | None:
    """FMP_API_KEY from the environment, or parsed from the repo .env."""
    key = os.getenv("FMP_API_KEY")
    if key:
        return key
    # Walk up to find a .env (repo root).
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        env = parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("FMP_API_KEY=") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***KEY***") if key else text


def _probe(label: str, url: str, key: str, timeout: float = 10.0) -> None:
    shown = _redact(url, key)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fmp-diagnostic"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = "(no body)"
    except Exception as e:
        print(f"\n[{label}] {shown}\n  ERROR: {type(e).__name__}: {e}")
        return

    snippet = _redact(body, key)[:300].replace("\n", " ")
    verdict = "✅ OK" if status == 200 and snippet.strip() not in ("[]", "") else (
        "⚠️ EMPTY" if status == 200 else f"❌ HTTP {status}")
    print(f"\n[{label}] {verdict}")
    print(f"  GET {shown}")
    print(f"  status {status} · body: {snippet}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="AAPL")
    args = ap.parse_args()
    t = args.ticker.upper()

    key = _load_env_key()
    if not key:
        print("❌ FMP_API_KEY not found in env or repo .env.")
        return 1
    print(f"FMP key detected (…{key[-4:]}). Probing endpoints for {t}:")

    base = "https://financialmodelingprep.com"
    probes = [
        ("key sanity / profile (usually free)", f"{base}/api/v3/profile/{t}?apikey={key}"),
        ("legacy v3 DCF", f"{base}/api/v3/discounted-cash-flow/{t}?apikey={key}"),
        ("legacy v4 price-target-summary", f"{base}/api/v4/price-target-summary?symbol={t}&apikey={key}"),
        ("stable DCF", f"{base}/stable/discounted-cash-flow?symbol={t}&apikey={key}"),
        ("stable price-target-summary", f"{base}/stable/price-target-summary?symbol={t}&apikey={key}"),
        ("stable ratios-ttm (free valuation proxy)", f"{base}/stable/ratios-ttm?symbol={t}&apikey={key}"),
    ]
    for label, url in probes:
        _probe(label, url, key)

    print("\nDone. Paste the ✅/⚠️/❌ lines back — none of them contain your key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
