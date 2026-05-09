"""Test fixtures for recommendation-list-fetcher tests."""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

# Add scripts directory to path so modules can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Add tests directory to path so helpers can be imported
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def temp_dir():
    """Temporary directory for test artifacts."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config(temp_dir):
    """Sample config for testing."""
    cfg = {
        "source": {
            "url": "https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/gviz/tq?tqx=out:csv",
            "sheet_id": "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M",
            "data_starts_at_row": 2,
        },
        "column_mapping": {
            "name": "A",
            "recommendation": "B",
            "date_updated": "C",
            "price_target_2026": "D",
            "price_target_2026_asof": "E",
            "price_target_2027": "F",
            "price_target_2027_asof": "G",
        },
        "ticker_resolution": {
            "manual_overrides": {
                "Apple": "AAPL",
                "Alphabet": "GOOG",
                "Google": "GOOGL",
                "Meta Platforms": "META",
                "Microsoft": "MSFT",
                "NVIDIA": "NVDA",
                "Taiwan Semi": "TSM",
                "Tesla": "TSLA",
                "Visa": "V",
                "Booking Holdings": "BKNG",
                "Amazon": "AMZN",
                "JPMorgan": "JPM",
            },
            "cache_path": str(temp_dir / "ticker_map.json"),
            "use_yfinance_fallback": True,
        },
        "normalization": {
            "rating_tiers": {
                "Top Stock to Buy": 5,
                "Top 15 Stock": 4,
                "Buy": 3,
                "Borderline Buy": 2,
                "Hold/ Market Perform": 1,
                "Sell": 0,
            },
            "tier_to_recommendation": {
                5: "STRONG_BUY",
                4: "BUY",
                3: "BUY",
                2: "WEAK_BUY",
                1: "HOLD",
                0: "SELL",
            },
            "data_hygiene": {
                "strip_whitespace_all_fields": True,
                "drop_ref_errors": True,
                "accepted_date_formats": [
                    "%m/%d/%Y",
                    "%m/%d/%y",
                    "%Y-%m-%d",
                ],
            },
        },
        "freshness": {
            "max_age_days": 30,
            "warn_age_days": 14,
        },
        "caching": {
            "cache_for_minutes": 60,
            "cache_path": str(temp_dir / "recommendation_list.json"),
        },
    }

    config_path = temp_dir / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    return config_path, cfg


@pytest.fixture
def sample_csv_data():
    """Sample CSV data for testing."""
    return """Name,Rating,Date Updated,2026 Price Target,2026 As-of,2027 Price Target,2027 As-of
Apple,Buy,5/1/26,320-350,3/16/26,,,
Meta Platforms,Top Stock to Buy,4/20/26,280-310,3/16/26,350-400,1/20/26
Alphabet,Borderline Buy,5/2/26,180-200,2/1/26,,
Visa,Top 15 Stock ,4/15/26,300-320,3/1/26,330-360,1/15/26
Tesla,Sell,5/5/26,200-220,2/1/26,,
Booking Holdings,Hold/ Market Perform,4/10/26,,,,
NVIDIA,Buy,5/3/26,1200-1300,3/15/26,,
Microsoft,Buy,4/28/26,420-450,2/1/26,500-550,1/20/26
Google,Buy,4/25/26,,,,,
Taiwan Semi,Buy,4/1/26,150-170,3/1/26,,
Amazon,#REF!,5/1/26,#REF!,#REF!,,
JPMorgan,Hold/ Market Perform,3/1/26,220-250,1/1/26,,
Unknown Company,Buy,4/1/26,100-120,2/1/26,,
"""
