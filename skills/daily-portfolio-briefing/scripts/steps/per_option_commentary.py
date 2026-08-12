"""
Per-option rich commentary generation.

For each option position, detect meaningful scenarios and generate 0-3 short notes.
Patterns: earnings imminent, profit captured, ITM covered calls, etc.
"""

import sys
from datetime import datetime
from pathlib import Path

try:
    from analysis import rsi_discipline
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import rsi_discipline


def _rsi_management_note(rsi: float | None, opt_type: str, qty: float) -> str | None:
    """Disciplined RSI read for an EXISTING short option (never a gate).

    Short PUT: oversold/falling momentum pushes the stock toward the strike
    (assignment risk rising); overbought means the put is safe.
    Short CALL: overbought/extended pushes toward the cap (covered-call
    assignment, which keeps premium + strike gain); oversold means it's safe.
    """
    if rsi is None or qty >= 0:  # only short positions
        return None
    opt_type = (opt_type or "").upper()
    if opt_type == "PUT":
        if rsi <= 30:
            return f"RSI {rsi:.0f} (oversold) — momentum is pressing toward your short-put strike; watch support / assignment risk."
        if rsi >= 70:
            return f"RSI {rsi:.0f} (overbought) — well clear of your short-put strike; theta working in your favour."
    elif opt_type == "CALL":
        if rsi >= 70:
            return f"RSI {rsi:.0f} (overbought) — stock extended toward your call strike; assignment keeps premium + strike gain (roll up to keep shares)."
        if rsi <= 30:
            return f"RSI {rsi:.0f} (oversold) — short call is safe; little near-term cap risk."
    return None


def _get_earnings_days_away(underlying: str, snapshot_data: dict) -> int | None:
    """Extract next earnings date for an underlying from snapshot, return days away."""
    earnings_calendar = snapshot_data.get("earnings_calendar", {})
    if underlying not in earnings_calendar:
        return None
    earnings_date_str = earnings_calendar[underlying]
    try:
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        days_away = (earnings_date - datetime.now().date()).days
        return max(0, days_away)
    except (ValueError, TypeError):
        return None


def generate_commentary(
    option_review: dict,
    snapshot_data: dict,
    equity_reviews: list | None = None,
) -> list[str]:
    """
    Generate 0-3 short commentary lines per option.

    Args:
        option_review: from options_reviews (keys: contract, type, underlying, strike,
                       expiration, dte, entry_price, current_mid, recommendation, etc.)
        snapshot_data: portfolio snapshot with quotes, earnings_calendar, etc.
        equity_reviews: list of equity reviews (for detecting "shares up sharply" patterns)

    Returns:
        list of markdown strings (no bullet points; caller adds them)
    """
    notes = []

    # Extract key fields
    contract = option_review.get("contract", "")
    underlying = contract.split("_")[0] if "_" in contract else ""
    opt_type = option_review.get("type", "").upper()
    strike = option_review.get("strike")
    expiration = option_review.get("expiration")
    dte = option_review.get("days_to_expiry")
    entry_price = option_review.get("entry_price")
    current_mid = option_review.get("current_mid")
    recommendation = option_review.get("recommendation", "")
    qty = option_review.get("qty", -1)  # negative = short

    # Compute capture percentage
    if entry_price and current_mid and entry_price > 0:
        capture_pct = (entry_price - current_mid) / entry_price * 100
    else:
        capture_pct = 0

    # Get earnings info
    earnings_days = _get_earnings_days_away(underlying, snapshot_data) if underlying else None

    # Get underlying price for ITM checks
    quotes = snapshot_data.get("quotes", {})
    underlying_quote = quotes.get(underlying, {})
    underlying_price = underlying_quote.get("last")

    # Get underlying day change
    underlying_day_change = underlying_quote.get("dayChangePct", 0)

    # Get corresponding equity review for checking if shares up despite ITM short call
    equity_review = None
    if equity_reviews:
        for eq in equity_reviews:
            if eq.get("ticker") == underlying:
                equity_review = eq
                break

    # --- PATTERN 1: URGENT - Earnings imminent + short option with BAD capture (<-50%) ---
    if earnings_days is not None and earnings_days <= 30 and capture_pct < -50 and qty < 0:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"🚨 URGENT: Earnings in {earnings_days}d with {capture_pct:.0f}% capture — "
                "close BEFORE report or roll up & out to avoid binary gap risk"
            )

    # --- PATTERN 1b: Earnings imminent + short option with good profit ---
    elif earnings_days is not None and earnings_days <= 30 and capture_pct >= 30:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"Earnings in {earnings_days}d with {capture_pct:.0f}% captured. "
                "Consider closing before report to lock profit, then re-sell after IV crush"
            )

    # --- PATTERN 2: Earnings imminent + short option underwater (but not desperate) ---
    elif earnings_days is not None and earnings_days <= 30 and capture_pct < 0 and capture_pct >= -50:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"Earnings in {earnings_days}d and position is underwater. "
                "Close before report to limit loss"
            )

    # --- PATTERN 2b: General earnings flag (not imminent but worth noting) ---
    elif earnings_days is not None and earnings_days <= 14 and opt_type in ("PUT", "CALL"):
        if "Earnings" not in "\n".join(notes):  # Don't repeat if already mentioned
            notes.append(f"Earnings in {earnings_days}d")

    # --- PATTERN 3: Covered call (short call) + earnings nearby ---
    elif (
        opt_type == "CALL"
        and earnings_days is not None
        and earnings_days <= 30
        and qty < 0  # short call
    ):
        notes.append(
            f"Earnings in {earnings_days}d — covered call, so assignment risk is fine "
            "(you keep premium + strike gain). If you want to keep shares through earnings, roll up and out"
        )

    # --- PATTERN 4: ITM short call but shares up more (shares profited more) ---
    elif (
        opt_type == "CALL"
        and strike is not None
        and underlying_price is not None
        and underlying_price > strike  # ITM
        and capture_pct < 0  # underwater on the short call
        and equity_review is not None
    ):
        eq_pl_pct = equity_review.get("pl_pct", 0) * 100
        if eq_pl_pct > 0:
            notes.append(
                f"Call is ITM (option at {capture_pct:.0f}%) but shares gained more — "
                "combined position is profitable. Let it get called away at "
                f"${strike:g} + keep premium, or roll up and out if you want to keep shares"
            )

    # --- PATTERN 5: High capture + long DTE ---
    elif capture_pct >= 30 and dte and dte > 14:
        notes.append(
            f"+{capture_pct:.0f}% captured with {dte}d left — consider closing winners early"
        )

    # --- PATTERN 6: TV-aligned moves (simple proxy: >2% day move) ---
    if underlying_day_change > 0.02 and opt_type == "CALL" and qty < 0:
        notes.append(
            "Shares up sharply — bearish momentum favors your short call. "
            "Hold unless stock reverses sharply"
        )
    elif underlying_day_change < -0.02 and opt_type == "PUT" and qty < 0:
        notes.append(
            "Shares down sharply — crowd is bearish. Tighten your stop or close if price breaks support"
        )

    return notes


# Watch-panel compact mode (2026-08-06 length diet). HOLD-family advisor
# verdicts — no action recommended — collapse to one summary line; anything
# that recommends an action (roll/close/urgent/demotion note/credit-window
# closing) keeps the full block incl. the ROLL ANALYSIS table. No position
# is ever dropped (a missing position is a data gap — existing rule).
_WATCH_HOLD_FAMILY = {"HOLD", "WAIT", "LET_EXPIRE", ""}


def _watch_needs_full_block(review: dict, commentary: list[str],
                            snapshot_data: dict | None) -> bool:
    """True when the position's advisor/verdict recommends an action —
    those keep the full Watch block (ROLL ANALYSIS table, credit window,
    demotion notes). HOLD-family verdicts with nothing urgent collapse."""
    rec = (review.get("recommendation") or "").upper()
    if rec not in _WATCH_HOLD_FAMILY:
        return True
    # Demotion notes: the Watch panel is their ONLY surface — never collapse.
    if (review.get("_roll_gate_demotion") or review.get("_churn_guard_demotion")
            or review.get("_debit_cap_demotion")
            or review.get("_close_floor_demotion")
            or review.get("_redeploy_hold_demotion")):
        return True
    # Advisor's own roll table recommends an actual roll (not A=HOLD).
    rec_id = review.get("recommended_candidate_id")
    if review.get("roll_candidates") and rec_id and rec_id != "A":
        return True
    # Urgent commentary (earnings + bad capture etc.).
    if any("🚨" in n for n in commentary or []):
        return True
    # Credit window closing / debit-only — "act while a credit remains".
    try:
        _cw = (((snapshot_data or {}).get("_credit_windows") or {})
               .get(review.get("contract")) or {})
        if _cw.get("state") in ("closing", "debit_only"):
            return True
    except Exception:
        pass
    return False


def _truncate_phrase(text: str, limit: int = 90) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def render_watch_with_commentary(
    equity_reviews: list,
    options_reviews: list,
    snapshot_data: dict,
    compact: bool = False,
) -> list[str]:
    """
    Render watch panel with per-option commentary inline.

    Args:
        equity_reviews, options_reviews: from briefing steps
        snapshot_data: portfolio snapshot
        compact: 2026-08-06 length diet — HOLD-family option positions
            collapse to one summary line (position, P&L, capture %, verdict
            + one-phrase why); positions whose advisor recommends an action
            keep the full block. Default False = legacy byte-identical.

    Returns:
        markdown lines ready to concatenate
    """
    lines = ["## Watch / Portfolio Review", ""]
    technicals = (snapshot_data or {}).get("technicals", {}) or {}

    if equity_reviews:
        lines.append("### Equities")
        lines.append("")
        for review in equity_reviews:
            ticker = review.get("ticker")
            price = review.get("price", 0)
            weight = review.get("weight", 0) * 100
            pl_pct = review.get("pl_pct", 0) * 100
            rec = review.get("recommendation")
            third_party = review.get("third_party_rec")
            _eq_rsi = rsi_discipline.rsi_for(ticker, technicals)
            _eq_rsi_suffix = f"  · {rsi_discipline.tag(_eq_rsi)}" if _eq_rsi is not None else ""
            lines.append(f"- **{ticker}** @ ${price:.2f} — {weight:.1f}% ({pl_pct:+.1f}%) → **{rec}**{_eq_rsi_suffix}")
            # Compact: the third-party sub-bullet duplicates the 🅿️ chip the
            # annotation post-pass appends to the header — skip it.
            if third_party and not compact:
                lines.append(f"  - {third_party}")
        lines.append("")

    if options_reviews:
        lines.append("### Options")
        lines.append("")
        for review in options_reviews:
            contract = review.get("contract")
            rec = review.get("recommendation")
            opt_type = review.get("type", "")
            strike = review.get("strike")
            exp = review.get("expiration")
            dte = review.get("days_to_expiry")
            mid = review.get("current_mid")
            entry = review.get("entry_price")
            cell = review.get("matrix_cell_id")
            qty = review.get("qty", 0)
            underlying = review.get("underlying") or (contract.split("_")[0] if contract and "_" in contract else "")
            opt_rsi = rsi_discipline.rsi_for(underlying, technicals)

            # Compute P&L for the entire position: (entry − mid) × 100 × |qty|.
            # This is from a SHORT seller's perspective: positive = profit.
            # (entry − mid) is per-share gain; × 100 = per-contract; × |qty| = per-position.
            if entry and mid:
                contracts = abs(int(qty)) if qty else 1
                pl_dollars = (entry - mid) * 100 * contracts
                pl_pct = (entry - mid) / entry * 100 if entry else 0
            else:
                pl_dollars = None
                pl_pct = None

            # Build header with emoji based on state
            emoji = "⚠️" if rec in ("CLOSE", "ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_OUT_AND_UP") else "📌"
            header = f"{emoji} **{contract}**"
            if opt_type and strike:
                header += f" {opt_type} ${strike:g}"
                if exp:
                    # Format: "Fri Jun 26 '26"
                    try:
                        exp_date = datetime.strptime(exp, "%Y-%m-%d")
                        exp_pretty = exp_date.strftime("%a %b %d '%y")
                        header += f" {exp_pretty}"
                    except ValueError:
                        header += f" exp {exp}"
                if dte is not None:
                    header += f", {dte}d left"
            if opt_rsi is not None:
                header += f"  · {rsi_discipline.tag(opt_rsi)}"

            # Commentary (computed before the compact branch so the
            # 🚨-urgent check can inspect it).
            commentary = generate_commentary(review, snapshot_data, equity_reviews)

            # Monthly opex-week awareness (2026-08-10, informational and
            # non-directional — rule #9): a position expiring on a standard
            # monthly (3rd Friday) gets ONE mechanics line during its final
            # week. Gated on expiration_policy.prefer_monthly; fail-open →
            # no line, never a fabricated one.
            _opex_line = None
            try:
                from analysis.expiration_policy import (
                    opex_week_line, policy_enabled)
                if policy_enabled((snapshot_data or {}).get("_config", {}) or {}):
                    _opex_line = opex_week_line(exp)
            except Exception:
                _opex_line = None

            # ── Compact mode (2026-08-06): HOLD-family, nothing urgent →
            # ONE summary line (position · P&L · capture % · verdict + one-
            # phrase why). The position is always present — never dropped.
            if compact and not _watch_needs_full_block(review, commentary,
                                                       snapshot_data):
                summary = header
                if pl_dollars is not None and pl_pct is not None:
                    pl_s = (f"+${pl_dollars:,.0f}" if pl_dollars > 0
                            else f"-${abs(pl_dollars):,.0f}")
                    summary += f" · P&L {pl_s} ({pl_pct:+.0f}% captured)"
                summary += f" → **{rec or 'HOLD'}**"
                why = None
                if commentary:
                    why = commentary[0]
                elif review.get("rationale"):
                    why = review.get("rationale")
                elif cell:
                    from render.panels import _humanize_matrix_cell
                    why = _humanize_matrix_cell(cell)
                if why:
                    summary += f" — {_truncate_phrase(why)}"
                lines.append(summary)
                if _opex_line:
                    lines.append(f"  • {_opex_line}")
                lines.append("")
                continue

            lines.append(header)

            # P&L line
            if pl_dollars is not None and pl_pct is not None:
                lines.append(f"  P&L: +${pl_dollars:,.0f} ({pl_pct:+.0f}%) | {dte}d left" if pl_dollars > 0
                            else f"  P&L: ${pl_dollars:,.0f} ({pl_pct:.0f}%) | {dte}d left")

            # Monthly opex-week note (informational, non-directional)
            if _opex_line:
                lines.append(f"  • {_opex_line}")

            # Disciplined RSI read for this short position (context, never a gate)
            rsi_note = _rsi_management_note(opt_rsi, opt_type, qty)
            if rsi_note:
                lines.append(f"  • {rsi_note}")

            # Commentary
            for note in commentary:
                lines.append(f"  • {note}")

            # Fall-back rationale if no commentary
            if not commentary and review.get("rationale"):
                lines.append(f"  - {review.get('rationale')}")

            # Rule #3 gate / HOLD_FOR_DECAY (TSM 2026-07-30): demoted roll
            # items surface HERE — the Watch panel is the ONLY surface for
            # them, never an actionable ticket. Fail-open: any error → no
            # line, the briefing still ships.
            _gate_note = review.get("_roll_gate_demotion")
            if _gate_note:
                lines.append(f"  • ⏸ {_gate_note}")
            # Task #40 fix 2: churn-guarded roll (position opened within
            # roll.min_position_age_days) — Watch note, never a ticket.
            _churn_note = review.get("_churn_guard_demotion")
            if _churn_note:
                lines.append(f"  • ⏸ {_churn_note}")
            # Task #40 fix 5: debit-to-collateral cap demotion.
            _debit_note = review.get("_debit_cap_demotion")
            if _debit_note:
                lines.append(f"  • ⏸ {_debit_note}")
            # Rule #43 micro-fix (AMZN $330C $27, 2026-08-04): take-profit
            # close below the dollar action floor (close_winner_min_dollars)
            # — Watch note, never an action slot.
            _floor_note = review.get("_close_floor_demotion")
            if _floor_note:
                lines.append(f"  • ⏸ _{_floor_note}_")
            # Redeploy-aware TP (George 2026-08-10: "If we don't have a path
            # to redeployment, then it doesn't make sense to close it.") —
            # a yield-motivated winner close held for the 75%+ zone because
            # the freed collateral has nowhere to go. Watch note with the
            # measured reason — visible, never hidden (rule #24).
            _rd_note = review.get("_redeploy_hold_demotion")
            if _rd_note:
                lines.append(f"  • {_rd_note}")
            if (opt_type or "").upper() == "PUT" and (qty or 0) < 0:
                try:
                    from render.panels import _exit_cost_anatomy
                    _an, _ = _exit_cost_anatomy(
                        review, snapshot_data, equity_reviews)
                    if _an is not None and _an.verdict == "HOLD_FOR_DECAY":
                        lines.append(
                            f"  • **⚖️ Verdict: HOLD_FOR_DECAY** — "
                            f"{_an.verdict_reason}"
                        )
                except Exception:
                    pass

            # Cell ID humanized (raw cell preserved in JSON sidecar for audit)
            if cell:
                # Import locally to avoid circular import
                from render.panels import _humanize_matrix_cell
                human = _humanize_matrix_cell(cell)
                if human:
                    lines.append(f"  - {human}")

            # Task #38 Part 2: credit-window indicator on every held short
            # put — the state that matters is "can I still roll for a
            # credit", surfaced one line above the ROLL ANALYSIS table.
            # Fail-open: any error → no line, never a fabricated state.
            if (opt_type or "").upper() == "PUT" and (qty or 0) < 0:
                try:
                    from analysis.credit_windows import (
                        credit_window_for_review, format_credit_window_line)
                    _cw = (((snapshot_data or {}).get("_credit_windows") or {})
                           .get(contract)
                           or credit_window_for_review(
                               review, (snapshot_data or {}).get("_config") or {}))
                    _cw_line = format_credit_window_line(_cw) if _cw else None
                    if _cw_line:
                        lines.append(f"  {_cw_line}")
                except Exception:
                    pass

            # Roll target (legacy single-target)
            roll = review.get("roll_target")
            if roll and not review.get("roll_candidates"):
                # Legacy select_roll_target keys are strikePrice/expirationDate
                # (rule #43, 2026-07-31: the old strike/expiration lookups
                # rendered None/? placeholders). Fail closed when unresolved.
                r_strike = roll.get("strikePrice") or roll.get("strike")
                r_exp = roll.get("expirationDate") or roll.get("expiration")
                if r_strike and r_exp:
                    lines.append(f"  - roll target: ${float(r_strike):g} exp {r_exp} for ${roll.get('expectedNetCredit', 0):.2f} credit")

            # NEW: Multi-candidate roll-analysis table from wheel-roll-advisor
            candidates = review.get("roll_candidates") or []
            if candidates:
                rec_id = review.get("recommended_candidate_id")
                if_anyway = review.get("if_rolling_anyway_candidate_id")
                lines.append("")
                # Assignment-basis header for ITM short puts (2026-07-29 LITE
                # lesson): before weighing roll candidates, show what owning
                # the shares via assignment would actually cost vs market.
                # All measured values (strike/premium/spot) — no line when any
                # is missing (CLAUDE.md #19).
                if ((opt_type or "").upper() == "PUT" and (qty or 0) < 0
                        and strike and underlying):
                    _spot_b = float(((snapshot_data or {}).get("quotes", {})
                                     .get(underlying) or {}).get("last") or 0)
                    if _spot_b and float(strike) > _spot_b:
                        try:
                            from analysis.exit_cost import format_basis_line
                            _basis_line = format_basis_line(strike, entry, _spot_b)
                        except Exception:
                            _basis_line = None
                        if _basis_line:
                            lines.append(f"  {_basis_line}")
                            lines.append("")
                lines.append("  **ROLL ANALYSIS:**")
                lines.append("")
                lines.append("  | id | Action | Net | Notes |")
                lines.append("  |----|--------|-----|-------|")
                # Task #43 defect 2 (AVGO candidate E, 2026-07-31): a
                # candidate extending +854d (~2.3yr) rendered with honest
                # tenor phrasing but NO warning that it sits 7× past the
                # action-list tenor cap. The ranker already never picks
                # these (max_tenor_days); the menu must say why. Core
                # names get the same 3× allowance the action list uses.
                _cfg_menu = (snapshot_data or {}).get("_config") or {}
                try:
                    _tenor_cap_menu = int((_cfg_menu.get("roll") or {})
                                          .get("max_action_tenor_days", 120))
                    # 2026-08-04 (PLTR): core = core_positions ∪ Tier A —
                    # tier-A names get the same 3× tenor allowance.
                    try:
                        from analysis.position_tiers import (
                            core_union as _core_union_menu,
                        )
                        _core_menu = _core_union_menu(_cfg_menu)
                    except Exception:
                        _core_menu = set(
                            _cfg_menu.get("core_positions") or [])
                    if underlying in _core_menu:
                        _tenor_cap_menu *= 3
                except (TypeError, ValueError, AttributeError):
                    _tenor_cap_menu = None  # unmeasurable cap → no warning
                for c in candidates:
                    cid = c.get("id", "?")
                    action = c.get("description", "")
                    net = c.get("netDollars", 0) or 0
                    notes = c.get("notes", "")
                    try:
                        _ext = int(c.get("dteExtension") or 0)
                    except (TypeError, ValueError):
                        _ext = 0
                    if _tenor_cap_menu and _ext > _tenor_cap_menu:
                        _warn = (f"⚠ far past the {_tenor_cap_menu}d tenor "
                                 f"cap — shown for completeness, not "
                                 f"recommended")
                        notes = f"{notes} · {_warn}" if notes else _warn
                    is_rec = cid == rec_id
                    is_anyway = cid == if_anyway
                    badge = ""
                    if is_rec:
                        badge = " ✅ recommended"
                    elif is_anyway:
                        badge = " ⤴️ if rolling anyway"
                    if net > 0:
                        net_str = f"+${net:,.0f} credit"
                    elif net < 0:
                        net_str = f"-${abs(net):,.0f} debit"
                    else:
                        net_str = "$0"
                    lines.append(f"  | {cid}{badge} | {action} | {net_str} | {notes} |")

                # If recommended is HOLD but there's a fallback ticket, show it
                ticket = review.get("if_rolling_anyway_ticket")
                if ticket and rec_id == "A" and if_anyway:
                    btc = ticket.get("buy_to_close", {})
                    sto = ticket.get("sell_to_open", {})
                    btc_exp = btc.get("expiration", "")
                    sto_exp = sto.get("expiration", "")
                    btc_strike = btc.get("strike", 0)
                    sto_strike = sto.get("strike", 0)
                    btc_lim = btc.get("limit_price", 0)
                    sto_lim = sto.get("limit_price", 0)
                    btc_qty = btc.get("quantity", 1)
                    net_cred = ticket.get("net_dollars_total", 0)

                    # Format expirations as "Fri Jun 26 '26"
                    def _fmt_exp(s):
                        try:
                            return datetime.strptime(s, "%Y-%m-%d").strftime("%a %b %d '%y")
                        except (ValueError, TypeError):
                            return s

                    lines.append("")
                    lines.append(f"  **IF ROLLING anyway, prefer {if_anyway}:**")
                    lines.append("")
                    lines.append("  ```")
                    lines.append(f"  BUY TO CLOSE   {btc_qty} × {review.get('underlying', '?')}  {_fmt_exp(btc_exp)}  ${btc_strike:g} {review.get('type', 'CALL')}   Limit ${btc_lim:.2f}  GTC")
                    # Bug #26 (2026-07-22): the STO leg carries the underlying's
                    # RSI inline — the parent ROLL header has it, but the RSI
                    # Coverage verifier scans ±3 lines and the ROLL ANALYSIS
                    # table pushes the header out of the window. Measured value
                    # only; omitted when RSI is unknown (never fabricated).
                    _sto_rsi = f"  · RSI {opt_rsi:.0f}" if opt_rsi is not None else ""
                    lines.append(f"  SELL TO OPEN   {btc_qty} × {review.get('underlying', '?')}  {_fmt_exp(sto_exp)}  ${sto_strike:g} {review.get('type', 'CALL')}   Limit ${sto_lim:.2f}  GTC{_sto_rsi}")
                    lines.append(f"  NET CREDIT TARGET: ${net_cred:,.0f}")
                    lines.append("  ```")

            lines.append("")

        lines.append("")

    return lines
