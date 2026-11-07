from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config import (
    DAY_VOL_ELAPSED_MAX_SECONDS,
    DAY_VOL_ELAPSED_MIN_SECONDS,
    DAY_VOL_GATE_ENABLED,
    DAY_VOL_MIN_MOVE_BPS,
    DAY_VOL_MOVE_MULTIPLIER,
    EARLY_CONVICTION_TRIGGER_PRICE,
    ENTRY_MAX_CONTRACT_PRICE,
    ENTRY_MIN_CONTRACT_PRICE,
    LATE_CONVICTION_EDGE_THRESHOLD,
    LATE_CONVICTION_MAX_CONTRACT_PRICE,
    LATE_CONVICTION_MIN_CONTRACT_PRICE,
    LATE_CONVICTION_MIN_POLY_SCORE,
    LATE_CONVICTION_LOW_PRICE_MIN_POLY_SCORE,
    LATE_CONVICTION_OPEN_LOW_PRICE_MIN_POLY_SCORE,
    LATE_CONVICTION_OPEN_MIN_POLY_SCORE,
    LATE_CONVICTION_START_SECONDS,
    LATE_CONVICTION_STRONG_POLY_SCORE,
    LATE_CONVICTION_MIN_MOMENTUM_CONVICTION,
    LATE_CONVICTION_HIGH_PRICE_MOMENTUM_CONVICTION,
    LATE_CONVICTION_HIGH_PRICE_THRESHOLD,
    MAX_BET_USDC,
    MOMENTUM_SL_MAX_WINDOW_SECONDS,
    MOMENTUM_SL_SUPPRESS_ENABLED,
    MOMENTUM_SL_SUPPRESS_THRESHOLD,
    MOMENTUM_THRESHOLD,
)
from state.types import Direction
from state.window import WindowState
from strategy.edge import calculate_edge, expected_value, is_tradeable
from strategy.jev import resolve_btc_confirmation
from strategy.probability import ModelContext, get_probability


@dataclass
class Signal:
    direction: Direction
    confidence: float
    theo_prob: float
    market_price: float
    edge: float
    ev: float
    model_used: str
    entry_regime: str = "MISPRICING"
    strongest_side: str = "NONE"
    market_conviction: float = 0.0
    btc_confirmed: bool = False


@dataclass
class SignalEvaluation:
    signal: Signal | None
    strongest_side: str = "NONE"
    regime_candidate: str = "NONE"
    reason_code: str = ""
    reason_detail: str = ""
    market_conviction: float = 0.0
    btc_confirmed: bool = False


def price_in_entry_band(market_price: float) -> bool:
    return ENTRY_MIN_CONTRACT_PRICE <= market_price <= ENTRY_MAX_CONTRACT_PRICE


def price_in_late_conviction_band(market_price: float) -> bool:
    return (
        LATE_CONVICTION_MIN_CONTRACT_PRICE
        <= market_price
        <= LATE_CONVICTION_MAX_CONTRACT_PRICE
    )


def _build_signal(
    *,
    direction: Direction,
    theo_prob: float,
    market_price: float,
    model: str,
    entry_regime: str = "MISPRICING",
    strongest_side: str = "NONE",
    market_conviction: float = 0.0,
    btc_confirmed: bool = False,
) -> Signal:
    edge = calculate_edge(theo_prob, market_price, direction)
    confidence = theo_prob if direction == Direction.UP else (1 - theo_prob)
    ev = expected_value(edge=edge, bet_size=MAX_BET_USDC, market_price=market_price)
    return Signal(
        direction=direction,
        confidence=confidence,
        theo_prob=theo_prob,
        market_price=market_price,
        edge=edge,
        ev=ev,
        model_used=model.upper(),
        entry_regime=entry_regime,
        strongest_side=strongest_side,
        market_conviction=market_conviction,
        btc_confirmed=btc_confirmed,
    )


def _evaluate_mispricing_signal(
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float = 0.0,
    model: str = "A",
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    model_context: ModelContext | None = None,
) -> Optional[Signal]:
    if window.open_price == 0:
        window.set_open_price(btc_price)
        return None

    move_pct = (btc_price - window.open_price) / window.open_price
    if abs(move_pct) < MOMENTUM_THRESHOLD:
        return None

    direction = Direction.UP if move_pct > 0 else Direction.DOWN
    theo_prob = get_probability(
        window.open_price,
        btc_price,
        seconds_remaining,
        volatility,
        model,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        context=model_context,
    )
    market_price = up_market_price if direction == Direction.UP else down_market_price
    if not price_in_entry_band(market_price):
        return None
    signal = _build_signal(
        direction=direction,
        theo_prob=theo_prob,
        market_price=market_price,
        model=model,
        entry_regime="MISPRICING",
        strongest_side=model_context.strongest_side if model_context is not None else "NONE",
        market_conviction=model_context.market_conviction if model_context is not None else 0.0,
        btc_confirmed=(model_context.btc_poly_agreement > 0) if model_context is not None else False,
    )
    if not is_tradeable(signal.edge):
        return None
    return signal


def _late_conviction_adjustment(
    *,
    market_price: float,
    seconds_remaining: int,
    model_context: ModelContext,
) -> tuple[float, bool]:
    conviction = min(max(model_context.market_conviction, 0.0), 1.0)

    # Conviction threshold decays linearly from window-open (strict) to window-close (lenient).
    # Prevents trigger-happy entries early in the window when pump fakes are common.
    is_low_price = market_price < LATE_CONVICTION_HIGH_PRICE_THRESHOLD
    floor_poly = LATE_CONVICTION_LOW_PRICE_MIN_POLY_SCORE if is_low_price else LATE_CONVICTION_MIN_POLY_SCORE
    open_poly = LATE_CONVICTION_OPEN_LOW_PRICE_MIN_POLY_SCORE if is_low_price else LATE_CONVICTION_OPEN_MIN_POLY_SCORE
    window_span = max(LATE_CONVICTION_START_SECONDS - 5, 1)
    fraction_remaining = max(0.0, min(1.0, (seconds_remaining - 5) / window_span))
    min_poly = floor_poly + (open_poly - floor_poly) * fraction_remaining

    if conviction < min_poly:
        return 0.0, False
    if (
        seconds_remaining > LATE_CONVICTION_START_SECONDS
        and market_price < EARLY_CONVICTION_TRIGGER_PRICE
    ):
        return 0.0, False

    btc_confirmed = model_context.btc_poly_agreement > 0
    # Jev pilot: contested/expensive entries get a second opinion.
    # None = keep deterministic (flag off, no key, cheap entry, or API miss).
    jev_confirmed = resolve_btc_confirmation(
        model_context,
        seconds_remaining=seconds_remaining,
        market_price=market_price,
    )
    if jev_confirmed is not None:
        btc_confirmed = jev_confirmed
    strong_conviction = conviction >= LATE_CONVICTION_STRONG_POLY_SCORE
    if model_context.btc_poly_agreement < 0 and not strong_conviction:
        return 0.0, False

    time_factor = max(0.0, 1 - (seconds_remaining / max(LATE_CONVICTION_START_SECONDS, 1)))
    supporting_momentum = (
        max(0.0, model_context.up_price_momentum)
        if model_context.strongest_side == "UP"
        else max(0.0, model_context.down_price_momentum)
    )
    supporting_acceleration = (
        max(0.0, model_context.up_price_acceleration)
        if model_context.strongest_side == "UP"
        else max(0.0, model_context.down_price_acceleration)
    )

    # Change 4: require the market to actually be moving toward this side
    if supporting_momentum <= 0:
        return 0.0, False

    # Change 1: compute non-price conviction (momentum + acceleration + market health)
    # This decouples the gate from raw price — market must show real momentum, not just sit at a high price
    momentum_conviction = (
        min(supporting_momentum * 10.0, 0.18)
        + min(supporting_acceleration * 10.0, 0.08)
        + (1.0 - min(model_context.one_sided_ratio, 1.0)) * 0.12
        + max(model_context.strongest_queue_imbalance, 0.0) * 0.08
        + max(model_context.strongest_microprice_offset * 40.0, 0.0) * 0.05
    )
    if momentum_conviction < LATE_CONVICTION_MIN_MOMENTUM_CONVICTION:
        return 0.0, False

    if model_context.strongest_depth_to_fill_usdc < (MAX_BET_USDC * 0.75):
        return 0.0, False

    # Change 2: stricter gate for high-price entries (>= 0.79) — must have stronger momentum AND BTC agreement
    if market_price >= LATE_CONVICTION_HIGH_PRICE_THRESHOLD:
        if momentum_conviction < LATE_CONVICTION_HIGH_PRICE_MOMENTUM_CONVICTION:
            return 0.0, False
        if not btc_confirmed:
            return 0.0, False

    adjustment = (
        0.008
        + 0.05 * conviction
        + 0.02 * time_factor
        + min(supporting_momentum * 8.0, 0.04)
        + min(supporting_acceleration * 8.0, 0.015)
        + max(model_context.strongest_queue_imbalance, 0.0) * 0.025
        + max(model_context.strongest_microprice_offset * 40.0, 0.0) * 0.01
    )
    if btc_confirmed:
        adjustment += 0.015
    if model_context.one_sided_ratio > 0.25:
        adjustment -= 0.02 * min(model_context.one_sided_ratio, 1.0)
    if model_context.weakest_depth_to_fill_usdc > model_context.strongest_depth_to_fill_usdc:
        adjustment -= 0.015
    return max(0.0, adjustment), btc_confirmed


def _day_vol_gate(
    *,
    btc_price: float,
    window_open_price: float,
    seconds_remaining: int,
    strongest_side: str,
    day_vol_v24_bps: float | None,
) -> tuple[bool, str]:
    """Day-volatility move gate. Returns (allowed, detail).

    Late-window BTC moves persist (~97-99% at elapsed 180-240s), but only when
    the move is large relative to the trailing 24h median window move (V24).
    """
    if not DAY_VOL_GATE_ENABLED:
        return True, ""
    elapsed = 300 - seconds_remaining
    if not (DAY_VOL_ELAPSED_MIN_SECONDS <= elapsed <= DAY_VOL_ELAPSED_MAX_SECONDS):
        return False, (
            f"Day-vol gate: elapsed {elapsed}s outside "
            f"[{DAY_VOL_ELAPSED_MIN_SECONDS}-{DAY_VOL_ELAPSED_MAX_SECONDS}]s"
        )
    if day_vol_v24_bps is None:
        return False, "Day-vol gate: trailing window history unavailable or stale"
    if window_open_price <= 0 or btc_price <= 0:
        return False, "Day-vol gate: missing BTC price reference"
    move_bps = (btc_price - window_open_price) / window_open_price * 10_000
    if strongest_side == "UP" and move_bps < 0:
        return False, f"Day-vol gate: BTC move {move_bps:+.1f}bp disagrees with UP"
    if strongest_side == "DOWN" and move_bps > 0:
        return False, f"Day-vol gate: BTC move {move_bps:+.1f}bp disagrees with DOWN"
    required_bps = max(DAY_VOL_MIN_MOVE_BPS, DAY_VOL_MOVE_MULTIPLIER * day_vol_v24_bps)
    if abs(move_bps) < required_bps:
        return False, (
            f"Day-vol gate: |move| {abs(move_bps):.1f}bp < required "
            f"{required_bps:.1f}bp (V24 {day_vol_v24_bps:.1f}bp)"
        )
    return True, ""


def _evaluate_late_conviction_signal(
    *,
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float,
    model: str,
    ledger_path: str | None,
    calibration_path: str | None,
    model_context: ModelContext | None,
    day_vol_v24_bps: float | None = None,
) -> SignalEvaluation:
    strongest_side = model_context.strongest_side if model_context is not None else "NONE"
    conviction = model_context.market_conviction if model_context is not None else 0.0
    btc_confirmed = (model_context.btc_poly_agreement > 0) if model_context is not None else False
    if window.open_price == 0 or model_context is None:
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="NONE",
            reason_code="NO_POLY_CONTEXT",
            reason_detail="Polymarket conviction history is not available yet",
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )
    if strongest_side not in {"UP", "DOWN"}:
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="NONE",
            reason_code="NO_DOMINANT_SIDE",
            reason_detail="Neither Polymarket side has clear conviction",
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )

    market_price = up_market_price if strongest_side == "UP" else down_market_price
    if not price_in_late_conviction_band(market_price):
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="LATE_CONVICTION",
            reason_code="CONVICTION_TOO_EXPENSIVE",
            reason_detail="High-probability side is outside the late-conviction price band",
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )

    gate_allowed, gate_detail = _day_vol_gate(
        btc_price=btc_price,
        window_open_price=window.open_price,
        seconds_remaining=seconds_remaining,
        strongest_side=strongest_side,
        day_vol_v24_bps=day_vol_v24_bps,
    )
    if not gate_allowed:
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="LATE_CONVICTION",
            reason_code="DAY_VOL_MOVE_TOO_SMALL",
            reason_detail=gate_detail,
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )

    adjustment, btc_confirmed = _late_conviction_adjustment(
        market_price=market_price,
        seconds_remaining=seconds_remaining,
        model_context=model_context,
    )
    if adjustment <= 0:
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="LATE_CONVICTION",
            reason_code=(
                "BTC_DISAGREES" if model_context.btc_poly_agreement < 0 else "LATE_EDGE_TOO_SMALL"
            ),
            reason_detail="Polymarket conviction is not strong enough to overcome costs yet",
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )

    side_probability = min(0.97, market_price + adjustment)
    pooled_up_probability = get_probability(
        window.open_price,
        btc_price,
        seconds_remaining,
        volatility,
        "P",
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        context=model_context,
    )
    pooled_direction_probability = (
        pooled_up_probability if strongest_side == "UP" else (1 - pooled_up_probability)
    )
    if market_price >= LATE_CONVICTION_HIGH_PRICE_THRESHOLD:
        side_probability -= min(max(side_probability - pooled_direction_probability, 0.0), 0.05)
        if model_context.one_sided_ratio > 0.35:
            side_probability -= 0.015
        if model_context.strongest_depth_to_fill_usdc < (MAX_BET_USDC * 1.5):
            side_probability -= 0.015
        side_probability = max(market_price, side_probability)

    direction = Direction.UP if strongest_side == "UP" else Direction.DOWN
    theo_prob = side_probability if direction == Direction.UP else (1 - side_probability)
    signal = _build_signal(
        direction=direction,
        theo_prob=theo_prob,
        market_price=market_price,
        model=model,
        entry_regime="LATE_CONVICTION",
        strongest_side=strongest_side,
        market_conviction=conviction,
        btc_confirmed=btc_confirmed,
    )
    required_edge = LATE_CONVICTION_EDGE_THRESHOLD
    if model.upper() == "A":
        if market_price >= 0.90:
            required_edge += 0.04
        elif market_price >= 0.86:
            required_edge += 0.02
        if model_context.one_sided_ratio > 0.30:
            required_edge += 0.01
        if model_context.strongest_depth_to_fill_usdc < (MAX_BET_USDC * 1.75):
            required_edge += 0.01
    if signal.edge < required_edge:
        return SignalEvaluation(
            signal=None,
            strongest_side=strongest_side,
            regime_candidate="LATE_CONVICTION",
            reason_code="LATE_EDGE_TOO_SMALL",
            reason_detail=(
                f"Late-conviction edge {signal.edge:.3f} did not clear threshold "
                f"{required_edge:.3f}"
            ),
            market_conviction=conviction,
            btc_confirmed=btc_confirmed,
        )
    return SignalEvaluation(
        signal=signal,
        strongest_side=strongest_side,
        regime_candidate="LATE_CONVICTION",
        reason_code="READY",
        reason_detail="Late-conviction regime is tradeable",
        market_conviction=conviction,
        btc_confirmed=btc_confirmed,
    )


def _score_signal(signal: Signal) -> tuple[float, float, float, float]:
    if signal is None:
        return (0.0, 0.0, 0.0, 0.0)
    regime_weight = 1.0 if signal.entry_regime == "MISPRICING" else 0.95
    return (
        abs(signal.ev) * regime_weight,
        signal.edge,
        signal.confidence,
        signal.market_conviction,
    )


def _choose_signal(*signals: Signal | None) -> Signal | None:
    valid_signals = [signal for signal in signals if signal is not None]
    if not valid_signals:
        return None
    return max(valid_signals, key=_score_signal)


def evaluate_with_diagnostics(
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float = 0.0,
    model: str = "A",
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    model_context: ModelContext | None = None,
    btc_price_available: bool = True,
    day_vol_v24_bps: float | None = None,
) -> SignalEvaluation:
    if window.open_price == 0 and btc_price_available:
        window.set_open_price(btc_price)
        return SignalEvaluation(
            signal=None,
            strongest_side=model_context.strongest_side if model_context is not None else "NONE",
            regime_candidate="NONE",
            reason_code="WINDOW_OPEN_SET",
            reason_detail="Window open price was initialized from the latest BTC price",
            market_conviction=model_context.market_conviction if model_context is not None else 0.0,
            btc_confirmed=(model_context.btc_poly_agreement > 0) if model_context is not None else False,
        )

    # Skip mispricing signals if BTC price unavailable - rely on Polymarket-only signals
    mispricing_signal = None
    if btc_price_available:
        mispricing_signal = _evaluate_mispricing_signal(
            window,
            btc_price,
            seconds_remaining,
            up_market_price,
            down_market_price,
            volatility=volatility,
            model=model,
            ledger_path=ledger_path,
            calibration_path=calibration_path,
            model_context=model_context,
        )
    late_eval = _evaluate_late_conviction_signal(
        window=window,
        btc_price=btc_price,
        seconds_remaining=seconds_remaining,
        up_market_price=up_market_price,
        down_market_price=down_market_price,
        volatility=volatility,
        model=model,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        model_context=model_context,
        day_vol_v24_bps=day_vol_v24_bps,
    )
    signal = _choose_signal(mispricing_signal, late_eval.signal)
    if signal is not None:
        return SignalEvaluation(
            signal=signal,
            strongest_side=signal.strongest_side,
            regime_candidate=signal.entry_regime,
            reason_code="READY",
            reason_detail=f"{signal.entry_regime} signal is tradeable",
            market_conviction=signal.market_conviction,
            btc_confirmed=signal.btc_confirmed,
        )
    if late_eval.regime_candidate == "LATE_CONVICTION":
        return late_eval
    return SignalEvaluation(
        signal=None,
        strongest_side=model_context.strongest_side if model_context is not None else "NONE",
        regime_candidate="MISPRICING",
        reason_code="NO_SIGNAL",
        reason_detail="Model signal did not clear price-band or edge gates",
        market_conviction=model_context.market_conviction if model_context is not None else 0.0,
        btc_confirmed=(model_context.btc_poly_agreement > 0) if model_context is not None else False,
    )


def evaluate(
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float = 0.0,
    model: str = "A",
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    model_context: ModelContext | None = None,
    btc_price_available: bool = True,
) -> Optional[Signal]:
    return evaluate_with_diagnostics(
        window,
        btc_price,
        seconds_remaining,
        up_market_price,
        down_market_price,
        volatility=volatility,
        model=model,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        model_context=model_context,
        btc_price_available=btc_price_available,
    ).signal


def evaluate_interval(
    window: WindowState,
    btc_price: float,
    seconds_remaining: int,
    up_market_price: float,
    down_market_price: float,
    volatility: float = 0.0,
    model: str = "A",
    *,
    force_trade: bool = False,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    model_context: ModelContext | None = None,
    btc_price_available: bool = True,
) -> Optional[Signal]:
    evaluation = evaluate_with_diagnostics(
        window,
        btc_price,
        seconds_remaining,
        up_market_price,
        down_market_price,
        volatility=volatility,
        model=model,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        model_context=model_context,
        btc_price_available=btc_price_available,
    )
    signal = evaluation.signal
    if signal is not None or not force_trade:
        return signal

    if window.open_price == 0 and btc_price_available:
        window.set_open_price(btc_price)
        return None

    # Can't force trade without BTC price
    if not btc_price_available:
        return None

    theo_prob = get_probability(
        window.open_price,
        btc_price,
        seconds_remaining,
        volatility,
        model,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        context=model_context,
    )
    up_signal = _build_signal(
        direction=Direction.UP,
        theo_prob=theo_prob,
        market_price=up_market_price,
        model=model,
        strongest_side=model_context.strongest_side if model_context is not None else "NONE",
        market_conviction=model_context.market_conviction if model_context is not None else 0.0,
        btc_confirmed=(model_context.btc_poly_agreement > 0) if model_context is not None else False,
    )
    down_signal = _build_signal(
        direction=Direction.DOWN,
        theo_prob=theo_prob,
        market_price=down_market_price,
        model=model,
        strongest_side=model_context.strongest_side if model_context is not None else "NONE",
        market_conviction=model_context.market_conviction if model_context is not None else 0.0,
        btc_confirmed=(model_context.btc_poly_agreement > 0) if model_context is not None else False,
    )
    up_allowed = price_in_entry_band(up_signal.market_price)
    down_allowed = price_in_entry_band(down_signal.market_price)
    if not up_allowed and not down_allowed:
        return None
    if not up_allowed:
        return down_signal
    if not down_allowed:
        return up_signal
    if down_signal.edge > up_signal.edge:
        return down_signal
    if up_signal.edge > down_signal.edge:
        return up_signal
    if down_market_price < up_market_price:
        return down_signal
    return up_signal


def compute_sl_confirmation_seconds(
    direction: str,
    context: ModelContext | None,
    base_seconds: float,
) -> float:
    """Extend the borderline SL confirmation window when momentum strongly supports the trade.

    Based on:
    - Cont, Kukanov & Stoikov (2014): OFI momentum decays with ~20s half-life in BTC
    - Modal-tick ratio (directional_consistency) > 0.65 signals a trending regime (note: this is not true Kaufman ER but is a valid trend proxy at this timescale)
    - Bouchaud et al. (2004): require acceleration >= 0 to guard against exhaustion/climax moves
    - Hard cap at MOMENTUM_SL_MAX_WINDOW_SECONDS (~0.5× AC half-life) to prevent death spirals

    Only applies to borderline breaches. Hard crash exits (SL_INSTANT_BREACH_PCT) are never suppressed.
    """
    if not MOMENTUM_SL_SUPPRESS_ENABLED or context is None:
        return base_seconds

    # Guard: if momentum is decelerating (acceleration < 0), price may be exhausting — don't suppress
    supporting_accel = (
        context.up_price_acceleration if direction == "UP"
        else context.down_price_acceleration
    )
    if supporting_accel < 0:
        return base_seconds

    # Compute suppression score (0–1) from four signals
    supporting_momentum = (
        max(0.0, context.up_price_momentum) if direction == "UP"
        else max(0.0, context.down_price_momentum)
    )
    # Normalize momentum: typical max ~0.05 (5% per tick)
    norm_momentum = min(supporting_momentum / 0.05, 1.0)
    btc_agreement = 1.0 if context.btc_poly_agreement > 0 else 0.0

    score = (
        0.35 * max(0.0, min(1.0, context.directional_consistency))
        + 0.25 * norm_momentum
        + 0.25 * max(0.0, min(1.0, context.market_conviction))
        + 0.15 * btc_agreement
    )

    if score < MOMENTUM_SL_SUPPRESS_THRESHOLD:
        return base_seconds

    # Linearly scale window from base to max as score goes from threshold to 1.0
    t = (score - MOMENTUM_SL_SUPPRESS_THRESHOLD) / max(1.0 - MOMENTUM_SL_SUPPRESS_THRESHOLD, 1e-9)
    extended = base_seconds + t * (MOMENTUM_SL_MAX_WINDOW_SECONDS - base_seconds)
    return min(extended, MOMENTUM_SL_MAX_WINDOW_SECONDS)


def build_hedge_signal(
    primary_signal: Signal,
    *,
    up_market_price: float,
    down_market_price: float,
    allow_late_conviction: bool = False,
) -> Signal | None:
    if primary_signal is None:
        return None
    if primary_signal.entry_regime == "LATE_CONVICTION" and not allow_late_conviction:
        return None
    direction = Direction.DOWN if primary_signal.direction == Direction.UP else Direction.UP
    market_price = down_market_price if direction == Direction.DOWN else up_market_price
    if not price_in_entry_band(market_price):
        return None
    return _build_signal(
        direction=direction,
        theo_prob=primary_signal.theo_prob,
        market_price=market_price,
        model=primary_signal.model_used,
        entry_regime="HEDGE",
        strongest_side=primary_signal.strongest_side,
        market_conviction=primary_signal.market_conviction,
        btc_confirmed=primary_signal.btc_confirmed,
    )
