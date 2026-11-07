from __future__ import annotations

import logging
from dataclasses import dataclass

from config import (
    ALLOW_TRADING_WITHOUT_BTC_PRICE,
    EARLY_CONVICTION_MIN_SCORE,
    EARLY_CONVICTION_TRIGGER_PRICE,
    EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC,
    EARLY_ENTRY_MIN_SCORE,
    EXPECTED_SLIPPAGE_BPS,
    LIQUIDITY_MAX_SPREAD,
    LIQUIDITY_MIN_ASK_COUNT,
    LIQUIDITY_MIN_BID_COUNT,
    LIQUIDITY_MIN_SCORE,
    MAX_BET_USDC,
    MIN_NET_EXPECTED_PROFIT_USDC,
    SNIPE_HARD_DEADLINE_SECONDS,
    SNIPE_MIN_CONFIDENCE,
    SNIPE_SPIKE_CONFIDENCE_DELTA,
    SNIPE_START_SECONDS,
)
from market.orderer import BookSnapshot
from risk.sizing import get_bet_size
from state.session import BotState
from state.window import WindowState
from strategy.signal import Signal


@dataclass
class BookAssessment:
    liquidity_score: float
    combined_spread: float | None
    reason_code: str
    reason_detail: str
    trade_ready: bool


LIQUIDITY_RELAX_STRONG_CONVICTION = 0.82
LIQUIDITY_RELAX_MAX_SIDE_SPREAD = 0.05
LIQUIDITY_RELAX_MIN_ASK_RATIO = 0.70


def score_signal(signal: Signal) -> tuple[float, float, float]:
    regime_bonus = 0.01 if getattr(signal, "entry_regime", "") == "MISPRICING" else 0.0
    return (
        abs(signal.edge) + regime_bonus,
        signal.confidence + getattr(signal, "market_conviction", 0.0) * 0.05,
        abs(signal.ev),
    )


def choose_better_signal(
    best_signal: Signal | None, candidate_signal: Signal | None
) -> Signal | None:
    if candidate_signal is None:
        return best_signal
    if best_signal is None:
        return candidate_signal
    return candidate_signal if score_signal(candidate_signal) > score_signal(best_signal) else best_signal


def should_fire_snipe_signal(
    candidate_signal: Signal | None,
    *,
    best_signal: Signal | None,
    previous_signal: Signal | None,
    seconds_remaining: int,
    force_trade: bool,
    liquidity_score: float,
    net_expected_profit_usdc: float,
    min_net_profit_required: float = MIN_NET_EXPECTED_PROFIT_USDC,
) -> bool:
    if candidate_signal is None:
        return False
    if net_expected_profit_usdc < min_net_profit_required:
        return False
    if seconds_remaining > SNIPE_START_SECONDS:
        return (
            liquidity_score >= EARLY_ENTRY_MIN_SCORE
            and net_expected_profit_usdc >= EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC
            and candidate_signal.confidence >= SNIPE_MIN_CONFIDENCE
        )
    if candidate_signal.confidence >= SNIPE_MIN_CONFIDENCE:
        return True
    if previous_signal is not None and (
        candidate_signal.confidence - previous_signal.confidence
    ) >= SNIPE_SPIKE_CONFIDENCE_DELTA:
        return True
    if seconds_remaining <= SNIPE_HARD_DEADLINE_SECONDS:
        return force_trade or best_signal is not None
    return False


def book_price_spread(snapshot: BookSnapshot | None) -> float | None:
    if snapshot is None or snapshot.best_ask is None or snapshot.best_bid is None:
        return None
    return max(0.0, snapshot.best_ask - snapshot.best_bid)


def assess_book_quality(up_book: BookSnapshot | None, down_book: BookSnapshot | None) -> BookAssessment:
    if up_book is None or down_book is None:
        return BookAssessment(0.0, None, "NO_BOOK", "Order book snapshot unavailable", False)
    if up_book.ask_count == 0 and down_book.ask_count == 0:
        return BookAssessment(0.0, None, "NO_ASKS", "Both sides have no asks", False)
    if up_book.ask_count == 0 or down_book.ask_count == 0:
        detail = (
            "UP side has no asks" if up_book.ask_count == 0 else "DOWN side has no asks"
        )
        return BookAssessment(0.15, None, "ONE_SIDED_BOOK", detail, False)
    if up_book.bid_count < LIQUIDITY_MIN_BID_COUNT or down_book.bid_count < LIQUIDITY_MIN_BID_COUNT:
        combined_spread = None
        detail = (
            f"Bid depth too thin | UP {up_book.bid_count} | DOWN {down_book.bid_count}"
        )
        return BookAssessment(0.25, combined_spread, "THIN_BIDS", detail, False)

    up_spread = book_price_spread(up_book)
    down_spread = book_price_spread(down_book)
    if up_spread is None or down_spread is None:
        return BookAssessment(0.0, None, "NO_BOOK", "Best ask/bid missing on at least one side", False)

    combined_spread = round(up_spread + down_spread, 4)
    if up_book.ask_count < LIQUIDITY_MIN_ASK_COUNT or down_book.ask_count < LIQUIDITY_MIN_ASK_COUNT:
        liquidity_score = 0.35
        detail = (
            f"Ask depth thin | UP a{up_book.ask_count} | DOWN a{down_book.ask_count}"
        )
        return BookAssessment(liquidity_score, combined_spread, "THIN_ASKS", detail, False)

    spread_score = max(0.0, 1.0 - (combined_spread / max(LIQUIDITY_MAX_SPREAD, 0.01)))
    depth_score = min(
        1.0,
        (up_book.ask_count + down_book.ask_count + up_book.bid_count + down_book.bid_count)
        / 200.0,
    )
    available_depth_usdc = (
        up_book.depth_to_fill_usdc(up_book.best_ask)
        + down_book.depth_to_fill_usdc(down_book.best_ask)
    )
    size_score = min(1.0, available_depth_usdc / max(MAX_BET_USDC * 4, 1.0))
    liquidity_score = round((0.35 * depth_score) + (0.30 * size_score) + (0.35 * spread_score), 3)
    if combined_spread > LIQUIDITY_MAX_SPREAD:
        return BookAssessment(
            liquidity_score,
            combined_spread,
            "WIDE_SPREAD",
            f"Combined spread {combined_spread:.3f} exceeds limit {LIQUIDITY_MAX_SPREAD:.3f}",
            False,
        )
    if liquidity_score < LIQUIDITY_MIN_SCORE:
        return BookAssessment(
            liquidity_score,
            combined_spread,
            "LOW_LIQUIDITY_SCORE",
            f"Liquidity score {liquidity_score:.3f} below minimum {LIQUIDITY_MIN_SCORE:.3f}",
            False,
        )

    return BookAssessment(
        liquidity_score,
        combined_spread,
        "READY",
        f"Book ready | score={liquidity_score:.3f} spread={combined_spread:.3f}",
        True,
    )


def should_enter_on_waiting_conviction(
    *,
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    state: BotState,
    window: WindowState,
) -> tuple[bool, str]:
    if state.is_price_stale() and not ALLOW_TRADING_WITHOUT_BTC_PRICE:
        return False, "STALE_BTC_PRICE"
    state.update_polymarket_book(
        window_end_ts=window.end_ts,
        up_ask=up_book.best_ask if up_book is not None else None,
        down_ask=down_book.best_ask if down_book is not None else None,
        up_bid=up_book.best_bid if up_book is not None else None,
        down_bid=down_book.best_bid if down_book is not None else None,
        up_ask_count=up_book.ask_count if up_book is not None else 0,
        down_ask_count=down_book.ask_count if down_book is not None else 0,
        up_bid_count=up_book.bid_count if up_book is not None else 0,
        down_bid_count=down_book.bid_count if down_book is not None else 0,
        up_ask_size=up_book.best_ask_size if up_book is not None else 0.0,
        down_ask_size=down_book.best_ask_size if down_book is not None else 0.0,
        up_bid_size=up_book.best_bid_size if up_book is not None else 0.0,
        down_bid_size=down_book.best_bid_size if down_book is not None else 0.0,
        up_total_ask_size=up_book.total_ask_size if up_book is not None else 0.0,
        down_total_ask_size=down_book.total_ask_size if down_book is not None else 0.0,
        up_total_bid_size=up_book.total_bid_size if up_book is not None else 0.0,
        down_total_bid_size=down_book.total_bid_size if down_book is not None else 0.0,
    )
    features = state.polymarket_features()
    strongest_side = str(features.get("strongest_side", "NONE"))
    strongest_price = 0.0
    strongest_snapshot = None
    if strongest_side == "UP" and up_book is not None:
        strongest_price = float(up_book.best_ask or 0.0)
        strongest_snapshot = up_book
    elif strongest_side == "DOWN" and down_book is not None:
        strongest_price = float(down_book.best_ask or 0.0)
        strongest_snapshot = down_book
    if strongest_snapshot is None or strongest_snapshot.best_ask is None:
        return False, "NO_STRONG_SIDE_ASK"
    if strongest_price < EARLY_CONVICTION_TRIGGER_PRICE:
        return False, f"PRICE_BELOW_EARLY_CONVICTION:{strongest_price:.3f}"
    if strongest_snapshot.ask_count < LIQUIDITY_MIN_ASK_COUNT:
        return False, f"STRONG_SIDE_THIN_ASKS:{strongest_snapshot.ask_count}"
    side_spread = book_price_spread(strongest_snapshot)
    if side_spread is not None and side_spread > LIQUIDITY_MAX_SPREAD:
        return False, f"STRONG_SIDE_WIDE_SPREAD:{side_spread:.3f}"
    overall_assessment = assess_book_quality(up_book, down_book)
    liquidity_ok = overall_assessment.liquidity_score >= EARLY_CONVICTION_MIN_SCORE
    conviction_ok = float(features.get("market_conviction", 0.0)) >= 0.60
    if liquidity_ok and conviction_ok:
        return True, strongest_side
    if not conviction_ok:
        return False, f"EARLY_CONVICTION_LOW_SCORE:{float(features.get('market_conviction', 0.0)):.3f}"
    return False, f"EARLY_CONVICTION_LOW_LIQUIDITY:{overall_assessment.liquidity_score:.3f}"


def assess_book_quality_for_regime(
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    *,
    strongest_side: str,
    market_conviction: float,
) -> BookAssessment:
    base = assess_book_quality(up_book, down_book)
    if base.trade_ready:
        return base
    if up_book is None or down_book is None:
        return base
    if strongest_side not in {"UP", "DOWN"}:
        return base
    if market_conviction < LIQUIDITY_RELAX_STRONG_CONVICTION:
        return base

    strong = up_book if strongest_side == "UP" else down_book
    weak = down_book if strongest_side == "UP" else up_book
    side_spread = book_price_spread(strong)
    if side_spread is None or side_spread > LIQUIDITY_RELAX_MAX_SIDE_SPREAD:
        return base
    if strong.ask_count < max(1, int(LIQUIDITY_MIN_ASK_COUNT * LIQUIDITY_RELAX_MIN_ASK_RATIO)):
        return base
    if weak.ask_count == 0 and weak.bid_count == 0:
        return base

    if base.reason_code in {"THIN_BIDS", "LOW_LIQUIDITY_SCORE"}:
        relaxed_score = max(base.liquidity_score, LIQUIDITY_MIN_SCORE)
        return BookAssessment(
            liquidity_score=relaxed_score,
            combined_spread=base.combined_spread,
            reason_code="READY_RELAXED",
            reason_detail=(
                f"Relaxed liquidity gate for strong {strongest_side} conviction "
                f"({market_conviction:.3f}) with tight spread {side_spread:.3f}"
            ),
            trade_ready=True,
        )
    if base.reason_code in {"THIN_ASKS", "ONE_SIDED_BOOK"}:
        # Count-based gates are crude: few asks with real size behind them
        # are safer than many dust asks. Relax only when the strong side
        # can fill a full bet, spread is tight, and the weak side still
        # has bids (exit liquidity). Economics (net-profit floor) still
        # applies downstream as the final backstop.
        if strong.best_ask is None:
            return base
        if strong.depth_to_fill_usdc(strong.best_ask) < MAX_BET_USDC:
            return base
        if weak.bid_count == 0:
            return base
        relaxed_score = max(base.liquidity_score, LIQUIDITY_MIN_SCORE)
        return BookAssessment(
            liquidity_score=relaxed_score,
            combined_spread=base.combined_spread,
            reason_code="READY_RELAXED",
            reason_detail=(
                f"Relaxed count gate for strong {strongest_side} conviction "
                f"({market_conviction:.3f}): depth fills ${MAX_BET_USDC:.2f}, "
                f"spread {side_spread:.3f}, weak-side bids present"
            ),
            trade_ready=True,
        )
    return base


def spread_penalty_percentile(spread_penalty_usdc: float, bet_size: float) -> float:
    if spread_penalty_usdc <= 0 or bet_size <= 0:
        return 0.0
    baseline = max(1e-9, bet_size * (EXPECTED_SLIPPAGE_BPS / 10_000))
    ratio = spread_penalty_usdc / baseline
    return max(0.0, min(1.0, (ratio - 1.0) / 4.0))


def scaled_net_profit_floor_usdc(spread_penalty_pctile: float) -> float:
    pctile = max(0.0, min(1.0, spread_penalty_pctile))
    return MIN_NET_EXPECTED_PROFIT_USDC * (1.0 + 1.5 * pctile)


def estimate_trade_fee_usdc(
    *, asset: str, market_price: float, bet_size: float, fee_rate_bps: int = 0
) -> float:
    if bet_size <= 0 or market_price <= 0:
        return 0.0
    if fee_rate_bps > 0:
        return bet_size * (fee_rate_bps / 10_000)
    if asset.upper() in {"BTC", "ETH", "SOL", "XRP", "POL"}:
        return bet_size * 0.072 * market_price * (1 - market_price)
    return 0.0


def estimate_expected_profit_usdc(signal: Signal, bet_size: float) -> float:
    if bet_size <= 0 or MAX_BET_USDC <= 0:
        return 0.0
    return float(signal.ev) * (bet_size / MAX_BET_USDC)


def estimate_spread_penalty_usdc(
    *,
    bet_size: float,
    signal_market_price: float,
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    direction: str,
) -> float:
    snapshot = up_book if direction.upper() == "UP" else down_book
    baseline_slippage = bet_size * (EXPECTED_SLIPPAGE_BPS / 10_000)
    if snapshot is None or snapshot.best_ask is None or snapshot.best_bid is None:
        return baseline_slippage
    spread = max(0.0, snapshot.best_ask - snapshot.best_bid)
    if spread <= 0 or signal_market_price <= 0:
        return baseline_slippage
    spread_penalty = bet_size * (spread / max(signal_market_price, 0.01))
    return max(baseline_slippage, spread_penalty)


def evaluate_trade_economics(
    *,
    asset: str,
    signal: Signal,
    state: BotState,
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    liquidity_score: float | None = None,
    logger: logging.Logger | None = None,
) -> tuple[float, float, float, float, float, float]:
    candidate_bet = get_bet_size(
        signal.edge,
        signal.market_price,
        state.available_bankroll_usdc(),
        current_bankroll=state.current_bankroll_usdc(),
        starting_bankroll=state.starting_bankroll_usdc,
        consecutive_wins=state.consecutive_wins,
        consecutive_losses=state.consecutive_losses,
        entry_regime=getattr(signal, "entry_regime", "MISPRICING"),
        liquidity_score=liquidity_score,
        same_side_exposure_usdc=state.open_directional_exposure_usdc(signal.direction.value),
        total_open_exposure_usdc=state.open_window_exposure_usdc(),
    )
    if candidate_bet <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    for _ in range(20):
        gross_expected_profit = estimate_expected_profit_usdc(signal, candidate_bet)
        estimated_fee = estimate_trade_fee_usdc(
            asset=asset,
            market_price=signal.market_price,
            bet_size=candidate_bet,
        )
        spread_penalty = estimate_spread_penalty_usdc(
            bet_size=candidate_bet,
            signal_market_price=signal.market_price,
            up_book=up_book,
            down_book=down_book,
            direction=signal.direction.value,
        )
        net_expected_profit = gross_expected_profit - estimated_fee - spread_penalty
        adjusted_bet = get_bet_size(
            signal.edge,
            signal.market_price,
            state.available_bankroll_usdc(),
            current_bankroll=state.current_bankroll_usdc(),
            starting_bankroll=state.starting_bankroll_usdc,
            consecutive_wins=state.consecutive_wins,
            consecutive_losses=state.consecutive_losses,
            entry_regime=getattr(signal, "entry_regime", "MISPRICING"),
            liquidity_score=liquidity_score,
            net_expected_profit_usdc=net_expected_profit,
            same_side_exposure_usdc=state.open_directional_exposure_usdc(signal.direction.value),
            total_open_exposure_usdc=state.open_window_exposure_usdc(),
        )
        if abs(adjusted_bet - candidate_bet) < 0.01:
            spread_pctile = spread_penalty_percentile(spread_penalty, candidate_bet)
            return (
                candidate_bet,
                round(gross_expected_profit, 4),
                round(net_expected_profit, 4),
                round(estimated_fee + spread_penalty, 4),
                round(spread_penalty, 4),
                round(spread_pctile, 4),
            )
        candidate_bet = adjusted_bet
        if candidate_bet <= 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    if logger is None:
        logger = logging.getLogger("bot")
    logger.warning("evaluate_trade_economics: iteration cap reached, returning current bet")
    return (
        candidate_bet,
        round(gross_expected_profit, 4),
        round(net_expected_profit, 4),
        round(estimated_fee + spread_penalty, 4),
        round(spread_penalty, 4),
        round(spread_penalty_percentile(spread_penalty, candidate_bet), 4),
    )
