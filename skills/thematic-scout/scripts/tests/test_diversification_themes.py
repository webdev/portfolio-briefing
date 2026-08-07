"""Tests pinning the Diversification themes added 2026-08-07.

George's book is ~90% tech-correlated and the scout universe was AI-only —
every daily candidate was tech before he saw it. Five non-tech themes
(Healthcare & Pharma, Energy, Financials, Defense & Aerospace, Consumer
Staples & Value) were added so the candidate machine sources diversification
daily. These tests pin:

  (a) the new themes load and validate against the theme_universes.yaml
      schema (name / group / anchors / etfs / notes),
  (b) every new theme's ETFs are in the intrinsic-value default ETF set so
      they render "FV: n/a — basket (ETF)", never a fabricated DCF,
  (c) anchor count is <= 7 per new theme (bounds daily scout runtime/API
      cost: the scout researches every anchor every cycle).
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scout  # noqa: E402

_RULES_PATH = Path(__file__).resolve().parents[2] / "references" / "theme_universes.yaml"

NEW_THEMES = {
    "healthcare_pharma",
    "energy",
    "financials",
    "defense_aerospace",
    "consumer_staples_value",
}

# ETFs are real, long-established sector SPDR / iShares / Invesco / VanEck
# funds — verified 2026-08-07. Never add an ETF you are not certain exists.
EXPECTED_ETFS = {
    "healthcare_pharma": {"XLV"},
    "energy": {"XLE", "OIH"},
    "financials": {"XLF"},
    "defense_aerospace": {"ITA", "PPA"},
    "consumer_staples_value": {"XLP"},
}

# Names where a regulatory/FDA/exchange headline can gap the stock 15-25%
# through any strike — the tail-risk rule excludes them from wheel themes.
BINARY_BIOTECH_EXAMPLES = {"SRPT", "AXSM", "SAVA", "BIIB", "MRNA", "NVAX", "IOVA"}


def _load_rules() -> dict:
    return yaml.safe_load(_RULES_PATH.read_text())


def test_new_themes_present_and_schema_valid():
    """(a) All five diversification themes load with the standard schema."""
    themes = _load_rules()["themes"]
    for key in NEW_THEMES:
        assert key in themes, f"theme {key} missing from theme_universes.yaml"
        t = themes[key]
        assert isinstance(t.get("name"), str) and t["name"], key
        assert t.get("group") == "Diversification", key
        anchors = t.get("anchors")
        assert isinstance(anchors, list) and anchors, f"{key} has no anchors"
        for a in anchors:
            assert isinstance(a, str) and a.strip(), f"{key} bad anchor {a!r}"
            assert a == a.upper(), f"{key} anchor {a!r} not uppercase"
        assert isinstance(t.get("etfs"), list), key
        assert isinstance(t.get("notes"), str) and t["notes"], key


def test_new_theme_etfs_match_verified_list():
    """Only the verified sector funds — never an invented ETF ticker."""
    themes = _load_rules()["themes"]
    for key, expected in EXPECTED_ETFS.items():
        assert set(themes[key]["etfs"]) == expected, key


def test_new_theme_etfs_in_intrinsic_value_etf_set():
    """(b) Every new theme's ETFs render 'FV: n/a — basket (ETF)' — they
    must be in the intrinsic-value default ETF set (no fabricated DCF)."""
    iv_mod = scout._load_intrinsic_module()
    assert iv_mod is not None, "intrinsic_value module failed to load"
    etf_set = iv_mod.default_etf_set(None)
    for key, expected in EXPECTED_ETFS.items():
        for etf in expected:
            assert etf in etf_set, (
                f"{etf} ({key}) missing from intrinsic_value DEFAULT_ETFS — "
                f"would risk a fabricated DCF on a basket"
            )
            assert iv_mod.is_etf(etf, etf_set), etf


def test_new_theme_anchor_count_capped_at_seven():
    """(c) <= 7 anchors per new theme — the scout researches every anchor
    daily; the cap bounds runtime and yfinance/E*TRADE API cost."""
    themes = _load_rules()["themes"]
    for key in NEW_THEMES:
        n = len(themes[key]["anchors"])
        assert n <= 7, f"{key} has {n} anchors (cap is 7)"


def test_no_single_binary_biotechs_in_healthcare():
    """Tail-risk rule: single-binary biotechs are excluded from the
    healthcare theme — wheel mechanics break on FDA-headline gap risk."""
    themes = _load_rules()["themes"]
    anchors = set(themes["healthcare_pharma"]["anchors"])
    assert not (anchors & BINARY_BIOTECH_EXAMPLES), (
        f"binary biotech leaked into healthcare_pharma: "
        f"{anchors & BINARY_BIOTECH_EXAMPLES}"
    )


def test_full_yaml_still_parses_and_verdict_block_intact():
    """The edit didn't corrupt the file: verdict thresholds still present
    and every pre-existing theme still loads."""
    rules = _load_rules()
    v = rules["verdict"]
    assert v["rsi_oversold"] == 35
    assert v["iv_rank_elevated"] == 50
    themes = rules["themes"]
    for legacy in ("semis", "power", "cybersecurity", "social_ad_tech_ai"):
        assert legacy in themes
