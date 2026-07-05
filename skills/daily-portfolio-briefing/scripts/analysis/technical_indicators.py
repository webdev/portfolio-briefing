"""Deep technical read — Bollinger, MACD, ATR, SMA slopes, 52-week range,
ATH drawdown, multi-horizon returns, volume ratio, and short/long-term
verdict labels for every ticker in the briefing.

This is the engine behind the "🎯 Technical Read" section. The indicator math
is lifted verbatim from the standalone infographic batch script
(``/tmp/tech_batch.py``) so the briefing's numbers match the infographic the
user already trusts. Verdicts are PURE functions over the computed snapshot —
no I/O, no side effects — so every band boundary is pinned by a test.

Data contract (hard rule #7 / #10): ``compute_technicals`` operates only on
the OHLC DataFrame handed to it by the caller (the same 730d yfinance pull
that feeds RSI/S-R in ``snapshot_inputs``). It NEVER fetches. When history is
insufficient it returns ``None`` — the renderer then surfaces "chart data
unavailable, verify manually" rather than fabricated indicators (hard rule #19).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

# Minimum bars for a full snapshot: 200-SMA + slope lookback + a little slack.
# Below this we fail closed (None) rather than emit half-fabricated verdicts.
MIN_BARS = 210

# Verdict label vocabularies — pinned so renderers/tests can be precise.
ST_STRETCHED = "stretched-pullback-risk"
ST_OVERSOLD_BOUNCE = "oversold-bounce"
ST_BULL = "bull-momentum"
ST_BEAR = "bear-momentum"
ST_STABILIZING = "stabilizing"
ST_NEUTRAL = "neutral"

LT_SECULAR = "secular-uptrend"
LT_UPTREND = "uptrend"
LT_BROKEN = "broken"
LT_DOWNTREND = "downtrend"
LT_RECOVERY = "recovery"
LT_WEAKENING = "weakening"
LT_SIDEWAYS = "sideways"

SHORT_TERM_LABELS = {
    ST_STRETCHED, ST_OVERSOLD_BOUNCE, ST_BULL, ST_BEAR, ST_STABILIZING, ST_NEUTRAL,
}
LONG_TERM_LABELS = {
    LT_SECULAR, LT_UPTREND, LT_BROKEN, LT_DOWNTREND, LT_RECOVERY, LT_WEAKENING,
    LT_SIDEWAYS,
}


def _f(x) -> float:
    """Coerce single-element Series / numpy scalar to a plain float."""
    if hasattr(x, "iloc"):
        try:
            x = x.iloc[0]
        except Exception:
            pass
    return float(x)


# ─────────────────────────────────────────────────────────────────────────────
# Indicator math — copied verbatim from the infographic batch script
# ─────────────────────────────────────────────────────────────────────────────

def wilder_rsi(series: pd.Series, period: int = 14) -> float | None:
    """Wilder's RSI — same smoothing as snapshot_inputs so the briefing shows
    ONE RSI number per ticker, not two subtly different ones."""
    if len(series) < period * 2:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    last_gain = _f(avg_gain.iloc[-1])
    last_loss = _f(avg_loss.iloc[-1])
    if last_loss > 0:
        return round(100.0 - (100.0 / (1.0 + last_gain / last_loss)), 1)
    if last_gain > 0:
        return 100.0
    return 50.0


def iv_rank_252(closes: pd.Series) -> float | None:
    """IV rank proxy — percentile of the current 20d realized vol within the
    trailing 252 observations (~1 trading year).

    Canonical implementation (Task #12 consolidation): snapshot_inputs,
    the thematic scout, and the broad-universe screener all route here so
    every surface shows ONE IV rank per ticker. Math:

      1. daily pct-change returns from ``closes``
      2. 20-day rolling std, annualized (× √252) → realized-vol series
      3. ``.tail(252)`` — the percentile window stays at the documented
         252 obs even when the caller hands in longer history (730d pull)
      4. rank = % of window obs at/below the latest vol, rounded to 0.1

    Fail-closed: returns ``None`` on insufficient data (< 21 returns), an
    empty vol window, or a NaN current vol — never a fabricated number
    (hard rule #19).
    """
    rets = closes.pct_change().dropna()
    if len(rets) < 21:
        return None
    rolling_vol = (rets.rolling(20).std() * math.sqrt(252)).dropna().tail(252)
    if rolling_vol.empty:
        return None
    cur = float(rolling_vol.iloc[-1])
    if math.isnan(cur):
        return None
    return round(float((rolling_vol <= cur).sum()) / len(rolling_vol) * 100.0, 1)


def bollinger(series: pd.Series, period: int = 20, std: int = 2) -> dict:
    """Bollinger Bands (20, 2). ``position`` is 0.0 at the lower band, 1.0 at
    the upper — can exceed [0, 1] when price pierces a band."""
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    upper = ma + std * sd
    lower = ma - std * sd
    spot = _f(series.iloc[-1])
    up = _f(upper.iloc[-1])
    dn = _f(lower.iloc[-1])
    mid = _f(ma.iloc[-1])
    bb_pct = (spot - dn) / (up - dn) if up != dn else 0.5
    width_pct = (up - dn) / mid * 100 if mid else 0
    return {"upper": up, "mid": mid, "lower": dn, "position": bb_pct,
            "width_pct": width_pct}


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD(12, 26, 9). ``hist_5d_ago`` gives the histogram trend read."""
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_f - ema_s
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line
    return {
        "macd": _f(macd_line.iloc[-1]),
        "signal": _f(sig_line.iloc[-1]),
        "hist": _f(hist.iloc[-1]),
        "hist_5d_ago": _f(hist.iloc[-6]) if len(hist) > 5 else 0,
    }


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR(14) via true range rolling mean."""
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return _f(tr.rolling(period).mean().iloc[-1])


def sma_slope(series: pd.Series, period: int) -> float:
    """% change of the ``period``-SMA over the last 20 trading days."""
    ma = series.rolling(period).mean()
    if len(ma.dropna()) < 21:
        return 0
    now = _f(ma.iloc[-1])
    then = _f(ma.iloc[-21])
    return (now - then) / then * 100 if then else 0


def swing_levels(df: pd.DataFrame, lookback: int = 180, band_pct: float = 0.02) -> dict:
    """Swing highs/lows over ``lookback`` bars, clustered within ``band_pct``.

    Kept for API parity with the infographic script. The briefing pipeline's
    canonical S/R comes from ``analysis.support_resistance.compute_sr`` (which
    adds pivots, confluence, and recency scoring) — this simpler variant is
    only used when a caller has no SR result to hand.
    """
    highs, lows = [], []
    d = df.tail(lookback).reset_index(drop=True)
    hi = d["High"].values
    lo = d["Low"].values
    for i in range(2, len(d) - 2):
        if hi[i] > hi[i - 1] and hi[i] > hi[i - 2] and hi[i] > hi[i + 1] and hi[i] > hi[i + 2]:
            highs.append(float(hi[i]))
        if lo[i] < lo[i - 1] and lo[i] < lo[i - 2] and lo[i] < lo[i + 1] and lo[i] < lo[i + 2]:
            lows.append(float(lo[i]))

    def cluster(levels, band):
        levels = sorted(levels)
        cs = []
        for lv in levels:
            placed = False
            for c in cs:
                if abs(c["mid"] - lv) / c["mid"] < band:
                    c["members"].append(lv)
                    c["mid"] = sum(c["members"]) / len(c["members"])
                    placed = True
                    break
            if not placed:
                cs.append({"mid": lv, "members": [lv]})
        return [{"level": round(c["mid"], 2), "touches": len(c["members"])}
                for c in cs if len(c["members"]) >= 2]

    return {"supports": cluster(lows, band_pct), "resistances": cluster(highs, band_pct)}


def ath_metrics(series: pd.Series) -> dict:
    """Drawdown vs the highest close in the provided history plus the 52-week
    range position. Note: 'ATH' here means the high of the fetched window
    (~2 years with the 730d pull), not the all-time listing high."""
    ath = _f(series.max())
    dd = (_f(series.iloc[-1]) - ath) / ath * 100
    yr = series.tail(252)
    hi = _f(yr.max())
    lo = _f(yr.min())
    spot = _f(series.iloc[-1])
    yr_pos = (spot - lo) / (hi - lo) * 100 if hi > lo else 50
    return {"ath": ath, "ath_dd_pct": dd, "yr_hi": hi, "yr_lo": lo,
            "yr_position_pct": yr_pos}


# ─────────────────────────────────────────────────────────────────────────────
# Verdicts — pure functions over snapshot fields (every boundary test-pinned)
# ─────────────────────────────────────────────────────────────────────────────

def short_term_verdict(
    *,
    rsi: float | None,
    bb_position_pct: float,
    macd_hist: float,
    macd_hist_5d_ago: float,
    vs_sma20_pct: float,
    ret_1w_pct: float,
    ret_1m_pct: float,
) -> str:
    """1-4 week read. First matching rule wins (order is the contract):

    1. stretched-pullback-risk — RSI ≥ 70, or RSI ≥ 60 with price pinned to
       the upper Bollinger band (position ≥ 90%).
    2. oversold-bounce — RSI ≤ 32 with the MACD histogram turning up, or price
       pierced the lower band (≤ 5%) with RSI ≤ 35.
    3. bear-momentum — negative AND falling MACD histogram.
    4. bull-momentum — positive AND rising histogram with price above 20-SMA.
    5. stabilizing — down month, flat-to-up week, histogram turning up.
    6. neutral — everything else.
    """
    rsi_v = rsi if rsi is not None else 50.0
    hist_rising = macd_hist > macd_hist_5d_ago

    if rsi_v >= 70 or (rsi_v >= 60 and bb_position_pct >= 90):
        return ST_STRETCHED
    if (rsi_v <= 32 and hist_rising) or (bb_position_pct <= 5 and rsi_v <= 35):
        return ST_OVERSOLD_BOUNCE
    if macd_hist < 0 and not hist_rising:
        return ST_BEAR
    if macd_hist > 0 and hist_rising and vs_sma20_pct > 0:
        return ST_BULL
    if ret_1m_pct < 0 and ret_1w_pct >= 0 and hist_rising:
        return ST_STABILIZING
    return ST_NEUTRAL


def long_term_verdict(
    *,
    cross: str,
    vs_sma200_pct: float,
    sma_200_slope_pct: float,
    sma_50_slope_pct: float,
    ath_dd_pct: float,
    yr_position_pct: float,
) -> str:
    """3-12 month read. First matching rule wins (order is the contract):

    1. broken — ≥40% off the high, below a falling 200-SMA.
    2. downtrend — death cross, below a flat/falling 200-SMA.
    3. recovery — reclaimed the 200-SMA while the cross is still death, or
       deep drawdown (≥25%) but back above 200 with a rising 50-SMA.
    4. secular-uptrend — golden cross, above 200, 200-SMA rising ≥1%/20d,
       upper 40% of the 52-week range.
    5. uptrend — golden cross, above the 200-SMA.
    6. weakening — golden cross but price slipped below the 200-SMA.
    7. sideways — everything else.
    """
    if ath_dd_pct <= -40 and vs_sma200_pct < 0 and sma_200_slope_pct < 0:
        return LT_BROKEN
    if cross == "death" and vs_sma200_pct < 0 and sma_200_slope_pct <= 0:
        return LT_DOWNTREND
    if (cross == "death" and vs_sma200_pct > 0) or (
        ath_dd_pct <= -25 and vs_sma200_pct > 0 and sma_50_slope_pct > 0
    ):
        return LT_RECOVERY
    if (cross == "golden" and vs_sma200_pct > 0
            and sma_200_slope_pct >= 1.0 and yr_position_pct >= 60):
        return LT_SECULAR
    if cross == "golden" and vs_sma200_pct > 0:
        return LT_UPTREND
    if cross == "golden" and vs_sma200_pct < 0:
        return LT_WEAKENING
    return LT_SIDEWAYS


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TechnicalSnapshot:
    """One ticker's full deep-technical read for one cycle. Every number is
    computed from the OHLC handed in — nothing defaulted, nothing fabricated."""

    ticker: str
    spot: float
    bars: int
    rsi_14: float | None
    # Bollinger (20, 2)
    bb_upper: float
    bb_mid: float
    bb_lower: float
    bb_position_pct: float      # 0 = lower band, 100 = upper band (can exceed)
    bb_width_pct: float
    # MACD (12, 26, 9)
    macd: float
    macd_signal: float
    macd_hist: float
    macd_hist_5d_ago: float
    # ATR (14)
    atr_14: float
    atr_pct: float
    # Trend structure
    sma_20: float
    sma_50: float
    sma_200: float
    sma_50_slope_pct: float     # % change of the 50-SMA over 20 trading days
    sma_200_slope_pct: float
    vs_sma20_pct: float
    vs_sma50_pct: float
    vs_sma200_pct: float
    cross: str                  # "golden" | "death"
    # Range / drawdown
    ath: float
    ath_dd_pct: float           # negative = below high
    yr_hi: float
    yr_lo: float
    yr_position_pct: float      # 0 = 52w low, 100 = 52w high
    # Returns
    ret_1w_pct: float
    ret_1m_pct: float
    ret_3m_pct: float
    ret_1y_pct: float | None    # None when < 252 bars of history
    # Volume
    vol_ratio_30d: float | None  # last volume / 30d avg; None without Volume col
    # Verdicts
    short_term_verdict: str
    long_term_verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def compute_technicals(
    ticker: str,
    ohlc_df: pd.DataFrame,
    *,
    rsi_14: float | None = None,
) -> TechnicalSnapshot | None:
    """Compute the full deep read for one ticker from its OHLC DataFrame.

    ``rsi_14`` may be passed by the caller (snapshot_inputs already computed
    Wilder's RSI on the same pull) so the briefing carries ONE RSI per ticker;
    when omitted, it is computed here with the identical formula.

    Fail-closed: returns ``None`` when the DataFrame is missing, lacks
    High/Low/Close, or has fewer than ``MIN_BARS`` usable bars.
    """
    if ohlc_df is None or len(ohlc_df) == 0:
        return None
    if not {"High", "Low", "Close"}.issubset(ohlc_df.columns):
        return None

    df = ohlc_df.dropna(subset=["High", "Low", "Close"])
    close = df["Close"]
    if len(close) < MIN_BARS:
        return None

    spot = _f(close.iloc[-1])
    if not math.isfinite(spot) or spot <= 0:
        return None

    sma_20 = _f(close.rolling(20).mean().iloc[-1])
    sma_50 = _f(close.rolling(50).mean().iloc[-1])
    sma_200 = _f(close.rolling(200).mean().iloc[-1])

    bb = bollinger(close)
    mac = macd(close)
    at = atr(df)
    rsi_v = rsi_14 if rsi_14 is not None else wilder_rsi(close)
    ath = ath_metrics(close)

    vs_sma20 = (spot - sma_20) / sma_20 * 100
    vs_sma50 = (spot - sma_50) / sma_50 * 100
    vs_sma200 = (spot - sma_200) / sma_200 * 100
    slope_50 = sma_slope(close, 50)
    slope_200 = sma_slope(close, 200)
    cross = "golden" if sma_50 > sma_200 else "death"

    ret_1w = (spot / _f(close.iloc[-6]) - 1) * 100 if len(close) > 5 else 0.0
    ret_1m = (spot / _f(close.iloc[-22]) - 1) * 100 if len(close) > 21 else 0.0
    ret_3m = (spot / _f(close.iloc[-66]) - 1) * 100 if len(close) > 65 else 0.0
    ret_1y = (spot / _f(close.iloc[-252]) - 1) * 100 if len(close) > 251 else None

    vol_ratio: float | None = None
    if "Volume" in df.columns:
        vols = df["Volume"].dropna()
        if len(vols) >= 30:
            avg30 = _f(vols.tail(30).mean())
            if avg30 > 0:
                vol_ratio = round(_f(vols.iloc[-1]) / avg30, 2)

    bb_position_pct = round(bb["position"] * 100, 1)
    st = short_term_verdict(
        rsi=rsi_v,
        bb_position_pct=bb_position_pct,
        macd_hist=mac["hist"],
        macd_hist_5d_ago=mac["hist_5d_ago"],
        vs_sma20_pct=vs_sma20,
        ret_1w_pct=ret_1w,
        ret_1m_pct=ret_1m,
    )
    lt = long_term_verdict(
        cross=cross,
        vs_sma200_pct=vs_sma200,
        sma_200_slope_pct=slope_200,
        sma_50_slope_pct=slope_50,
        ath_dd_pct=ath["ath_dd_pct"],
        yr_position_pct=ath["yr_position_pct"],
    )

    return TechnicalSnapshot(
        ticker=ticker,
        spot=round(spot, 2),
        bars=len(close),
        rsi_14=rsi_v,
        bb_upper=round(bb["upper"], 2),
        bb_mid=round(bb["mid"], 2),
        bb_lower=round(bb["lower"], 2),
        bb_position_pct=bb_position_pct,
        bb_width_pct=round(bb["width_pct"], 1),
        macd=round(mac["macd"], 3),
        macd_signal=round(mac["signal"], 3),
        macd_hist=round(mac["hist"], 3),
        macd_hist_5d_ago=round(mac["hist_5d_ago"], 3),
        atr_14=round(at, 2),
        atr_pct=round(at / spot * 100, 1),
        sma_20=round(sma_20, 2),
        sma_50=round(sma_50, 2),
        sma_200=round(sma_200, 2),
        sma_50_slope_pct=round(slope_50, 1),
        sma_200_slope_pct=round(slope_200, 1),
        vs_sma20_pct=round(vs_sma20, 1),
        vs_sma50_pct=round(vs_sma50, 1),
        vs_sma200_pct=round(vs_sma200, 1),
        cross=cross,
        ath=round(ath["ath"], 2),
        ath_dd_pct=round(ath["ath_dd_pct"], 1),
        yr_hi=round(ath["yr_hi"], 2),
        yr_lo=round(ath["yr_lo"], 2),
        yr_position_pct=round(ath["yr_position_pct"]),
        ret_1w_pct=round(ret_1w, 1),
        ret_1m_pct=round(ret_1m, 1),
        ret_3m_pct=round(ret_3m, 1),
        ret_1y_pct=round(ret_1y, 1) if ret_1y is not None else None,
        vol_ratio_30d=vol_ratio,
        short_term_verdict=st,
        long_term_verdict=lt,
    )
