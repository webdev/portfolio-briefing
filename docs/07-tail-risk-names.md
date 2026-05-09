# Tail-Risk Name List

**Status:** Draft v0.1, sourced from wheelhouz src/risk/tail_risk.py
**Date:** 2026-05-07

Names on this list get conservative treatment in the wheel-roll-advisor skill: no new short-put recommendations, ⚠ TAIL RISK warning on existing positions, DEFENSIVE_ROLL trigger overridden to CLOSE NOW rather than rolling down-and-out.

---

## Chinese ADRs

Regulatory, delisting (HFCAA), PCAOB audit, capital controls. Headlines move these 10–25% overnight with no warning.

```
BIDU, BABA, JD, NIO, PDD, NTES, TME, BILI,
LI, XPEV, VIPS, IQ, TCOM, TAL, EDU, YUMC,
ZH, FUTU, TIGR
```

**Rationale:** Chinese ADRs trade on US exchanges but face geopolitical and regulatory risk orthogonal to market moves. A trade war escalation, delisting announcement, or audit dispute can gap the stock 15–25% in either direction, regardless of SPY price. Defensive rolls (buying back at a lower strike, selling a later-dated lower strike) provide **no protection** — the new strike is often still blown through in a gap move.

**Recommendation:** Do NOT roll down-and-out. Take profits at >30% capture instead of rolling. On existing underwater positions, override DEFENSIVE_ROLL to CLOSE NOW.

---

## Binary Biotechs

Single-product companies. FDA verdicts, trial readouts, or regulatory actions can gap 30–60% in either direction.

```
NVAX, OCGN, INO, VXRT, BNGO, SAVA, AXSM
```

**Rationale:** Pre-revenue or single-indication biotechs have binary catalysts. An FDA Complete Response Letter or failed Phase 3 readout can crater a stock 50%+ in a day. Wheel mechanics assume drift, rolling through known binary events is a recipe for catastrophic loss.

**Recommendation:** Avoid short puts unless the catalyst date (FDA decision, data readout) is well outside the option expiration. Never roll through a known catalyst date. On existing positions with an approaching catalyst, close immediately.

---

## Crypto-Proxies

Bitcoin moves, SEC enforcement, exchange counterparty events. 20%+ gaps independent of equity market direction.

```
MARA, RIOT, MSTR, HOOD, BITO
```

**Rationale:** These names trade as leveraged proxies for Bitcoin. A Bitcoin halving, SEC action against exchanges, or counterparty crisis (e.g., FTX-style blow-up) can move the stock 20%+ independent of tech sector performance. Correlation to SPY drops to near zero during crypto crises.

**Recommendation:** Size 50% of normal; close at >30% capture rather than roll. On existing positions, tighten stops and close at any significant profit.

---

## High-Borrow / Meme Stocks

Gamma squeezes can blow out short puts in days. Borrow rates signal market fear.

```
GME, AMC, BBBY, BBIG
```

**Rationale:** High short interest creates gamma squeezes when price moves into option expiration. A 1–2% intraday rally can catalyze a 15–20% gap up when gamma unwinds shorts. Short puts sold near strikes can be blown through in hours, forcing assignment at catastrophic loss multiples.

**Recommendation:** Avoid short puts entirely. If existing, take any profit >20% and walk. Never defend these with rolls — the gamma and borrow cost make rolls mathematically negative. On squeeze setups, monitor pre-market and close immediately if volume/price action turns aggressive.

---

## Rationale Summary

The wheel strategy works because it assumes stocks drift, theta decays premium, and rolling down-and-out gives you time and lower strike cost to defend capital. This breaks spectacularly on tail-risk names where:

1. **Discrete events create gaps** that don't respect strike levels
2. **Rolling doesn't help** — the new strike is often gapped through in the same event
3. **Capital preservation trumps premium** — a 25% headline gap is a 15–20% realized loss even on a "defensively" rolled position

These names require active binary thinking, not passive premium collection.

---

## Maintenance

This list is **curated tight**. Adding a name commits the system to more conservative treatment:
- No new short-put recommendations on that name (or downgrade to LOW conviction only)
- Every WATCH line surfaces a ⚠ TAIL RISK warning
- DEFENSIVE_ROLL recommendation overrides to CLOSE NOW
- Take-profit thresholds may shift lower (aggressive closes, no rolling)

**Review quarterly** — if a company matures (pre-revenue biotech gets approved drug), consider removal. If a sector's regulatory environment shifts (crypto sector regulation), consider additions.

**Adding to the list:** Edit `src/risk/tail_risk.py`, add to the appropriate frozenset (`_CHINA_ADRS`, `_BINARY_BIOTECH`, `_CRYPTO_PROXY`, `_MEME_HIGH_BORROW`), update this document, and run the test suite to verify existing positions are flagged correctly.

---

## Sources

All names extracted from wheelhouz `src/risk/tail_risk.py` (lines 29–51) as of 2026-05-07.
