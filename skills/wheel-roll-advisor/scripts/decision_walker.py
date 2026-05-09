"""State derivation and matrix walking logic."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, List
from decimal import Decimal


@dataclass
class DerivedState:
    """All derived state variables."""
    position_type: str  # SHORT_PUT, SHORT_CALL, LONG_CALL, etc.
    moneyness: str  # DEEP_OTM, MODERATE_OTM, NEAR_ATM, ITM
    dte_band: str  # EXPIRY_WEEK, SHORT_DTE, MID_DTE, LONG_DTE, LEAP_DTE
    profit_captured_pct: float
    iv_regime: str  # LOW, NORMAL, HIGH
    outlook: str
    regime: str
    delta: float
    dte: int
    current_mid: float
    entry_price: float
    strike_price: float
    underlying_price: float


@dataclass
class Decision:
    """Decision output."""
    decision: str
    matrix_cell: str
    rationale: str
    roll_target: Optional[Dict[str, Any]] = None
    warnings: Optional[List[str]] = None


def derive_state(
    position: Dict[str, Any],
    underlying: Dict[str, Any],
    context: Dict[str, Any],
    params: Dict[str, Any],
) -> DerivedState:
    """Derive all 10 state variables from raw position/underlying/context data."""
    
    option_type = position.get("optionType", position.get("positionType", "")).replace("SHORT_", "").replace("LONG_", "")
    side = "SHORT" if "SHORT" in position.get("positionType", "") else "LONG"
    
    strike = float(position.get("strikePrice", 0))
    current_mid = float(position.get("currentMid", 0))
    entry = float(position.get("entryPrice", 0))
    underlying_price = float(underlying.get("lastPrice", 0))

    # Delta is optional; estimate from moneyness if not provided
    raw_delta = position.get("delta")
    if raw_delta is None:
        if strike > 0 and underlying_price > 0:
            ratio = underlying_price / strike
            opt_type = position.get("optionType", "PUT")
            is_short = "SHORT" in position.get("positionType", "")
            if opt_type == "PUT" and is_short:
                # Stock above strike → OTM, delta closer to 0; below strike → ITM
                if ratio >= 1.20:
                    raw_delta = -0.10
                elif ratio >= 1.05:
                    raw_delta = -0.20
                elif ratio >= 0.98:
                    raw_delta = -0.40
                else:
                    raw_delta = -0.60
            elif opt_type == "CALL" and is_short:
                if ratio <= 0.83:
                    raw_delta = 0.10
                elif ratio <= 0.95:
                    raw_delta = 0.20
                elif ratio <= 1.02:
                    raw_delta = 0.40
                else:
                    raw_delta = 0.60
            else:
                raw_delta = 0.0
        else:
            raw_delta = 0.0
    delta = float(raw_delta)
    dte = int(position.get("daysToExpiry", 0))

    # Moneyness: use strike vs spot as primary signal, not delta (which can be unreliable)
    # For SHORT_CALL: ITM when spot > strike; for SHORT_PUT: ITM when spot < strike
    moneyness = "NEAR_ATM"  # default

    if strike > 0 and underlying_price > 0:
        pct_away = abs(underlying_price - strike) / strike

        if option_type == "PUT" and side == "SHORT":
            # SHORT PUT: OTM when underlying > strike
            if underlying_price > strike:
                if pct_away > 0.15:
                    moneyness = "DEEP_OTM"
                elif pct_away > 0.08:
                    moneyness = "MODERATE_OTM"
                else:
                    moneyness = "NEAR_ATM"
            else:
                moneyness = "ITM"
        elif option_type == "CALL" and side == "SHORT":
            # SHORT CALL: OTM when underlying < strike
            if underlying_price < strike:
                if pct_away > 0.15:
                    moneyness = "DEEP_OTM"
                elif pct_away > 0.08:
                    moneyness = "MODERATE_OTM"
                else:
                    moneyness = "NEAR_ATM"
            else:
                moneyness = "ITM"
    
    # DTE bands
    if dte <= 7:
        dte_band = "EXPIRY_WEEK"
    elif dte <= 21:
        dte_band = "SHORT_DTE"
    elif dte <= 120:
        dte_band = "MID_DTE"
    elif dte <= 180:
        dte_band = "LONG_DTE"
    else:
        dte_band = "LEAP_DTE"
    
    # IV regime
    iv_rank = float(context.get("ivRank", 45))
    high_iv_threshold = float(params.get("high_iv_threshold", 60))
    low_iv_threshold = float(params.get("low_iv_threshold", 30))
    
    if iv_rank >= high_iv_threshold:
        iv_regime = "HIGH"
    elif iv_rank < low_iv_threshold:
        iv_regime = "LOW"
    else:
        iv_regime = "NORMAL"
    
    # Profit captured
    if entry > 0:
        profit_captured = max(0, (entry - current_mid) / entry)
    else:
        profit_captured = 0.0
    
    outlook = underlying.get("outlook", "NEUTRAL")
    regime = context.get("regime", "NORMAL")
    
    return DerivedState(
        position_type="SHORT_" + option_type,
        moneyness=moneyness,
        dte_band=dte_band,
        profit_captured_pct=profit_captured,
        iv_regime=iv_regime,
        outlook=outlook,
        regime=regime,
        delta=delta,
        dte=dte,
        current_mid=current_mid,
        entry_price=entry,
        strike_price=strike,
        underlying_price=underlying_price,
    )


def row_matches(row: Dict[str, Any], state: DerivedState) -> bool:
    """Check if a matrix row matches the derived state."""
    
    # Moneyness
    if "moneyness" in row:
        row_moneyness = row.get("moneyness")
        if isinstance(row_moneyness, list):
            if state.moneyness not in row_moneyness:
                return False
        elif row_moneyness != state.moneyness:
            return False
    
    # DTE band
    if "dte_band" in row:
        row_dte_band = row.get("dte_band")
        if isinstance(row_dte_band, list):
            if state.dte_band not in row_dte_band:
                return False
        elif row_dte_band != state.dte_band:
            return False
    
    # Outlook
    if "outlook" in row:
        row_outlook = row.get("outlook")
        if isinstance(row_outlook, list):
            if state.outlook not in row_outlook:
                return False
        elif row_outlook != state.outlook:
            return False
    
    # IV regime
    if "iv_regime" in row:
        row_regime = row.get("iv_regime")
        if isinstance(row_regime, list):
            if state.iv_regime not in row_regime:
                return False
        elif row_regime != state.iv_regime:
            return False
    
    # Profit bounds
    profit_min = row.get("profit_min")
    profit_max = row.get("profit_max")
    
    if profit_min is not None and state.profit_captured_pct < float(profit_min):
        return False
    if profit_max is not None and state.profit_captured_pct > float(profit_max):
        return False
    
    # Custom expression (if present)
    if "expression" in row:
        expr = row.get("expression")
        try:
            # Evaluate simple expressions like "dte < 15"
            result = eval(expr, {"dte": state.dte, "profit": state.profit_captured_pct})
            if not result:
                return False
        except:
            pass
    
    return True


def walk_matrix(matrix: List[Dict[str, Any]], state: DerivedState) -> Decision:
    """Walk matrix rows in order, return first match."""
    
    for row in matrix:
        if row_matches(row, state):
            return Decision(
                decision=row.get("decision", "INVALID_DECISION"),
                matrix_cell=row.get("id", "UNKNOWN_CELL"),
                rationale=row.get("rationale", ""),
                warnings=[],
            )
    
    # No match found
    return Decision(
        decision="INVALID_MATRIX_LOOKUP",
        matrix_cell="NO_MATCH",
        rationale="No matrix row matched derived state",
        warnings=["matrix_lookup_failed"],
    )
