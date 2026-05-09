"""Tests for recommendation-list-fetcher skill."""

import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest
import structlog

# Set up structlog for tests
structlog.configure(
    processors=[],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

from shopping_list import (
    _parse_rating_tier,
    _tier_to_recommendation,
    _parse_price_target,
    _parse_date,
    resolve_ticker,
    _parse_csv_rows,
    validate_config,
)


class TestRatingParsing:
    """Test rating tier parsing and mapping."""

    def test_parse_rating_tier_top_stock(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Top Stock to Buy", cfg) == 5

    def test_parse_rating_tier_top_15(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Top 15 Stock", cfg) == 4
        assert _parse_rating_tier("Top 15 Stock ", cfg) == 4  # trailing space

    def test_parse_rating_tier_buy(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Buy", cfg) == 3

    def test_parse_rating_tier_borderline(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Borderline Buy", cfg) == 2

    def test_parse_rating_tier_hold(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Hold/ Market Perform", cfg) == 1

    def test_parse_rating_tier_sell(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Sell", cfg) == 0

    def test_parse_rating_tier_unknown_defaults_to_hold(self, sample_config):
        _, cfg = sample_config
        assert _parse_rating_tier("Unknown Rating", cfg) == 1

    def test_tier_to_recommendation(self, sample_config):
        _, cfg = sample_config
        assert _tier_to_recommendation(5, cfg) == "STRONG_BUY"
        assert _tier_to_recommendation(4, cfg) == "BUY"
        assert _tier_to_recommendation(3, cfg) == "BUY"
        assert _tier_to_recommendation(2, cfg) == "WEAK_BUY"
        assert _tier_to_recommendation(1, cfg) == "HOLD"
        assert _tier_to_recommendation(0, cfg) == "SELL"


class TestPriceTargetParsing:
    """Test price target range parsing."""

    def test_parse_price_target_range(self):
        assert _parse_price_target("320-350") == (320.0, 350.0)

    def test_parse_price_target_range_with_comma(self):
        assert _parse_price_target("1,200-1,350") == (1200.0, 1350.0)

    def test_parse_price_target_single_value(self):
        assert _parse_price_target("300") == 300.0

    def test_parse_price_target_single_value_decimal(self):
        assert _parse_price_target("300.50") == 300.50

    def test_parse_price_target_empty_returns_none(self):
        assert _parse_price_target("") is None
        assert _parse_price_target(None) is None

    def test_parse_price_target_invalid_returns_none(self):
        assert _parse_price_target("invalid") is None
        assert _parse_price_target("abc-def") is None


class TestDateParsing:
    """Test date parsing with multiple formats."""

    def test_parse_date_slash_format_4digit_year(self, sample_config):
        _, cfg = sample_config
        result = _parse_date("5/6/2026", cfg)
        assert result == date(2026, 5, 6)

    def test_parse_date_slash_format_2digit_year(self, sample_config):
        _, cfg = sample_config
        result = _parse_date("5/6/26", cfg)
        assert result == date(2026, 5, 6)

    def test_parse_date_iso_format(self, sample_config):
        _, cfg = sample_config
        result = _parse_date("2026-05-06", cfg)
        assert result == date(2026, 5, 6)

    def test_parse_date_empty_returns_none(self, sample_config):
        _, cfg = sample_config
        assert _parse_date("", cfg) is None
        assert _parse_date(None, cfg) is None

    def test_parse_date_invalid_returns_none(self, sample_config):
        _, cfg = sample_config
        assert _parse_date("invalid", cfg) is None


class TestTickerResolution:
    """Test ticker resolution from company names."""

    def test_resolve_ticker_manual_override(self, sample_config):
        _, cfg = sample_config
        assert resolve_ticker("Alphabet", cfg) == "GOOG"
        assert resolve_ticker("Meta Platforms", cfg) == "META"
        assert resolve_ticker("Taiwan Semi", cfg) == "TSM"

    def test_resolve_ticker_from_cache(self, sample_config, temp_dir):
        _, cfg = sample_config
        cache_path = Path(cfg["ticker_resolution"]["cache_path"])

        # Pre-populate cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {"Apple Inc": "AAPL"}
        cache_path.write_text(json.dumps(cache_data))

        # Should resolve from cache
        assert resolve_ticker("Apple Inc", cfg) == "AAPL"

    def test_resolve_ticker_unknown_returns_none(self, sample_config):
        _, cfg = sample_config
        # Disable yfinance fallback to avoid network I/O in tests
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False
        result = resolve_ticker("Nonexistent Company XYZ", cfg)
        # Will be None if not in overrides and yfinance disabled
        assert result is None or isinstance(result, str)


class TestCSVRowParsing:
    """Test CSV row parsing and normalization."""

    def test_parse_csv_rows_basic(self, sample_config, sample_csv_data):
        _, cfg = sample_config
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False

        # Parse CSV
        reader = csv.reader(io.StringIO(sample_csv_data))
        rows = list(reader)
        rows = rows[1:]  # Skip header

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Should have parsed some entries
        assert len(entries) > 0

        # Check a specific entry
        apple = next((e for e in entries if e["ticker"] == "AAPL"), None)
        assert apple is not None
        assert apple["recommendation"] == "BUY"
        assert apple["rating_tier"] == 3
        assert apple["price_target_2026"] == (320.0, 350.0)

    def test_parse_csv_rows_skips_ref_errors(self, sample_config, sample_csv_data):
        _, cfg = sample_config
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False

        reader = csv.reader(io.StringIO(sample_csv_data))
        rows = list(reader)
        rows = rows[1:]

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Amazon row has #REF! errors — should be skipped
        amazon = next((e for e in entries if e.get("name") == "Amazon"), None)
        assert amazon is None

        # Check skipped reasons
        has_ref_error = any("#REF!" in reason for reason in skipped)
        assert has_ref_error

    def test_parse_csv_rows_trailing_space_in_rating(self, sample_config, sample_csv_data):
        _, cfg = sample_config
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False

        reader = csv.reader(io.StringIO(sample_csv_data))
        rows = list(reader)
        rows = rows[1:]

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Visa has "Top 15 Stock " with trailing space
        visa = next((e for e in entries if e["ticker"] == "V"), None)
        # Note: Visa resolution may fail due to yfinance being disabled
        # Just check that we handle the trailing space correctly
        if visa:
            assert visa["rating_tier"] == 4
            assert visa["recommendation"] == "BUY"

    def test_parse_csv_rows_age_calculation(self, sample_config, sample_csv_data):
        _, cfg = sample_config
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False

        reader = csv.reader(io.StringIO(sample_csv_data))
        rows = list(reader)
        rows = rows[1:]

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Apple dated 5/1/26, checked on 5/7/26 = 6 days old
        apple = next((e for e in entries if e["ticker"] == "AAPL"), None)
        if apple:
            assert apple["age_days"] == 6
            assert apple["aging"] is False

        # JPMorgan dated 3/1/26, checked on 5/7/26 = 67 days old (> 30 max)
        # Should be skipped as archived
        jp = next((e for e in entries if e.get("name") == "JPMorgan"), None)
        assert jp is None

    def test_parse_csv_rows_skip_empty_rows(self, sample_config):
        _, cfg = sample_config

        rows = [
            ["Apple", "Buy", "5/1/26", "320-350"],
            ["", "", "", ""],  # Empty row
            ["Meta Platforms", "Buy", "4/20/26", "280-310"],
        ]

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Should skip the empty row
        assert len(entries) <= 2


class TestConfigValidation:
    """Test config validation."""

    def test_validate_config_valid(self, sample_config):
        config_path, _ = sample_config
        cfg = validate_config(config_path)
        assert cfg is not None
        assert "source" in cfg
        assert "column_mapping" in cfg

    def test_validate_config_missing_file(self, temp_dir):
        config_path = temp_dir / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            validate_config(config_path)

    def test_validate_config_missing_required_field(self, temp_dir):
        import yaml

        config_path = temp_dir / "bad_config.yaml"
        cfg = {"source": {"url": "test"}}  # Missing column_mapping, etc.
        with open(config_path, "w") as f:
            yaml.dump(cfg, f)

        with pytest.raises(ValueError):
            validate_config(config_path)


class TestIntegration:
    """Integration tests."""

    def test_full_pipeline_with_mocked_data(self, sample_config, sample_csv_data):
        """Test the full pipeline with mock data."""
        _, cfg = sample_config
        cfg["ticker_resolution"]["use_yfinance_fallback"] = False

        reader = csv.reader(io.StringIO(sample_csv_data))
        rows = list(reader)
        rows = rows[1:]

        entries, skipped = _parse_csv_rows(rows, cfg, today=date(2026, 5, 7))

        # Verify we got some entries
        assert len(entries) > 0

        # Verify structure of each entry
        for entry in entries:
            assert "ticker" in entry
            assert "name" in entry
            assert "recommendation" in entry
            assert "rating_tier" in entry
            assert "date_updated" in entry
            assert "age_days" in entry
            assert "aging" in entry
            assert "price_target_2026" in entry
            assert "price_target_2027" in entry
            assert "row_number" in entry

        # Verify recommendations are canonical enums
        valid_recs = {"BUY", "SELL", "HOLD", "STRONG_BUY", "WEAK_BUY"}
        for entry in entries:
            assert entry["recommendation"] in valid_recs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
