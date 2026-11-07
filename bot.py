from __future__ import annotations

import argparse
import asyncio
from collections import deque
import contextlib
import copy
import hashlib
import json
import logging
import math
import os
import signal
import statistics
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    ACTIVE_MODEL_TRACK,
    ALERTS_FILE,
    ALERT_ORDER_FAILURE_THRESHOLD,
    ALERT_ORDER_FAILURE_WINDOW_SECONDS,
    ALERT_RISK_BREACH_THROTTLE_SECONDS,
    ALERT_WEBHOOK_URL,
    ALLOW_TRADING_WITHOUT_BTC_PRICE,
    ASSET_KEYWORDS,
    BANKROLL_RISK_FRACTION,
    BINANCE_REST_SYMBOLS,
    BOT_LOCK_FILE,
    COOLDOWN_SECONDS,
    CONSECUTIVE_LOSS_PAUSE_SECONDS,
    DATA_DIR,
    DEFAULT_MAX_CORRECTION_AGE,
    DRY_RUN,
    EDGE_THRESHOLD,
    EXIT_REVERSE_SIGNAL,
    EXIT_STOP_LOSS_PCT,
    HIGH_CONVICTION_ENTRY_THRESHOLD,
    CONVICTION_DECAY_FLOOR,
    CONVICTION_DECAY_SECONDS,
    HIGH_CONVICTION_MIN_ELAPSED_SECONDS,
    HIGH_CONVICTION_SL_PCT,
    HIGH_CONVICTION_TIME_EXIT,
    HIGH_CONVICTION_TIME_EXTENSION_SECONDS,
    MOMENTUM_SL_VELOCITY_PCT,
    MOMENTUM_SL_VELOCITY_WINDOW,
    EARLY_WINDOW_TP_PCT,
    LATE_WINDOW_TP_PCT,
    LATE_WINDOW_THRESHOLD_SECONDS,
    EXIT_TAKE_PROFIT_PCT,
    EXIT_TP1_FRACTION,
    EXIT_TP1_PCT,
    EXIT_TRAIL_PCT,
    EXIT_TIME_SECONDS,
    LOW_CONVICTION_HARD_TIME_EXIT,
    MIN_HOLD_SECONDS,
    SL_CONFIRMATION_SECONDS,
    SHADOW_MULTI_TRACK_EXECUTION,
    SL_INSTANT_BREACH_PCT,
    ENTRY_WINDOW_END,
    ENTRY_WINDOW_START,
    FORCE_INTERVAL_TRADES,
    HEDGE_COOLDOWN_SECONDS,
    HEDGE_ENABLED,
    HEDGE_FRACTION,
    LEDGER_FILE,
    LEDGER_FILE_CONVICTION,
    LEDGER_FILE_MISPRICING,
    LEDGER_FILE_BOTH,
    LIVE_BALANCE_BUFFER_USDC,
    LIVE_DRY_RUN,
    LIVE_FORCE_INTERVAL_TRADES,
    LIVE_HEDGE_ENABLED,
    LIVE_KILL_SWITCH_FILE,
    INTENT_RECON_ABORT_GRACE_SECONDS,
    LIVE_MIN_AVAILABLE_USDC,
    LIVE_REQUIRE_EMPTY_OPEN_ORDERS,
    LIVE_RECON_LOOKBACK_SECONDS,
    LIVE_STARTUP_CANCEL_ALL,
    LOG_LEVEL,
    MAX_BET_USDC,
    MAX_PRICE_FEED_DIVERGENCE_BPS,
    MAX_POSITIONS_PER_WINDOW,
    MIN_BET_USDC,
    MIN_HEALTHY_PRICE_FEEDS,
    MIN_NET_EXPECTED_PROFIT_USDC,
    MODEL_C_CALIBRATION_FILE,
    POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS,
    POLYMARKET_API_KEY,
    POLYMARKET_FUNDER,
    POLYMARKET_PASSPHRASE,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_SECRET,
    POLYMARKET_SIGNATURE_TYPE,
    RUNTIME_STATUS_FILE,
    RUNTIME_STATUS_UPDATE_SECONDS,
    SNIPE_HARD_DEADLINE_SECONDS,
    SNIPE_EARLY_ENTRY_SECONDS,
    SNIPE_MIN_CONFIDENCE,
    SNIPE_POLL_SECONDS,
    SNIPE_SPIKE_CONFIDENCE_DELTA,
    SNIPE_START_SECONDS,
    STARTING_BANKROLL_USDC,
    TARGET_ASSET,
    UNRESOLVED_SWEEP_INTERVAL_SECONDS,
    PRICE_STALE_THRESHOLD_SECONDS,
    validate_config,
)
from feeds.binance import (
    CoinbaseFeed,
    fetch_binance_rest_json,
    fetch_binance_spot_price,
    fetch_coinbase_spot_price,
)
from feeds.bybit import BybitFeed, fetch_bybit_spot_price
from feeds.gemini import GeminiFeed, fetch_gemini_spot_price
from feeds.kraken import KrakenFeed, fetch_kraken_spot_price
from market.client import PolymarketClient, PublicPolymarketClient
from market.finder import MarketFinder
from market.kalshi import get_btc_yes_probability
from market.orderer import (
    AmbiguousOrderError,
    BookSnapshot,
    OrderResult,
    OrderStatus,
    Orderer,
)
from ops.alerts import AlertManager
from ops.live_startup import (
    IntentReconciliationResult,
    LivePreflightResult,
    LiveReconciliationResult,
    perform_live_preflight as _perform_live_preflight,
    reconcile_live_ledger as _reconcile_live_ledger,
    reconcile_open_order_intents as _reconcile_open_order_intents,
)
from ops.resolution import (
    fetch_binance_agg_trade_price as _fetch_binance_agg_trade_price,
    resolve_market_outcome as _resolve_market_outcome,
    resolve_with_binance as _resolve_with_binance,
    sweep_unresolved_trades_once as _sweep_unresolved_trades_once,
    wait_for_market_resolution as _wait_for_market_resolution,
)
from ops.lock import process_lock
from persistence.ledger import Ledger
from risk.guard import DailyGuard
from risk.sizing import fixed_size, get_bet_size, plan_position_sizes
from state import BotState, ExitReason, Position, PositionStatus, WindowPhase, WindowState
from state.track import ModelTrack, TRACK_COLORS, TRACK_CSS_COLORS
from strategy.probability import (
    ModelContext,
    bucket_acceleration,
    bucket_directional_consistency,
    bucket_move_pct,
    bucket_probability,
    bucket_seconds_remaining,
    build_calibration_regime_key,
    build_model_c_bucket_key,
    get_probability,
)
from strategy.entry_helpers import (
    BookAssessment,
    assess_book_quality_for_regime as _assess_book_quality_for_regime,
    book_price_spread as _book_price_spread,
    choose_better_signal as _choose_better_signal,
    estimate_trade_fee_usdc as _estimate_trade_fee_usdc,
    evaluate_trade_economics as _evaluate_trade_economics,
    scaled_net_profit_floor_usdc as _scaled_net_profit_floor_usdc,
    score_signal as _score_signal,
    should_enter_on_waiting_conviction as _should_enter_on_waiting_conviction,
    should_fire_snipe_signal as _should_fire_snipe_signal,
)
from strategy.signal import build_hedge_signal, compute_sl_confirmation_seconds, evaluate_with_diagnostics


LOGGER = logging.getLogger("bot")
ALERTER: AlertManager | None = None
_LAST_ALERT_TS: dict[tuple[str, str], float] = {}
_ORDER_FAILURE_TS: dict[str, deque[float]] = {}


def emit_alert(level: str, message: str, **context) -> None:
    if ALERTER is None:
        return
    with contextlib.suppress(Exception):
        ALERTER.emit(level, message, **context)


def emit_alert_throttled(
    level: str,
    message: str,
    *,
    throttle_seconds: float = 60.0,
    alert_key: str | None = None,
    **context,
) -> None:
    key = (level.upper(), alert_key or message)
    now = time.time()
    last_ts = _LAST_ALERT_TS.get(key, 0.0)
    if now - last_ts < throttle_seconds:
        return
    _LAST_ALERT_TS[key] = now
    emit_alert(level, message, **context)


def _book_unavailable_reason(state: BotState) -> tuple[str, str]:
    book_age = state.polymarket_book_age_seconds()
    if state.is_polymarket_book_stale():
        age_label = "never" if book_age is None else f"{book_age:.1f}s"
        return (
            "STALE_POLYMARKET_BOOK",
            f"Polymarket book stale ({age_label} since last successful refresh)",
        )
    return ("ORDER_BOOK_UNAVAILABLE", "Order book unavailable (API unreachable)")


def _looks_like_auth_failure(error_text: str) -> bool:
    text = error_text.lower()
    markers = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "invalid signature",
        "authentication",
        "auth failed",
        "passphrase",
    )
    return any(marker in text for marker in markers)


def _record_order_failure_alert(track: ModelTrack, *, token_id: str, error_text: str) -> None:
    if _looks_like_auth_failure(error_text):
        emit_alert_throttled(
            "CRITICAL",
            "Order auth failure",
            throttle_seconds=60.0,
            alert_key=f"order_auth_failure:{track.name}",
            track=track.name,
            token_id=token_id,
            error=error_text,
        )
        return

    tracker = _ORDER_FAILURE_TS.setdefault(track.name, deque())
    now = time.time()
    tracker.append(now)
    while tracker and now - tracker[0] > ALERT_ORDER_FAILURE_WINDOW_SECONDS:
        tracker.popleft()
    if len(tracker) < ALERT_ORDER_FAILURE_THRESHOLD:
        return

    emit_alert_throttled(
        "ERROR",
        "Repeated order failures",
        throttle_seconds=ALERT_ORDER_FAILURE_WINDOW_SECONDS,
        alert_key=f"order_failures:{track.name}",
        track=track.name,
        token_id=token_id,
        failure_count=len(tracker),
        failure_window_seconds=ALERT_ORDER_FAILURE_WINDOW_SECONDS,
        latest_error=error_text,
    )


def _emit_guard_breach_alert(track: ModelTrack, reason: str) -> None:
    message: str | None = None
    level = "WARNING"
    alert_key = None
    context = {
        "track": track.name,
        "reason": reason,
        "daily_pnl": round(track.state.daily_pnl, 4),
        "available_bankroll_usdc": round(track.state.available_bankroll_usdc(), 4),
        "current_bankroll_usdc": round(track.state.current_bankroll_usdc(), 4),
        "pending_exposure_usdc": round(track.state.pending_exposure_usdc(), 4),
    }

    if reason.startswith("Daily loss limit reached"):
        message = "Daily loss limit breached"
        level = "CRITICAL"
        alert_key = f"guard_daily_loss:{track.name}"
    elif reason.startswith("Drawdown limit reached"):
        message = "Canary drawdown limit breached"
        level = "CRITICAL"
        alert_key = f"guard_drawdown:{track.name}"
    elif reason.startswith("Max open orders reached"):
        message = "Max open orders reached"
        alert_key = f"guard_open_orders:{track.name}"
    elif reason.startswith("Max concurrent positions reached"):
        message = "Max concurrent positions reached"
        alert_key = f"guard_positions:{track.name}"
    elif reason.startswith("Max unresolved exposure reached"):
        message = "Max unresolved exposure reached"
        alert_key = f"guard_unresolved_exposure:{track.name}"
    elif reason.startswith("Max window exposure reached"):
        message = "Max window exposure reached"
        alert_key = f"guard_window_exposure:{track.name}"

    if message is None:
        return

    emit_alert_throttled(
        level,
        message,
        throttle_seconds=ALERT_RISK_BREACH_THROTTLE_SECONDS,
        alert_key=alert_key,
        **context,
    )


def create_position_from_trade(
    order_id: str,
    window: WindowState,
    direction: str,
    size_usdc: float,
    entry_price: float,
    entry_ts: float,
    role: str = "PRIMARY",
    fee_rate_bps: int = 0,
    seconds_remaining: int | None = None,
    signal: object | None = None,
    feature_snapshot: dict | None = None,
) -> Position:
    """Create a Position object from a trade execution.

    TP strategy is tiered by both trade number and window time remaining:
    - Early entry (>LATE_WINDOW_THRESHOLD_SECONDS remaining): aggressive TP at
      EARLY_WINDOW_TP_PCT (8%) — grab profit fast before reversal; time exit applies.
    - Late entry (<LATE_WINDOW_THRESHOLD_SECONDS remaining): patient TP at
      LATE_WINDOW_TP_PCT (20%), no time exit — hold to window close.
    - Trade 3+ in either mode: no TP, ride to window close with SL only.

    TP/SL prices are adjusted so the net P&L after round-trip fees equals the
    configured target percentages. Fee rate is taken from the order result; for
    dry-run (fee_rate_bps=0) we estimate using the standard formula.
    """
    # Count existing positions in this direction to determine trade number
    existing_count = sum(
        1 for p in window.get_open_positions()
        if p.direction == direction and p.role == "PRIMARY"
    )
    trade_number = existing_count + 1

    # Determine window phase: early (lots of time) vs late (closing soon)
    secs = seconds_remaining if seconds_remaining is not None else window.seconds_remaining(int(time.time()))
    late_window = secs < LATE_WINDOW_THRESHOLD_SECONDS

    # Set take profit based on trade number and window phase
    if trade_number >= 3:
        tp_pct_net = None  # 3rd+: no TP, ride to window close
    elif late_window:
        tp_pct_net = LATE_WINDOW_TP_PCT  # patient: hold for 20%
    else:
        tp_pct_net = EARLY_WINDOW_TP_PCT  # aggressive: take 8% fast

    # Estimate round-trip fee as a fraction of position size (entry + exit).
    # With fee_rate_bps known (live mode): one_way = fee_rate_bps / 10_000.
    # In dry-run (fee_rate_bps=0): use the market-price-based estimate.
    if fee_rate_bps > 0:
        one_way_fee = fee_rate_bps / 10_000
    else:
        one_way_fee = 0.072 * entry_price * (1 - entry_price)
    round_trip_fee = 2 * one_way_fee

    # Adjust TP/SL so net P&L after fees equals the configured targets.
    # TP: token must rise enough to cover fees + target gain.
    # SL: token must only fall enough that after fees total loss = EXIT_STOP_LOSS_PCT.
    take_profit_price = entry_price * (1 + tp_pct_net + round_trip_fee) if tp_pct_net is not None else None
    take_profit_1_price = None
    if tp_pct_net is not None and tp_pct_net > EXIT_TP1_PCT and EXIT_TP1_FRACTION > 0:
        take_profit_1_price = entry_price * (1 + EXIT_TP1_PCT + round_trip_fee)
    sl_pct = HIGH_CONVICTION_SL_PCT if entry_price >= HIGH_CONVICTION_ENTRY_THRESHOLD else EXIT_STOP_LOSS_PCT
    stop_loss_price = entry_price * (1 - sl_pct + round_trip_fee)

    # Time exit: only for early window entries (late entries hold to close)
    is_high_conviction = entry_price >= HIGH_CONVICTION_ENTRY_THRESHOLD
    base_time_exit = HIGH_CONVICTION_TIME_EXIT if is_high_conviction else EXIT_TIME_SECONDS
    entry_regime = getattr(signal, "entry_regime", "MISPRICING") if signal is not None else "MISPRICING"
    entry_edge = float(getattr(signal, "edge", 0.0) or 0.0) if signal is not None else 0.0
    entry_confidence = float(getattr(signal, "confidence", 0.0) or 0.0) if signal is not None else 0.0
    entry_market_conviction = (
        float(getattr(signal, "market_conviction", 0.0) or 0.0) if signal is not None else 0.0
    )
    entry_pooled_probability = float((feature_snapshot or {}).get("pooled_probability", 0.0) or 0.0)
    weak_entry = (
        entry_regime == "MISPRICING"
        and (
            entry_edge < 0.05
            or entry_confidence < 0.68
            or (
                entry_pooled_probability > 0
                and abs(entry_confidence - entry_pooled_probability) > 0.08
            )
        )
    )
    if trade_number >= 3 or late_window:
        time_exit = None  # 3rd+ or late entry: hold to window close
    else:
        time_exit = base_time_exit
        if weak_entry:
            time_exit = max(3, base_time_exit - 2)
    hard_time_exit = None if is_high_conviction else (LOW_CONVICTION_HARD_TIME_EXIT if trade_number < 3 else None)
    if weak_entry and hard_time_exit is not None and time_exit is not None:
        hard_time_exit = min(hard_time_exit, max(time_exit + 4, 8))

    conviction_decay_floor = CONVICTION_DECAY_FLOOR
    conviction_decay_seconds = CONVICTION_DECAY_SECONDS
    if entry_market_conviction > 0:
        decay_gap = 0.12 if entry_regime == "LATE_CONVICTION" else 0.18
        conviction_decay_floor = max(CONVICTION_DECAY_FLOOR, entry_market_conviction - decay_gap)
    if entry_regime == "LATE_CONVICTION":
        conviction_decay_seconds = min(CONVICTION_DECAY_SECONDS, 3.0)
    elif weak_entry:
        conviction_decay_seconds = min(CONVICTION_DECAY_SECONDS, 4.0)

    return Position(
        order_id=order_id,
        window_end_ts=window.end_ts,
        direction=direction,
        size_usdc=size_usdc,
        entry_price=entry_price,
        entry_ts=entry_ts,
        role=role,
        take_profit_price=take_profit_price,
        stop_loss_price=stop_loss_price,
        take_profit_1_price=take_profit_1_price,
        take_profit_1_fraction=EXIT_TP1_FRACTION if take_profit_1_price is not None else 0.0,
        time_exit_seconds=time_exit,
        time_exit_hard_seconds=hard_time_exit,
        trade_number=trade_number,
        is_high_conviction=is_high_conviction,
        conviction_decay_floor=conviction_decay_floor,
        conviction_decay_seconds=conviction_decay_seconds,
        break_even_price=entry_price * (1 + round_trip_fee),
        momentum_sl_velocity_pct=MOMENTUM_SL_VELOCITY_PCT,
        momentum_sl_velocity_window=MOMENTUM_SL_VELOCITY_WINDOW,
        entry_confidence=entry_confidence,
        entry_edge=entry_edge,
        entry_market_conviction=entry_market_conviction,
        entry_pooled_probability=entry_pooled_probability,
        entry_regime=entry_regime,
    )


async def check_position_exits(
    track: ModelTrack,
    *,
    asset: str,
    window: WindowState,
    up_price: float,
    down_price: float,
    model_context: ModelContext | None = None,
) -> list[Position]:
    """Check all open positions for exit conditions and execute exits.

    Two-path exit detection:
    1. Resting order fills (live mode): checks GTC SL/TP orders placed at entry.
       Fills at the exact target price with zero polling lag.
    2. Price polling (dry-run + live fallback): triggered by tiered SL / TP /
       TIME_EXIT logic. Also cancels any resting orders before placing the sell.
    """
    closed_positions = []
    open_positions = window.get_open_positions()

    if not open_positions:
        return closed_positions

    now = time.time()
    from state.types import Direction as _Direction

    LOGGER.debug(
        "Checking exits for %d open positions | up_price=%s | down_price=%s",
        len(open_positions),
        f"{up_price:.3f}" if up_price is not None else "-",
        f"{down_price:.3f}" if down_price is not None else "-",
    )

    for position in open_positions:
        seconds_elapsed = now - position.entry_ts
        exit_token_id = window.up_token_id if position.direction == "UP" else window.down_token_id
        exit_dir = _Direction.UP if position.direction == "UP" else _Direction.DOWN
        current_price = up_price if position.direction == "UP" else down_price

        exit_reason: ExitReason | None = None
        exit_price: float | None = None
        exit_fee_rate_bps: int = 0
        resting_fill = False  # True when a resting GTC order filled (no extra sell needed)
        filled_exit_result: OrderResult | None = None

        # ── PATH 1: Resting order fill check (live mode only) ──────────────────
        if not track.orderer.dry_run and (position.sl_order_id or position.tp_order_id):
            if position.sl_order_id:
                try:
                    sl_status = await asyncio.to_thread(
                        track.orderer.get_order_status, position.sl_order_id
                    )
                    if sl_status.status == OrderStatus.FILLED:
                        exit_reason = ExitReason.STOP_LOSS
                        exit_price = sl_status.avg_price or position.stop_loss_price
                        exit_fee_rate_bps = sl_status.fee_rate_bps
                        resting_fill = True
                        filled_exit_result = sl_status
                        if position.tp_order_id:
                            await asyncio.to_thread(track.orderer.cancel, position.tp_order_id)
                            position.tp_order_id = None
                except Exception as e:
                    LOGGER.warning("SL order status check failed for %s: %s", position.sl_order_id, e)

            if exit_reason is None and position.tp_order_id:
                try:
                    tp_status = await asyncio.to_thread(
                        track.orderer.get_order_status, position.tp_order_id
                    )
                    if tp_status.status == OrderStatus.FILLED:
                        exit_reason = ExitReason.TAKE_PROFIT
                        exit_price = tp_status.avg_price or position.take_profit_price
                        exit_fee_rate_bps = tp_status.fee_rate_bps
                        resting_fill = True
                        filled_exit_result = tp_status
                        if position.sl_order_id:
                            await asyncio.to_thread(track.orderer.cancel, position.sl_order_id)
                            position.sl_order_id = None
                except Exception as e:
                    LOGGER.warning("TP order status check failed for %s: %s", position.tp_order_id, e)

        # ── PATH 2: Price-polling exits (dry-run, or live if resting orders haven't fired) ──
        if exit_reason is None:
            if current_price is None:
                LOGGER.debug(
                    "No bid for %s %s — skipping polling exit check",
                    position.direction, position.order_id,
                )
                continue

            effective_sl_confirmation = compute_sl_confirmation_seconds(
                position.direction, model_context, SL_CONFIRMATION_SECONDS,
            )
            current_conviction = model_context.market_conviction if model_context is not None else 1.0
            partial_fraction = position.take_partial_profit(current_price)
            if partial_fraction > 0:
                should_exit, poll_reason = True, ExitReason.TAKE_PROFIT
                close_fraction = partial_fraction
            else:
                should_exit, poll_reason = position.should_exit(
                    current_price, seconds_elapsed,
                    sl_confirmation_seconds=effective_sl_confirmation,
                    sl_instant_breach_pct=SL_INSTANT_BREACH_PCT,
                    min_hold_seconds=MIN_HOLD_SECONDS,
                    time_extension_seconds=HIGH_CONVICTION_TIME_EXTENSION_SECONDS,
                    market_conviction=current_conviction,
                    trail_pct=EXIT_TRAIL_PCT,
                )
                close_fraction = 1.0

            LOGGER.debug(
                "Position %s (Trade #%d) | %s | Elapsed: %.0fs | Current: %.3f | "
                "TP: %s | SL: %.3f | ShouldExit: %s",
                position.order_id, position.trade_number, position.direction,
                seconds_elapsed, current_price,
                f"{position.take_profit_price:.3f}" if position.take_profit_price else "None",
                position.stop_loss_price, should_exit,
            )

            if not should_exit:
                continue

            exit_reason = poll_reason
            exit_price = current_price

        # ── Execute the exit ────────────────────────────────────────────────────
        try:
            LOGGER.info(
                "Exit triggered for Trade #%d | %s | Reason: %s | Entry: %.3f | Exit: %.3f | PnL: %.2f%%",
                position.trade_number, position.order_id, exit_reason.value,
                position.entry_price, exit_price,
                position.unrealized_pnl_pct(exit_price) * 100,
            )

            closed_notional_usdc = position.remaining_size_usdc * close_fraction
            if not resting_fill:
                # Cancel any resting orders before placing a new market sell
                if not track.orderer.dry_run:
                    for oid, label in (
                        (position.sl_order_id, "SL"),
                        (position.tp_order_id, "TP"),
                    ):
                        if oid:
                            try:
                                await asyncio.to_thread(track.orderer.cancel, oid)
                            except Exception as ce:
                                LOGGER.warning("Failed to cancel resting %s order %s: %s", label, oid, ce)
                    position.sl_order_id = None
                    position.tp_order_id = None

                exit_order = await asyncio.to_thread(
                        track.orderer.place_bet,
                        exit_token_id,
                        exit_dir,
                        position.remaining_size_usdc * close_fraction,
                        exit_price,
                    entry_regime="EXIT",
                    side="SELL",
                )
                if exit_order is None:
                    raise RuntimeError("Exit order placement returned None")
                if exit_order.order_id:
                    track.ledger.record_submission(
                        exit_order.order_id,
                        window.end_ts,
                        direction=position.direction,
                        amount_usdc=position.remaining_size_usdc,
                        submitted_price=exit_order.submitted_price or exit_price or position.entry_price,
                        market_price=exit_price or position.entry_price,
                        model="EXIT",
                        dry_run=track.orderer.dry_run,
                        role=position.role,
                        track=track.name,
                        phase="EXIT",
                        details={
                            "position_order_id": position.order_id,
                            "exit_reason": exit_reason.value,
                        },
                    )
                LOGGER.info(
                    "Exit order placed | Trade #%d | %s | %s | Size: $%.2f | Price: %.3f",
                    position.trade_number, exit_order.order_id,
                    position.direction, position.remaining_size_usdc, exit_price,
                )
                filled_exit_result = exit_order
                if exit_order.order_id and exit_order.status in {OrderStatus.SUBMITTED, OrderStatus.PENDING}:
                    filled_exit_result = await track.orderer.wait_for_fill(
                        exit_order.order_id,
                        _bounded_fill_deadline(window, timeout_seconds=15),
                    )
                if filled_exit_result.status == OrderStatus.CANCELLED:
                    track.ledger.record_cancellation(
                        filled_exit_result.order_id or position.order_id,
                        window.end_ts,
                        status=filled_exit_result.status.value,
                        details={
                            "phase": "EXIT",
                            "position_order_id": position.order_id,
                            "exit_reason": exit_reason.value,
                            "track": track.name,
                        },
                    )
                    if track.stats is not None:
                        track.stats.last_order_status = filled_exit_result.status.value
                        track.stats.last_order_id = filled_exit_result.order_id or ""
                        track.stats.note(
                            f"Exit {exit_reason.value} cancelled before fill; position kept open"
                        )
                    LOGGER.warning(
                        "Exit cancelled before fill | position=%s | exit_order=%s",
                        position.order_id,
                        filled_exit_result.order_id,
                    )
                    continue
                if filled_exit_result.status not in {OrderStatus.FILLED, OrderStatus.PARTIAL}:
                    raise RuntimeError(
                        f"Exit order {filled_exit_result.order_id} ended in unexpected state {filled_exit_result.status.value}"
                    )
                exit_price = (
                    filled_exit_result.avg_price
                    or filled_exit_result.submitted_price
                    or exit_price
                    or position.entry_price
                )
                exit_fee_rate_bps = filled_exit_result.fee_rate_bps
                closed_notional_usdc = _filled_amount_usdc(
                    filled_exit_result,
                    position.remaining_size_usdc,
                )
                close_fraction = min(
                    1.0,
                    closed_notional_usdc / max(position.remaining_size_usdc, 0.01),
                )

            if filled_exit_result is not None and filled_exit_result.order_id:
                track.ledger.record_fill(
                    filled_exit_result.order_id,
                    window.end_ts,
                    filled_exit_result.status.value,
                    filled_size=filled_exit_result.filled_size,
                    avg_price=exit_price or position.entry_price,
                    details={
                        "phase": "EXIT",
                        "position_order_id": position.order_id,
                        "exit_reason": exit_reason.value,
                        "track": track.name,
                        "closed_amount_usdc": closed_notional_usdc,
                    },
                )

            fee_usdc = estimate_trade_fee_usdc(
                asset=asset,
                market_price=exit_price,
                bet_size=closed_notional_usdc,
            )
            realized_pnl = position.close(
                exit_price=exit_price,
                exit_ts=now,
                reason=exit_reason,
                fee_usdc=fee_usdc,
                partial_pct=close_fraction,
            )

            track.state.realized_pnl += realized_pnl
            track.state.daily_pnl += realized_pnl
            if close_fraction >= 0.999:
                track.ledger.record_early_exit(
                    order_id=position.order_id,
                    window_end_ts=window.end_ts,
                    pnl=realized_pnl + fee_usdc,
                    exit_price=exit_price,
                    reason=exit_reason.value,
                )
                track.state.record_early_exit(
                    order_id=position.order_id,
                    pnl=realized_pnl,
                    won=realized_pnl > 0,
                )
            else:
                track.state.update_trade_amount(
                    position.order_id,
                    position.remaining_size_usdc,
                )

            if track.stats is not None:
                if close_fraction >= 0.999:
                    track.stats.last_resolution = "WIN" if realized_pnl > 0 else "LOSS"
                track.stats.note(
                    f"Exit {exit_reason.value} | {'full' if close_fraction >= 0.999 else 'partial'} | "
                    f"PnL: ${realized_pnl:+.2f} | "
                    f"{position.direction} @ {exit_price:.3f}"
                )

            if close_fraction >= 0.999:
                closed_positions.append(position)

        except Exception as e:
            import traceback
            LOGGER.error("Failed to exit position %s: %s", position.order_id, e)
            LOGGER.error("Exit error traceback: %s", traceback.format_exc())
            emit_alert("ERROR", f"Exit failed for {position.order_id}", error=str(e))
            LOGGER.warning(
                "Leaving local position open after exit failure | position=%s",
                position.order_id,
            )

    if closed_positions and window:
        open_count = len(window.get_open_positions())
        max_pos = MAX_POSITIONS_PER_WINDOW
        LOGGER.info(
            "Position(s) closed | Can re-enter: %d/%d slots available | Open positions: %d",
            max_pos - open_count, max_pos, open_count,
        )

    return closed_positions


async def place_resting_exits(
    track: ModelTrack,
    *,
    window: WindowState,
    position: "Position",
    asset: str,
) -> None:
    """Place GTC resting sell orders at SL and TP prices immediately after entry.

    Live mode only — in dry-run the polling path handles exits.
    These orders sit on the CLOB and fill automatically at the exact target price,
    eliminating polling slippage entirely for SL and TP.
    """
    if track.orderer.dry_run:
        return

    from state.types import Direction as _Direction
    token_id = window.up_token_id if position.direction == "UP" else window.down_token_id
    direction = _Direction.UP if position.direction == "UP" else _Direction.DOWN

    if position.stop_loss_price is not None:
        try:
            sl_order = await asyncio.to_thread(
                track.orderer.place_resting_sell,
                token_id,
                direction,
                position.remaining_size_usdc,
                position.stop_loss_price,
            )
            position.sl_order_id = sl_order.order_id
            LOGGER.info(
                "Resting SL order placed | %s | Price: %.3f | ID: %s",
                position.order_id, position.stop_loss_price, sl_order.order_id,
            )
        except Exception as e:
            LOGGER.warning("Failed to place resting SL order for %s: %s", position.order_id, e)

    if position.take_profit_price is not None:
        try:
            tp_order = await asyncio.to_thread(
                track.orderer.place_resting_sell,
                token_id,
                direction,
                position.remaining_size_usdc,
                position.take_profit_price,
            )
            position.tp_order_id = tp_order.order_id
            LOGGER.info(
                "Resting TP order placed | %s | Price: %.3f | ID: %s",
                position.order_id, position.take_profit_price, tp_order.order_id,
            )
        except Exception as e:
            LOGGER.warning("Failed to place resting TP order for %s: %s", position.order_id, e)


def check_signal_reversal(
    current_signal_direction: str,
    window: WindowState,
) -> list[Position]:
    """Check if any open positions should be exited due to signal reversal."""
    if not EXIT_REVERSE_SIGNAL:
        return []
    
    reversed_positions = []
    for position in window.get_open_positions():
        if position.direction != current_signal_direction:
            reversed_positions.append(position)
    
    return reversed_positions


def extract_market_slug(market_url: str) -> str:
    if not market_url:
        return ""
    return market_url.rstrip("/").rsplit("/", 1)[-1]


def force_interval_trades_enabled(*, live_mode: bool) -> bool:
    return LIVE_FORCE_INTERVAL_TRADES if live_mode else FORCE_INTERVAL_TRADES


def hedge_enabled_for_mode(*, live_mode: bool) -> bool:
    return LIVE_HEDGE_ENABLED if live_mode else HEDGE_ENABLED


def kill_switch_engaged(*, live_mode: bool) -> bool:
    if not live_mode or not LIVE_KILL_SWITCH_FILE.strip():
        return False
    return Path(LIVE_KILL_SWITCH_FILE).exists()


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)


def score_signal(signal) -> tuple[float, float, float]:
    return _score_signal(signal)


def choose_better_signal(best_signal, candidate_signal):
    return _choose_better_signal(best_signal, candidate_signal)


def should_fire_snipe_signal(
    candidate_signal,
    *,
    best_signal,
    previous_signal,
    seconds_remaining: int,
    force_trade: bool,
    liquidity_score: float,
    net_expected_profit_usdc: float,
    min_net_profit_required: float = MIN_NET_EXPECTED_PROFIT_USDC,
) -> bool:
    return _should_fire_snipe_signal(
        candidate_signal,
        best_signal=best_signal,
        previous_signal=previous_signal,
        seconds_remaining=seconds_remaining,
        force_trade=force_trade,
        liquidity_score=liquidity_score,
        net_expected_profit_usdc=net_expected_profit_usdc,
        min_net_profit_required=min_net_profit_required,
    )


def perform_live_preflight(client, requested_bankroll: float) -> LivePreflightResult:
    return _perform_live_preflight(
        client,
        requested_bankroll,
        live_startup_cancel_all=LIVE_STARTUP_CANCEL_ALL,
        live_require_empty_open_orders=LIVE_REQUIRE_EMPTY_OPEN_ORDERS,
        live_min_available_usdc=LIVE_MIN_AVAILABLE_USDC,
        live_balance_buffer_usdc=LIVE_BALANCE_BUFFER_USDC,
        min_bet_usdc=MIN_BET_USDC,
    )


def reconcile_live_ledger(client, ledger: Ledger) -> LiveReconciliationResult:
    return _reconcile_live_ledger(
        client,
        ledger,
        live_recon_lookback_seconds=LIVE_RECON_LOOKBACK_SECONDS,
        market_slug_extractor=extract_market_slug,
    )


def reconcile_open_order_intents(client, ledger: Ledger, *, now_ts: int) -> IntentReconciliationResult:
    return _reconcile_open_order_intents(
        client,
        ledger,
        now=now_ts,
        lookback_seconds=LIVE_RECON_LOOKBACK_SECONDS,
        abort_grace_seconds=INTENT_RECON_ABORT_GRACE_SECONDS,
    )


async def wait_for_market_resolution(
    market_client,
    asset: str,
    market_slug: str,
    window_end_ts: int,
    *,
    prefer_binance: bool,
    timeout_seconds: int = 180,
    poll_interval: float = 5.0,
):
    return await _wait_for_market_resolution(
        market_client,
        asset,
        market_slug,
        window_end_ts,
        prefer_binance=prefer_binance,
        resolve_market_outcome_fn=resolve_market_outcome,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        logger=LOGGER,
    )


def sweep_unresolved_trades_once(
    track: ModelTrack,
    market_client,
    *,
    now_ts: int | None = None,
) -> int:
    return _sweep_unresolved_trades_once(
        track,
        market_client,
        target_asset=TARGET_ASSET,
        extract_market_slug_fn=extract_market_slug,
        resolve_market_outcome_fn=resolve_market_outcome,
        is_demo_client_fn=lambda client: isinstance(client, DemoClient),
        now_ts=now_ts,
        logger=LOGGER,
    )


@dataclass
class CliArgs:
    live: bool
    shadow_live: bool
    asset: str
    model: str
    models: tuple[str, ...]
    yes_live: bool
    debug: bool
    demo: bool
    demo_local: bool
    once: bool
    duration: int | None
    dashboard: bool | None
    bankroll: float | None


def parse_args() -> CliArgs:
    parser = argparse.ArgumentParser()
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true")
    mode_group.add_argument(
        "--shadow-live",
        action="store_true",
        help="Use real Binance and Polymarket public data with dry-run orders",
    )
    mode_group.add_argument(
        "--demo",
        action="store_true",
        help="Run a bounded dry-run demo that prefers live public data and falls back to local simulation",
    )
    mode_group.add_argument(
        "--demo-local",
        action="store_true",
        help="Force local simulated prices and demo markets for a fast dry-run walkthrough",
    )
    parser.add_argument("--model", default="A", choices=["A", "B", "C"])
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model list to run side by side, for example A,B,C",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Skip the interactive live trading confirmation prompt",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run a single trading iteration")
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Maximum runtime in seconds before stopping cleanly",
    )
    parser.add_argument(
        "--asset",
        default=TARGET_ASSET,
        help="Target asset symbol, for example BTC, ETH, or POL",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Starting bankroll per model in USDC",
    )
    dashboard_group = parser.add_mutually_exclusive_group()
    dashboard_group.add_argument(
        "--dashboard",
        dest="dashboard",
        action="store_true",
        help="Show a live terminal dashboard while the bot runs",
    )
    dashboard_group.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Disable the live terminal dashboard",
    )
    parser.set_defaults(dashboard=None)
    parsed = parser.parse_args()
    try:
        models = parse_model_names(parsed.model, parsed.models)
    except ValueError as exc:
        parser.error(str(exc))
    return CliArgs(
        live=parsed.live,
        shadow_live=parsed.shadow_live,
        asset=parsed.asset.upper(),
        model=parsed.model,
        models=models,
        yes_live=parsed.yes_live,
        debug=parsed.debug,
        demo=parsed.demo,
        demo_local=parsed.demo_local,
        once=parsed.once,
        duration=parsed.duration,
        dashboard=parsed.dashboard,
        bankroll=parsed.bankroll,
    )


def parse_model_names(default_model: str, raw_models: str | None) -> tuple[str, ...]:
    if raw_models is None:
        return (default_model.upper(),)

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_models.split(","):
        name = item.strip().upper()
        if not name:
            continue
        if name not in {"A", "B", "C"}:
            raise ValueError(f"Unsupported model {name}. Supported models: A, B, C")
        if name in seen:
            continue
        seen.add(name)
        models.append(name)

    if not models:
        raise ValueError("No valid models supplied. Use --models A,B,C")
    return tuple(models)


@dataclass
class RuntimeStats:
    mode_label: str
    asset: str
    model: str
    started_ts: float = field(default_factory=time.time)
    market_source: str = ""
    ledger_path: str = ""
    last_event: str = "Starting"
    last_event_ts: float = field(default_factory=time.time)
    window_slug: str = ""
    window_phase: str = "-"
    seconds_remaining: int | None = None
    open_price: float | None = None
    current_up_ask: float | None = None
    current_down_ask: float | None = None
    current_up_bid: float | None = None
    current_down_bid: float | None = None
    last_strongest_side: str = "NONE"
    last_regime_candidate: str = "NONE"
    last_signal_direction: str = "-"
    last_signal_edge: float | None = None
    last_signal_theo: float | None = None
    last_signal_market_price: float | None = None
    last_gross_expected_profit_usdc: float | None = None
    last_estimated_cost_usdc: float | None = None
    last_bet_size: float | None = None
    last_order_status: str = "-"
    last_order_id: str = ""
    last_resolution: str = "-"
    last_market_slug: str = ""
    last_no_trade_reason: str = ""
    last_liquidity_score: float | None = None
    last_net_expected_profit_usdc: float | None = None
    last_book_spread: float | None = None
    last_hedge_decision: str = ""
    last_update_ts: float = field(default_factory=time.time)

    def note(self, message: str) -> None:
        self.last_event = message
        self.last_event_ts = time.time()
        self.last_update_ts = self.last_event_ts

    def update_window(self, window: WindowState | None, *, phase: WindowPhase | None = None) -> None:
        self.last_update_ts = time.time()
        if window is None:
            self.window_slug = ""
            self.window_phase = "-"
            self.seconds_remaining = None
            self.open_price = None
            return
        self.window_slug = extract_market_slug(window.market_url)
        self.open_price = window.open_price or None
        now = int(time.time())
        active_phase = phase or window.get_phase(now)
        self.window_phase = active_phase.value
        self.seconds_remaining = window.seconds_remaining(now)


@dataclass
class ModelRuntime:
    model: str
    state: BotState
    finder: MarketFinder
    orderer: Orderer
    guard: DailyGuard
    stats: RuntimeStats


@dataclass
class SubmittedTrade:
    role: str
    signal: object
    amount_usdc: float
    order_id: str
    submitted_price: float = 0.0
    fee_rate_bps: int = 0


@dataclass
class SnipeSelection:
    signal: object | None
    up_price: float | None
    down_price: float | None
    volatility: float
    seconds_remaining: int
    model_context: ModelContext | None
    strongest_side: str = "NONE"
    regime_candidate: str = "NONE"
    up_book: object | None = None
    down_book: object | None = None
    reason_code: str = ""
    reason_detail: str = ""
    liquidity_score: float = 0.0
    book_spread: float | None = None
    net_expected_profit_usdc: float = 0.0
    gross_expected_profit_usdc: float = 0.0
    estimated_cost_usdc: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)


def format_runtime(total_seconds: float) -> str:
    total = max(0, int(total_seconds))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_price(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"${value:.{digits}f}"


def format_prob(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def format_book_snapshot(label: str, snapshot) -> str:
    if snapshot is None:
        return f"{label} a-/b- ask=- bid=-"
    ask = "-" if snapshot.best_ask is None else f"{snapshot.best_ask:.3f}"
    bid = "-" if snapshot.best_bid is None else f"{snapshot.best_bid:.3f}"
    return f"{label} a{snapshot.ask_count}/b{snapshot.bid_count} ask={ask} bid={bid}"


def book_price_spread(snapshot: BookSnapshot | None) -> float | None:
    return _book_price_spread(snapshot)


def assess_book_quality(
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    *,
    strongest_side: str = "NONE",
    market_conviction: float = 0.0,
) -> BookAssessment:
    return _assess_book_quality_for_regime(
        up_book,
        down_book,
        strongest_side=strongest_side,
        market_conviction=market_conviction,
    )


def should_enter_on_waiting_conviction(
    *,
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    state: BotState,
    window: WindowState,
) -> tuple[bool, str]:
    return _should_enter_on_waiting_conviction(
        up_book=up_book,
        down_book=down_book,
        state=state,
        window=window,
    )


def estimate_trade_fee_usdc(*, asset: str, market_price: float, bet_size: float, fee_rate_bps: int = 0) -> float:
    return _estimate_trade_fee_usdc(
        asset=asset,
        market_price=market_price,
        bet_size=bet_size,
        fee_rate_bps=fee_rate_bps,
    )


def evaluate_trade_economics(
    *,
    asset: str,
    signal,
    state: BotState,
    up_book: BookSnapshot | None,
    down_book: BookSnapshot | None,
    liquidity_score: float | None = None,
) -> tuple[float, float, float, float, float, float]:
    return _evaluate_trade_economics(
        asset=asset,
        signal=signal,
        state=state,
        up_book=up_book,
        down_book=down_book,
        liquidity_score=liquidity_score,
        logger=LOGGER,
    )


def format_age(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, rem = divmod(int(seconds), 60)
    return f"{minutes}m {rem}s"


def fit_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return f"{text[:width - 3]}..."


def clone_window(window: WindowState | None) -> WindowState | None:
    if window is None:
        return None
    new_window = WindowState(
        start_ts=window.start_ts,
        end_ts=window.end_ts,
        market_id=window.market_id,
        up_token_id=window.up_token_id,
        down_token_id=window.down_token_id,
        open_price=window.open_price,
        traded=window.traded,
        order_id=window.order_id,
        market_url=window.market_url,
    )
    # Copy positions to cloned window (deep copy to avoid shared references)
    if window.positions:
        for pos in window.positions.positions:
            new_window.positions.positions.append(copy.deepcopy(pos))
    return new_window


def apply_price(
    states: list[BotState],
    price: float,
    *,
    updated_ts: float | None = None,
    source: str | None = None,
) -> None:
    ts = time.time() if updated_ts is None else updated_ts
    for state in states:
        state.update_price(price, updated_ts=ts, source=source)


def build_model_runtimes(
    *,
    models: tuple[str, ...],
    client,
    asset: str,
    mode_label: str,
    market_source: str,
    ledger_path: str,
    dry_run: bool,
    initial_price: float = 0.0,
    initial_price_updated_ts: float = 0.0,
    initial_window: WindowState | None = None,
    finder: MarketFinder | None = None,
    starting_bankroll: float = STARTING_BANKROLL_USDC,
) -> list[ModelRuntime]:
    runtimes: list[ModelRuntime] = []
    for model in models:
        state = BotState(
            btc_price=initial_price,
            btc_price_updated_ts=initial_price_updated_ts,
            starting_bankroll_usdc=starting_bankroll,
            current_window=clone_window(initial_window),
        )
        stats = RuntimeStats(
            mode_label=mode_label,
            asset=asset,
            model=model,
            market_source=market_source,
            ledger_path=ledger_path,
        )
        if initial_price_updated_ts:
            stats.note(f"Loaded initial {asset} snapshot")
        runtimes.append(
            ModelRuntime(
                model=model,
                state=state,
                finder=finder if finder is not None else MarketFinder(client, asset=asset),
                orderer=Orderer(client, dry_run=dry_run),
                guard=DailyGuard(state),
                stats=stats,
            )
        )
    return runtimes


# All three tracks run the LATE_CONVICTION regime with a different probability
# engine (A = pure momentum, B = brownian+microstructure, C = ledger-calibrated).
# Mispricing entries were dropped: external-feed latency makes them untradeable.
# Track keys are legacy ids kept for ledger/status compat; display labels live
# in webapp/static/index.html (TRACK_LABELS).
_TRACK_CONFIGS = [
    ("CONVICTION_ONLY", {"LATE_CONVICTION"}, "A"),
    ("MISPRICING_ONLY", {"LATE_CONVICTION"}, "B"),
    ("BOTH",            {"LATE_CONVICTION"}, "C"),
]

_TRACK_LEDGER_FILES = {
    "CONVICTION_ONLY": LEDGER_FILE_CONVICTION,
    "MISPRICING_ONLY": LEDGER_FILE_MISPRICING,
    "BOTH":            LEDGER_FILE_BOTH,
}


def build_model_tracks(
    *,
    client,
    asset: str,
    mode_label: str,
    market_source: str,
    dry_run: bool,
    initial_price: float = 0.0,
    initial_price_updated_ts: float = 0.0,
    initial_window: WindowState | None = None,
    starting_bankroll: float = STARTING_BANKROLL_USDC,
) -> list[ModelTrack]:
    tracks = []
    shadow_all_active = dry_run and SHADOW_MULTI_TRACK_EXECUTION
    for name, regime_filter, model in _TRACK_CONFIGS:
        ledger_path = _TRACK_LEDGER_FILES[name]
        state = BotState(
            btc_price=initial_price,
            btc_price_updated_ts=initial_price_updated_ts,
            current_window=clone_window(initial_window),
            starting_bankroll_usdc=starting_bankroll,
        )
        stats = RuntimeStats(model=model, mode_label=mode_label, asset=asset, market_source=market_source)
        ledger = Ledger(ledger_path)
        guard = DailyGuard(state)
        finder = MarketFinder(client, asset=asset)
        active = shadow_all_active or (name == ACTIVE_MODEL_TRACK)
        track_dry_run = dry_run or not active
        orderer = Orderer(client, dry_run=track_dry_run)
        tracks.append(ModelTrack(
            name=name,
            regime_filter=regime_filter,
            model=model,
            state=state,
            stats=stats,
            ledger=ledger,
            guard=guard,
            orderer=orderer,
            finder=finder,
            ansi_color=TRACK_COLORS[name],
            css_color=TRACK_CSS_COLORS[name],
            active=active,
        ))
    return tracks


def compute_all_engine_scores(
    shared_state: BotState,
    window: WindowState,
) -> dict[str, float]:
    """Compute probability scores for models A, B, C using shared market state.

    Called once per tick; the returned dict is shared across all model tracks.
    """
    open_price = window.open_price
    current_price = shared_state.btc_price
    seconds_remaining = window.seconds_remaining(int(time.time()))

    try:
        volatility = shared_state.rolling_volatility()
    except Exception:
        volatility = 0.0

    scores: dict[str, float] = {}
    for letter in ("A", "B", "C", "P"):
        try:
            prob = get_probability(
                open_price=open_price,
                current_price=current_price,
                seconds_remaining=seconds_remaining,
                volatility=volatility,
                model=letter,
            )
            scores[letter] = round(prob, 4)
        except Exception:
            scores[letter] = 0.5
    return scores


def compute_candidate_probabilities(
    *,
    tracks: list[ModelTrack],
    window: WindowState,
    state: BotState,
    seconds_remaining: int,
    volatility: float,
    model_context: ModelContext | None,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    ledger_paths = {track.model: track.ledger.filepath for track in tracks}
    for letter in ("A", "B", "C", "P"):
        try:
            scores[letter] = round(
                get_probability(
                    open_price=window.open_price,
                    current_price=state.btc_price,
                    seconds_remaining=seconds_remaining,
                    volatility=volatility,
                    model=letter,
                    ledger_path=ledger_paths.get(letter),
                    calibration_path=MODEL_C_CALIBRATION_FILE,
                    context=model_context,
                    decision_ts=window.end_ts - seconds_remaining,
                ),
                4,
            )
        except Exception:
            scores[letter] = 0.5
    return scores


async def _evaluate_track(
    track: ModelTrack,
    selection: SnipeSelection | None,
    engine_scores: dict[str, float],
    *,
    window: WindowState,
    asset: str,
    up_book: object | None = None,
    down_book: object | None = None,
) -> None:
    """Apply regime filter and independently decide entry/exit for one track."""
    logger = logging.getLogger(f"bot.{track.name.lower()}")

    # --- EXITS: always run, regardless of cooldown ---
    track_window = track.state.current_window
    if track_window is not None and track_window.has_open_positions():
        up_price = up_book.best_bid if up_book and up_book.best_bid else None
        down_price = down_book.best_bid if down_book and down_book.best_bid else None
        if up_price is not None or down_price is not None:
            closed = await check_position_exits(
                track,
                asset=asset,
                window=track_window,
                up_price=up_price or 0.0,
                down_price=down_price or 0.0,
                model_context=selection.model_context if selection is not None else None,
            )
            for pos in closed:
                prev = track.pnl_history[-1] if track.pnl_history else 0.0
                track.pnl_history.append(round(prev + pos.pnl_usdc, 4))
            if closed and track.stats is not None:
                total_pnl = sum(p.pnl_usdc for p in closed)
                track.stats.note(f"Closed {len(closed)} positions | PnL: ${total_pnl:+.2f}")

    # --- ENTRIES: gated by active flag, regime filter + cooldown ---
    # Non-active tracks shadow the market but never place orders.
    # Always mirror market signal state onto this track's stats so the dashboard
    # shows live signal info on all panels, not just the BOTH track.
    if track.stats is not None and selection is not None:
        track.stats.last_strongest_side = selection.strongest_side or "NONE"
        track.stats.last_liquidity_score = getattr(selection, "liquidity_score", None)
        track.stats.last_book_spread = getattr(selection, "book_spread", None)
        track.stats.current_up_ask = getattr(selection, "up_price", None)
        track.stats.current_down_ask = getattr(selection, "down_price", None)

    if selection is None or selection.signal is None:
        no_trade_reason = "no signal" if selection is None else (selection.reason_code or "no signal")
        if track.stats is not None:
            track.stats.last_signal_direction = "-"
            track.stats.last_regime_candidate = "NONE" if selection is None else selection.regime_candidate
            track.stats.last_no_trade_reason = no_trade_reason
        logger.info("NO TRADE     %s", no_trade_reason)
        return

    signal = selection.signal
    # Always show the market regime candidate on this track's stats panel
    if track.stats is not None:
        track.stats.last_regime_candidate = signal.entry_regime
        track.stats.last_signal_direction = signal.direction.value
        track.stats.last_signal_edge = signal.edge
        track.stats.last_signal_theo = getattr(signal, "theo_prob", None)
        track.stats.last_signal_market_price = signal.market_price

    reason = _track_entry_skip_reason(track, selection, window=window)
    if reason:
        if track.stats is not None:
            track.stats.last_no_trade_reason = reason
        if (
            reason.startswith("regime=")
            or reason.startswith("inactive track")
            or reason.startswith("high-conv blocked")
        ):
            logger.info("NO TRADE     %s", reason)
        else:
            logger.info("SKIP         %s", reason)
        return

    logger.info(
        "SIGNAL %s  regime=%s | conf=%.2f | A:%.2f B:%.2f C:%.2f P:%.2f",
        "▲ UP" if signal.direction.value == "UP" else "▼ DN",
        signal.entry_regime,
        getattr(signal, "confidence", 0.0),
        (selection.probabilities or engine_scores).get("A", 0.0),
        (selection.probabilities or engine_scores).get("B", 0.0),
        (selection.probabilities or engine_scores).get("C", 0.0),
        (selection.probabilities or engine_scores).get("P", 0.0),
    )

    # Compute bet size — same call pattern as the existing callsite
    available_bankroll = track.state.available_bankroll_usdc()
    current_bankroll = track.state.current_bankroll_usdc()
    primary_target_bet = get_bet_size(
        signal.edge,
        signal.market_price,
        available_bankroll,
        current_bankroll=current_bankroll,
        starting_bankroll=track.state.starting_bankroll_usdc,
        consecutive_wins=track.state.consecutive_wins,
        consecutive_losses=track.state.consecutive_losses,
        entry_regime=getattr(signal, "entry_regime", "MISPRICING"),
        liquidity_score=getattr(selection, "liquidity_score", 0.0),
        net_expected_profit_usdc=getattr(selection, "net_expected_profit_usdc", None),
        same_side_exposure_usdc=track.state.open_directional_exposure_usdc(signal.direction.value),
        total_open_exposure_usdc=track.state.open_window_exposure_usdc(),
    )
    if signal.direction.value == "UP":
        hedge_price = getattr(selection, "down_price", None)
    else:
        hedge_price = getattr(selection, "up_price", None)
    sizes = plan_position_sizes(
        primary_target_bet,
        available_bankroll,
        hedge_enabled=(
            HEDGE_ENABLED
            and (
                getattr(signal, "entry_regime", "") != "LATE_CONVICTION"
                or getattr(selection, "seconds_remaining", 0) <= ENTRY_WINDOW_START
            )
        ),
        hedge_fraction=HEDGE_FRACTION,
        hedge_price=hedge_price,
    )
    amount_usdc = sizes.primary_usdc
    await submit_trade(
        track,
        window=window,
        signal=signal,
        amount_usdc=amount_usdc,
        role="PRIMARY",
        asset=asset,
    )


def format_model_signal(stats: RuntimeStats) -> str:
    if stats.last_signal_direction == "-" or stats.last_signal_edge is None:
        return "-"
    return f"{stats.last_signal_direction} e{stats.last_signal_edge:.3f} p{stats.last_signal_market_price:.3f}"


def format_model_order(stats: RuntimeStats) -> str:
    if stats.last_order_status == "-":
        return "-"
    if stats.last_bet_size is None:
        return stats.last_order_status
    return f"{stats.last_order_status} {stats.last_bet_size:.2f}"


def format_model_result(state: BotState, stats: RuntimeStats) -> str:
    unresolved = state.unresolved_primary_count()
    if stats.last_resolution != "-" and stats.last_resolution != "PENDING":
        return stats.last_resolution
    if unresolved:
        return f"PEND {unresolved}"
    return stats.last_resolution


def format_model_risk(guard: DailyGuard) -> str:
    can_enter, reason = guard.can_enter()
    return "READY" if can_enter else reason


def render_multi_dashboard(runtimes: list[ModelRuntime]) -> str:
    primary = runtimes[0]
    now = time.time()
    width = max(108, shutil.get_terminal_size((140, 40)).columns)
    divider = "=" * width
    sub_divider = "-" * width
    started_ts = min(runtime.stats.started_ts for runtime in runtimes)
    price_age = (
        format_age(now - primary.state.btc_price_updated_ts)
        if primary.state.btc_price_updated_ts
        else "n/a"
    )
    active_slug = next((runtime.stats.window_slug for runtime in runtimes if runtime.stats.window_slug), "-")
    active_phase = next((runtime.stats.window_phase for runtime in runtimes if runtime.stats.window_phase != "-"), "-")
    active_remaining = next(
        (
            runtime.stats.seconds_remaining
            for runtime in runtimes
            if runtime.stats.seconds_remaining is not None
        ),
        None,
    )
    header = (
        f"Polymarket {primary.stats.asset} 5-Min Bot | {primary.stats.mode_label} | "
        f"Models {','.join(runtime.model for runtime in runtimes)} | "
        f"Runtime {format_runtime(now - started_ts)}"
    )
    rows = [
        divider,
        fit_text(header, width),
        divider,
        fit_text(
            "Market    "
            f"{active_slug} | Phase {active_phase} | "
            f"T-{active_remaining if active_remaining is not None else '-'}s | "
            f"Price {format_price(primary.state.btc_price if primary.state.btc_price_updated_ts else None)} | "
            f"Age {price_age} | Source {primary.state.price_source or '-'}",
            width,
        ),
        fit_text(
            "Source    "
            f"{primary.stats.market_source or '-'} | Ledger {primary.stats.ledger_path or '-'}",
            width,
        ),
        sub_divider,
        fit_text(
            "Model Phase       T-   Signal               Order            Result   Trades  W/L   PnL        Bank       Avail      Risk",
            width,
        ),
        sub_divider,
    ]
    for runtime in runtimes:
        state = runtime.state
        stats = runtime.stats
        row = (
            f"{runtime.model:<5}"
            f"{fit_text(stats.window_phase, 12)} "
            f"{str(stats.seconds_remaining if stats.seconds_remaining is not None else '-'):>4} "
            f"{fit_text(format_model_signal(stats), 20)} "
            f"{fit_text(format_model_order(stats), 16)} "
            f"{fit_text(format_model_result(state, stats), 8)} "
            f"{state.total_trades:>6} "
            f"{f'{state.wins}/{state.losses}':>5} "
            f"{format_price(state.daily_pnl):>10} "
            f"{format_price(state.current_bankroll_usdc()):>10} "
            f"{format_price(state.available_bankroll_usdc()):>10} "
            f"{fit_text(format_model_risk(runtime.guard), 28)}"
        )
        rows.append(fit_text(row, width))
    rows.append(sub_divider)
    for runtime in runtimes:
        rows.append(
            fit_text(
                f"{runtime.model} Event  {runtime.stats.last_event} | "
                f"Updated {time.strftime('%H:%M:%S', time.localtime(runtime.stats.last_event_ts))}",
                width,
            )
        )
    rows.append(divider)
    return "\n".join(rows)


def render_dashboard(state: BotState, guard: DailyGuard, stats: RuntimeStats) -> str:
    now = time.time()
    width = max(88, shutil.get_terminal_size((120, 40)).columns)
    divider = "=" * width
    sub_divider = "-" * width
    unresolved = state.unresolved_primary_count()
    can_enter, guard_reason = guard.can_enter()
    guard_status = "READY" if can_enter else guard_reason
    price_age = (
        format_age(now - state.btc_price_updated_ts)
        if state.btc_price_updated_ts
        else "n/a"
    )
    signal_summary = (
        f"{stats.last_signal_direction} | edge {format_prob(stats.last_signal_edge)} | "
        f"theo {format_prob(stats.last_signal_theo)} | mkt {format_prob(stats.last_signal_market_price)}"
    )
    header = (
        f"Polymarket {stats.asset} 5-Min Bot | {stats.mode_label} | "
        f"Model {stats.model} | Runtime {format_runtime(now - stats.started_ts)}"
    )
    rows = [
        divider,
        fit_text(header, width),
        divider,
        fit_text(
            "Market    "
            f"{stats.window_slug or '-'} | Phase {stats.window_phase} | "
            f"T-{stats.seconds_remaining if stats.seconds_remaining is not None else '-'}s",
            width,
        ),
        fit_text(
            "Price     "
            f"{format_price(state.btc_price if state.btc_price_updated_ts else None)} | "
            f"Age {price_age} | Source {state.price_source or '-'} | "
            f"Open {format_price(stats.open_price)}",
            width,
        ),
        fit_text(
            "Orderbook "
            f"UP {format_prob(stats.current_up_ask)} | DOWN {format_prob(stats.current_down_ask)}",
            width,
        ),
        fit_text(f"Signal    {signal_summary}", width),
        fit_text(
            "Order     "
            f"{stats.last_order_status} | Bet {format_price(stats.last_bet_size)} | "
            f"ID {stats.last_order_id or '-'}",
            width,
        ),
        fit_text(
            "Resolve   "
            f"{stats.last_resolution} | Source {stats.market_source or '-'} | "
            f"Ledger {stats.ledger_path or '-'}",
            width,
        ),
        sub_divider,
        fit_text(
            "Stats     "
            f"Trades {state.total_trades} | Resolved {state.wins + state.losses} | "
            f"W {state.wins} | L {state.losses} | Pending {unresolved} | "
            f"Void {state.cancelled_primary_count()} | "
            f"PnL {format_price(state.daily_pnl)}",
            width,
        ),
        fit_text(
            "Bank      "
            f"Start {format_price(state.starting_bankroll_usdc)} | "
            f"Current {format_price(state.current_bankroll_usdc())} | "
            f"Available {format_price(state.available_bankroll_usdc())} | "
            f"Reserved {format_price(state.pending_exposure_usdc())}",
            width,
        ),
        fit_text(
            "Risk      "
            f"{guard_status} | Daily trades {state.daily_trades} | "
            f"Win streak {state.consecutive_wins} | Loss streak {state.consecutive_losses}",
            width,
        ),
        fit_text(
            "Event     "
            f"{stats.last_event} | Updated {time.strftime('%H:%M:%S', time.localtime(stats.last_event_ts))}",
            width,
        ),
        divider,
    ]
    return "\n".join(rows)


def render_runtime_dashboard(runtimes: list[ModelRuntime]) -> str:
    if len(runtimes) == 1:
        runtime = runtimes[0]
        return render_dashboard(runtime.state, runtime.guard, runtime.stats)
    return render_multi_dashboard(runtimes)


def _build_runtime_status_legacy(runtimes: list[ModelRuntime]) -> dict:
    now = time.time()
    primary = runtimes[0]
    primary_state = primary.state
    price_age_seconds = (
        max(0.0, now - primary_state.btc_price_updated_ts)
        if primary_state.btc_price_updated_ts
        else None
    )
    return {
        "generated_ts": now,
        "mode": primary.stats.mode_label,
        "asset": primary.stats.asset,
        "price": {
            "current": primary_state.btc_price if primary_state.btc_price_updated_ts else None,
            "updated_ts": primary_state.btc_price_updated_ts or None,
            "age_seconds": price_age_seconds,
            "source": primary_state.price_source or None,
        },
        "market": {
            "slug": next(
                (runtime.stats.window_slug for runtime in runtimes if runtime.stats.window_slug),
                "",
            ),
            "phase": next(
                (runtime.stats.window_phase for runtime in runtimes if runtime.stats.window_phase != "-"),
                "-",
            ),
            "seconds_remaining": next(
                (
                    runtime.stats.seconds_remaining
                    for runtime in runtimes
                    if runtime.stats.seconds_remaining is not None
                ),
                None,
            ),
            "up_ask": next(
                (
                    runtime.stats.current_up_ask
                    for runtime in runtimes
                    if runtime.stats.current_up_ask is not None
                ),
                None,
            ),
            "down_ask": next(
                (
                    runtime.stats.current_down_ask
                    for runtime in runtimes
                    if runtime.stats.current_down_ask is not None
                ),
                None,
            ),
            "up_bid": next(
                (
                    runtime.stats.current_up_bid
                    for runtime in runtimes
                    if runtime.stats.current_up_bid is not None
                ),
                None,
            ),
            "down_bid": next(
                (
                    runtime.stats.current_down_bid
                    for runtime in runtimes
                    if runtime.stats.current_down_bid is not None
                ),
                None,
            ),
        },
        "models": [
            {
                "model": runtime.model,
                "last_event": runtime.stats.last_event,
                "last_event_ts": runtime.stats.last_event_ts,
                "window_phase": runtime.stats.window_phase,
                "seconds_remaining": runtime.stats.seconds_remaining,
                "last_signal_direction": runtime.stats.last_signal_direction,
                "last_strongest_side": runtime.stats.last_strongest_side,
                "last_regime_candidate": runtime.stats.last_regime_candidate,
                "last_signal_edge": runtime.stats.last_signal_edge,
                "last_signal_theo": runtime.stats.last_signal_theo,
                "last_signal_market_price": runtime.stats.last_signal_market_price,
                "last_gross_expected_profit_usdc": runtime.stats.last_gross_expected_profit_usdc,
                "last_estimated_cost_usdc": runtime.stats.last_estimated_cost_usdc,
                "last_order_status": runtime.stats.last_order_status,
                "last_resolution": runtime.stats.last_resolution,
                "last_no_trade_reason": runtime.stats.last_no_trade_reason,
                "last_liquidity_score": runtime.stats.last_liquidity_score,
                "last_net_expected_profit_usdc": runtime.stats.last_net_expected_profit_usdc,
                "last_book_spread": runtime.stats.last_book_spread,
                "last_hedge_decision": runtime.stats.last_hedge_decision,
                "total_trades": runtime.state.total_trades,
                "wins": runtime.state.wins,
                "losses": runtime.state.losses,
                "daily_pnl": runtime.state.daily_pnl,
                "current_bankroll_usdc": runtime.state.current_bankroll_usdc(),
                "available_bankroll_usdc": runtime.state.available_bankroll_usdc(),
                "pending_exposure_usdc": runtime.state.pending_exposure_usdc(),
                "unresolved_primary_count": runtime.state.unresolved_primary_count(),
            }
            for runtime in runtimes
        ],
    }


def build_runtime_status(
    tracks_or_runtimes,
    shared_state=None,
    engine_scores=None,
) -> dict:
    """Build runtime status payload.

    Supports two call signatures:
    - Legacy: build_runtime_status(runtimes: list[ModelRuntime])
    - Multi-track: build_runtime_status(tracks: list[ModelTrack], shared_state: BotState, engine_scores: dict)
    """
    from state.track import ModelTrack  # local import to avoid circular refs at module level

    if shared_state is not None:
        # New multi-track signature
        return _build_runtime_status_multi_track(tracks_or_runtimes, shared_state, engine_scores or {})
    # Legacy signature
    return _build_runtime_status_legacy(tracks_or_runtimes)


def _build_runtime_status_multi_track(
    tracks: list,
    shared_state,
    engine_scores: dict,
) -> dict:
    now = time.time()
    window = shared_state.current_window
    book_age_seconds = shared_state.polymarket_book_age_seconds()
    btc_price_age_seconds = shared_state.btc_price_age_seconds()
    healthy_price_sources = shared_state.healthy_price_source_names(now=now)
    price_divergence_bps = shared_state.price_feed_divergence_bps(now=now)
    window_phase = window.get_phase(int(now)) if window is not None else None
    book_idle = window is None or window_phase in {WindowPhase.TOO_LATE, WindowPhase.EXPIRED}

    btc_direction = "flat"
    window_open_price = window.open_price if window and hasattr(window, "open_price") else None
    if shared_state.btc_price and window_open_price:
        if shared_state.btc_price > window_open_price:
            btc_direction = "up"
        elif shared_state.btc_price < window_open_price:
            btc_direction = "down"

    secs_remaining = window.seconds_remaining(int(now)) if window else 0
    next_window_secs = (300 - (int(now) % 300)) if secs_remaining <= 0 else 0

    active_tracks = [t for t in tracks if t.active]
    active_track = active_tracks[0] if active_tracks else tracks[-1]
    if len(active_tracks) == 1:
        active_track_name = active_tracks[0].name
    elif len(active_tracks) > 1 and all(t.orderer.dry_run for t in active_tracks):
        active_track_name = "ALL_SHADOW"
    elif len(active_tracks) > 1:
        active_track_name = "MULTI"
    else:
        active_track_name = "NONE"
    mode = "LIVE" if any(not t.orderer.dry_run for t in active_tracks) else "DRY_RUN"

    total_pnl = round(sum(t.pnl_history[-1] if t.pnl_history else 0.0 for t in tracks), 4)
    btc_price_age_seconds = shared_state.btc_price_age_seconds()
    book_age_seconds = shared_state.polymarket_book_age_seconds()

    track_payloads = []
    for track in tracks:
        state = track.state
        stats = track.stats

        # Status badge
        can_enter, _ = track.guard.can_enter()
        if track.active and not track.orderer.dry_run:
            status = "live"
        elif not can_enter:
            status = "cooldown"
        else:
            status = "shadow"

        # Signal (last known) — assembled from RuntimeStats scalar fields
        signal_direction = getattr(stats, "last_signal_direction", None)
        signal_payload = None
        if signal_direction and signal_direction != "-":
            signal_payload = {
                "direction": signal_direction,
                "regime": getattr(stats, "last_regime_candidate", None),
                "confidence": round(float(getattr(stats, "last_signal_theo", 0.0) or 0.0), 3),
            }

        # Open positions — all of them with live prices
        open_positions = (
            state.current_window.get_open_positions()
            if state.current_window is not None
            else []
        )
        open_pos_by_id = {p.order_id: p for p in open_positions}
        all_open_positions_payload = []
        for pos in open_positions:
            if pos.direction == "UP":
                cur_price = (track.stats.current_up_bid or pos.entry_price) if track.stats else pos.entry_price
                ask_price = track.stats.current_up_ask if track.stats else None
            else:
                cur_price = (track.stats.current_down_bid or pos.entry_price) if track.stats else pos.entry_price
                ask_price = track.stats.current_down_ask if track.stats else None
            all_open_positions_payload.append({
                "direction": pos.direction,
                "role": pos.role,
                "size_usdc": round(pos.remaining_size_usdc, 2),
                "entry_price": pos.entry_price,
                "current_price": round(cur_price, 3),
                "ask_price": round(ask_price, 3) if ask_price is not None else None,
                "unrealized_pnl": round(pos.calculate_unrealized_pnl(cur_price), 4),
                "unrealized_pnl_pct": round(pos.unrealized_pnl_pct(cur_price) * 100, 2),
                "held_seconds": int(now - pos.entry_ts),
                "tp": pos.take_profit_price,
                "sl": pos.stop_loss_price,
            })
        # Keep legacy single open_position for backwards compat
        open_pos_payload = all_open_positions_payload[0] if all_open_positions_payload else None

        # Win stats from trade_history
        trade_history = getattr(state, "trade_history", [])
        resolved = [t for t in trade_history if t.resolved]
        wins = [t for t in resolved if t.pnl > 0]
        losses = [t for t in resolved if t.pnl < 0]
        win_rate = round(len(wins) / len(resolved), 3) if resolved else 0.0
        avg_win = round(sum(t.pnl for t in wins) / len(wins), 4) if wins else 0.0
        avg_loss = round(sum(t.pnl for t in losses) / len(losses), 4) if losses else 0.0
        gross_wins = sum(t.pnl for t in wins)
        gross_losses = abs(sum(t.pnl for t in losses))
        profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else None
        best_trade = round(max((t.pnl for t in resolved), default=0.0), 4)
        worst_trade = round(min((t.pnl for t in resolved), default=0.0), 4)

        # Bankroll
        bankroll_available = round(state.available_bankroll_usdc(), 2)
        bankroll_current = round(state.current_bankroll_usdc(), 2)
        bankroll_starting = round(state.starting_bankroll_usdc, 2)

        # All trades (most recent first) — open trades get live unrealized P&L
        all_trades_payload = []
        for t in reversed(trade_history):
            row = {
                "direction": t.direction,
                "status": t.status,
                "pnl": round(t.pnl, 4),
                "amount": round(t.amount_usdc, 2),
                "price": round(t.price, 3),
                "regime": getattr(t, "entry_regime", ""),
                "ts": int(t.ts),
            }
            if t.status == "OPEN" and t.order_id in open_pos_by_id:
                pos = open_pos_by_id[t.order_id]
                if pos.direction == "UP":
                    cur = (track.stats.current_up_bid or pos.entry_price) if track.stats else pos.entry_price
                else:
                    cur = (track.stats.current_down_bid or pos.entry_price) if track.stats else pos.entry_price
                row["live_pnl"] = round(pos.calculate_unrealized_pnl(cur), 4)
                row["live_pnl_pct"] = round(pos.unrealized_pnl_pct(cur) * 100, 2)
                row["current_price"] = round(cur, 3)
            all_trades_payload.append(row)

        track_payloads.append({
            "name": track.name,
            "css_color": track.css_color,
            "status": status,
            "signal": signal_payload,
            "open_position": open_pos_payload,
            "open_positions": all_open_positions_payload,
            "win_stats": {
                "win_rate": win_rate,
                "trades": len(resolved),
                "open": len(trade_history) - len(resolved),
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
                "best_trade": best_trade,
                "worst_trade": worst_trade,
                "consecutive_wins": state.consecutive_wins,
                "consecutive_losses": state.consecutive_losses,
            },
            "bankroll": {
                "available": bankroll_available,
                "current": bankroll_current,
                "starting": bankroll_starting,
            },
            "all_trades": all_trades_payload,
            "pnl_history": list(track.pnl_history),
        })

    return {
        "generated_ts": now,
        "btc_price": shared_state.btc_price or 0.0,
        "btc_direction": btc_direction,
        "window_seconds_remaining": secs_remaining,
        "next_window_seconds": next_window_secs,
        "mode": mode,
        "active_track": active_track_name,
        "total_pnl": total_pnl,
        "engine_scores": engine_scores,
        "freshness": {
            "btc_price_age_seconds": round(btc_price_age_seconds, 1) if btc_price_age_seconds is not None else None,
            "btc_price_stale": shared_state.is_price_stale(),
            "btc_price_source": shared_state.price_source or "",
            "btc_price_quorum_met": shared_state.price_feed_quorum_met(now=now),
            "btc_healthy_source_count": shared_state.healthy_price_source_count(now=now),
            "btc_healthy_sources": healthy_price_sources,
            "btc_min_healthy_sources": MIN_HEALTHY_PRICE_FEEDS,
            "btc_price_divergence_bps": round(price_divergence_bps, 1),
            "btc_max_divergence_bps": MAX_PRICE_FEED_DIVERGENCE_BPS,
            "polymarket_book_age_seconds": (
                round(book_age_seconds, 1) if book_age_seconds is not None and not book_idle else None
            ),
            "polymarket_book_stale": shared_state.is_polymarket_book_stale() if not book_idle else False,
            "polymarket_book_idle": book_idle,
            "price_stale_threshold_seconds": PRICE_STALE_THRESHOLD_SECONDS,
            "book_stale_threshold_seconds": POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS,
        },
        "tracks": track_payloads,
        "market": {
            # Market prices always come from both_track: select_snipe_signal only updates
            # both_track.stats regardless of which track is "active", so reading from
            # active_track would always return null when active_track != BOTH.
            "up_ask": next((t.stats.current_up_ask for t in tracks if t.name == "BOTH" and t.stats), None),
            "up_bid": next((t.stats.current_up_bid for t in tracks if t.name == "BOTH" and t.stats), None),
            "down_ask": next((t.stats.current_down_ask for t in tracks if t.name == "BOTH" and t.stats), None),
            "down_bid": next((t.stats.current_down_bid for t in tracks if t.name == "BOTH" and t.stats), None),
        },
    }


def write_runtime_status_file(
    payload_or_runtimes,
    path: str = "",
) -> None:
    """Write runtime status JSON file.

    Supports two call signatures:
    - New: write_runtime_status_file(payload_dict)  — pre-built status dict, uses RUNTIME_STATUS_FILE
    - Legacy: write_runtime_status_file(runtimes, path)  — builds status from ModelRuntime list
    """
    if isinstance(payload_or_runtimes, dict):
        payload = payload_or_runtimes
        effective_path = path or RUNTIME_STATUS_FILE
    else:
        # Legacy: list of ModelRuntime
        effective_path = path
        payload = build_runtime_status(payload_or_runtimes)
    if not effective_path.strip():
        return
    target = Path(effective_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = handle.name
    os.replace(temp_path, target)


async def dashboard_loop(runtimes_or_tracks, shared_state=None, engine_scores_ref=None) -> None:
    """Render dashboard in a loop.

    Supports legacy (list[ModelRuntime]) and new (list[ModelTrack]) signatures.
    """
    while True:
        if shared_state is not None:
            # New multi-track path — build fresh status each tick
            scores = engine_scores_ref() if callable(engine_scores_ref) else (engine_scores_ref or {})
            status = build_runtime_status(runtimes_or_tracks, shared_state, scores)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(json.dumps(status, indent=2))
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            frame = render_runtime_dashboard(runtimes_or_tracks)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(frame)
            sys.stdout.write("\n")
            sys.stdout.flush()
        await asyncio.sleep(1)


async def runtime_status_loop(
    runtimes_or_tracks,
    *,
    path: str = "",
    interval: float,
    shared_state: BotState | None = None,
    engine_scores_ref=None,
) -> None:
    """Write status JSON on interval.

    Supports legacy (list[ModelRuntime], path) and new (list[ModelTrack], shared_state) signatures.
    """
    while True:
        if shared_state is not None:
            scores = engine_scores_ref() if callable(engine_scores_ref) else (engine_scores_ref or {})
            payload = build_runtime_status(runtimes_or_tracks, shared_state, scores)
            await asyncio.to_thread(write_runtime_status_file, payload, path)
        else:
            await asyncio.to_thread(write_runtime_status_file, runtimes_or_tracks, path)
        await asyncio.sleep(interval)


async def _log_window_outcome_once(
    market_client,
    asset: str,
    market_slug: str,
    window_end_ts: int,
    ledgers: list,
) -> None:
    """Best-effort journal of a finished window's direction (no trade attached).

    Opens calibration data for windows the bot watched but skipped: replay and
    report join these to candidate records by window_end_ts. Never raises.
    """
    try:
        resolution = await wait_for_market_resolution(
            market_client,
            asset,
            market_slug,
            window_end_ts,
            prefer_binance=not isinstance(market_client, DemoClient),
            timeout_seconds=180,
        )
    except Exception as exc:  # noqa: BLE001 - calibration logging is best-effort
        LOGGER.warning("Window outcome logging failed for %s: %s", market_slug, exc)
        return
    if not resolution or resolution.get("resolved_up") is None:
        return
    for ledger in ledgers:
        try:
            if not ledger.has_window_outcome(window_end_ts):
                ledger.record_window_outcome(
                    window_end_ts,
                    bool(resolution["resolved_up"]),
                    market_slug=market_slug,
                    final_price=resolution.get("final_price"),
                )
        except Exception as exc:  # noqa: BLE001 - one bad ledger must not block others
            LOGGER.warning("Window outcome journal failed: %s", exc)


async def unresolved_resolution_loop(
    tracks: list[ModelTrack],
    market_client,
    *,
    interval: float,
    reconcile_intents: bool = False,
) -> None:
    while True:
        now_ts = int(time.time())
        total_resolved = 0
        for track in tracks:
            resolved_count = await asyncio.to_thread(
                sweep_unresolved_trades_once,
                track,
                market_client,
                now_ts=now_ts,
            )
            if resolved_count:
                summary = track.ledger.get_daily_summary()
                LOGGER.info(
                    "[%s] Daily: %sW/%sL | PnL: $%.2f",
                    track.name,
                    summary["wins"],
                    summary["losses"],
                    summary["total_pnl"],
                )
                total_resolved += resolved_count
            if reconcile_intents:
                await _reconcile_intents_for_track(track, market_client, now_ts=now_ts)
        await asyncio.sleep(interval)


async def _reconcile_intents_for_track(
    track: ModelTrack, market_client, *, now_ts: int
) -> None:
    """Resolve held ambiguous order intents against live state, mid-session.

    Best-effort: a live-API hiccup must not crash the resolution loop. Adopted
    fills and cancelled strays are journaled to the ledger; the operator is
    alerted so an unattended run surfaces the event.
    """
    try:
        result = await asyncio.to_thread(
            reconcile_open_order_intents,
            market_client,
            track.ledger,
            now_ts=now_ts,
        )
    except Exception as exc:  # noqa: BLE001 - reconciliation is best-effort
        LOGGER.warning("[%s] Intent reconciliation failed: %s", track.name, exc)
        return
    if result.reconciled_filled or result.cancelled:
        LOGGER.warning(
            "[%s] Inline intent reconciliation | adopted_fills=%s cancelled_strays=%s "
            "aborted=%s pending=%s",
            track.name,
            result.reconciled_filled,
            result.cancelled,
            result.aborted,
            result.still_pending,
        )
        emit_alert(
            "WARNING",
            "Inline order reconciliation resolved a held ambiguous order",
            track=track.name,
            adopted_fills=result.reconciled_filled,
            cancelled_strays=result.cancelled,
            aborted=result.aborted,
        )


def finalize_dashboard(runtimes: list[ModelRuntime], message: str) -> None:
    for runtime in runtimes:
        runtime.stats.note(message)
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write(render_runtime_dashboard(runtimes))
    sys.stdout.write("\n")
    sys.stdout.flush()


def build_feature_snapshot(
    *,
    window: WindowState,
    signal,
    btc_price: float,
    seconds_remaining: int,
    up_price: float,
    down_price: float,
    volatility: float,
    model_context: ModelContext | None,
    force_trade: bool,
    live_mode: bool,
    liquidity_score: float | None = None,
    book_spread: float | None = None,
    net_expected_profit_usdc: float | None = None,
) -> dict:
    move_pct = 0.0
    if window.open_price:
        move_pct = (btc_price - window.open_price) / window.open_price
    baseline_b_prob = get_probability(
        window.open_price,
        btc_price,
        seconds_remaining,
        volatility,
        "B",
        context=model_context,
    )
    directional_consistency = model_context.directional_consistency if model_context is not None else 0.5
    acceleration_pct = model_context.acceleration_pct if model_context is not None else 0.0
    tick_move_pct = model_context.tick_move_pct if model_context is not None else 0.0
    strongest_side = model_context.strongest_side if model_context is not None else "NONE"
    market_conviction = model_context.market_conviction if model_context is not None else 0.0
    market_implied_up_probability = (
        model_context.market_implied_up_probability if model_context is not None else 0.5
    )
    pooled_probability = get_probability(
        window.open_price,
        btc_price,
        seconds_remaining,
        volatility,
        "P",
        calibration_path=MODEL_C_CALIBRATION_FILE,
        context=model_context,
    )
    move_bin = bucket_move_pct(move_pct)
    time_bin = bucket_seconds_remaining(seconds_remaining)
    base_prob_bin = bucket_probability(baseline_b_prob)
    consistency_bin = bucket_directional_consistency(directional_consistency)
    acceleration_bin = bucket_acceleration(acceleration_pct)
    calibration_regime = build_calibration_regime_key(
        seconds_remaining=seconds_remaining,
        context=model_context,
        market_probability=market_implied_up_probability,
    )
    return {
        "theo_prob": signal.theo_prob,
        "confidence": signal.confidence,
        "seconds_remaining": seconds_remaining,
        "current_price": btc_price,
        "up_market_price": up_price,
        "down_market_price": down_price,
        "volatility": volatility,
        "move_pct": move_pct,
        "move_pct_bin": move_bin,
        "time_bin": time_bin,
        "baseline_b_prob": baseline_b_prob,
        "base_prob_bin": base_prob_bin,
        "tick_move_pct": tick_move_pct,
        "directional_consistency": directional_consistency,
        "directional_consistency_bin": consistency_bin,
        "acceleration_pct": acceleration_pct,
        "acceleration_bin": acceleration_bin,
        "model_c_bucket": build_model_c_bucket_key(
            move_bin=move_bin,
            time_bin=time_bin,
            base_prob_bin=base_prob_bin,
            directional_consistency_bin=consistency_bin,
            acceleration_bin=acceleration_bin,
        ),
        "entry_regime": getattr(signal, "entry_regime", "MISPRICING"),
        "strongest_side": strongest_side,
        "market_conviction": market_conviction,
        "market_implied_up_probability": market_implied_up_probability,
        "pooled_probability": pooled_probability,
        "calibration_regime": calibration_regime,
        "btc_confirmed": getattr(signal, "btc_confirmed", False),
        "up_price_momentum": model_context.up_price_momentum if model_context is not None else 0.0,
        "down_price_momentum": model_context.down_price_momentum if model_context is not None else 0.0,
        "up_price_acceleration": (
            model_context.up_price_acceleration if model_context is not None else 0.0
        ),
        "down_price_acceleration": (
            model_context.down_price_acceleration if model_context is not None else 0.0
        ),
        "spread_change": model_context.spread_change if model_context is not None else 0.0,
        "one_sided_ratio": model_context.one_sided_ratio if model_context is not None else 0.0,
        "midpoint_shift": model_context.midpoint_shift if model_context is not None else 0.0,
        "btc_poly_agreement": model_context.btc_poly_agreement if model_context is not None else 0.0,
        "force_trade": force_trade,
        "live_mode": live_mode,
        "liquidity_score": liquidity_score,
        "book_spread": book_spread,
        "net_expected_profit_usdc": net_expected_profit_usdc,
    }


def _build_selection(
    *,
    signal,
    up_price: float | None,
    down_price: float | None,
    volatility: float,
    seconds_remaining: int,
    model_context: ModelContext | None,
    strongest_side: str = "NONE",
    regime_candidate: str = "NONE",
    up_book: object | None = None,
    down_book: object | None = None,
    reason_code: str = "",
    reason_detail: str = "",
    liquidity_score: float = 0.0,
    book_spread: float | None = None,
    net_expected_profit_usdc: float = 0.0,
    gross_expected_profit_usdc: float = 0.0,
    estimated_cost_usdc: float = 0.0,
    probabilities: dict[str, float] | None = None,
) -> SnipeSelection:
    return SnipeSelection(
        signal=signal,
        up_price=up_price,
        down_price=down_price,
        volatility=volatility,
        seconds_remaining=seconds_remaining,
        model_context=model_context,
        strongest_side=strongest_side,
        regime_candidate=regime_candidate,
        up_book=up_book,
        down_book=down_book,
        reason_code=reason_code,
        reason_detail=reason_detail,
        liquidity_score=liquidity_score,
        book_spread=book_spread,
        net_expected_profit_usdc=net_expected_profit_usdc,
        gross_expected_profit_usdc=gross_expected_profit_usdc,
        estimated_cost_usdc=estimated_cost_usdc,
        probabilities=dict(probabilities or {}),
    )


def _track_entry_skip_reason(
    track: ModelTrack,
    selection: SnipeSelection | None,
    *,
    window: WindowState,
) -> str:
    if selection is None or selection.signal is None:
        return "no signal"

    signal = selection.signal
    if not track.active:
        return "inactive track (shadow only)"

    if signal.entry_regime not in track.regime_filter:
        return f"regime={signal.entry_regime} blocked by filter"

    if signal.market_price >= HIGH_CONVICTION_ENTRY_THRESHOLD:
        window_elapsed = time.time() - (window.end_ts - 300)
        if window_elapsed < HIGH_CONVICTION_MIN_ELAPSED_SECONDS:
            remaining = HIGH_CONVICTION_MIN_ELAPSED_SECONDS - window_elapsed
            return f"high-conv blocked: {remaining:.0f}s until 50s mark"

    can_enter, reason = track.guard.can_enter(
        direction=signal.direction.value if signal else None
    )
    if not can_enter:
        _emit_guard_breach_alert(track, reason)
        return reason

    if track.name == "CONVICTION_ONLY" and signal.entry_regime == "LATE_CONVICTION":
        same_window_entries = [
            trade
            for trade in track.state.trade_history
            if trade.role == "PRIMARY"
            and trade.window_end_ts == window.end_ts
            and trade.direction == signal.direction.value
        ]
        same_window_entry_count = len(same_window_entries)
        liquidity_score = (
            selection.liquidity_score
            if isinstance(getattr(selection, "liquidity_score", None), (int, float))
            else 0.0
        )
        btc_confirmed = getattr(signal, "btc_confirmed", False) is True
        if same_window_entry_count >= 2:
            return "conviction re-entry cap reached for this window"
        if same_window_entry_count == 1:
            if (
                getattr(signal, "confidence", 0.0) < 0.96
                or getattr(signal, "edge", 0.0) < 0.08
                or liquidity_score < 0.75
                or not btc_confirmed
            ):
                return "conviction re-entry needs stronger confirmation"

    return ""


async def select_snipe_signals(
    *,
    window: WindowState,
    state: BotState,
    orderer: Orderer,
    tracks: list[ModelTrack],
    asset: str,
    force_trade: bool,
    live_mode: bool,
    allow_waiting_conviction: bool = False,
    on_tick_callback=None,
) -> dict[str, SnipeSelection]:
    del live_mode
    best_signals: dict[str, object | None] = {track.name: None for track in tracks}
    previous_signals: dict[str, object | None] = {track.name: None for track in tracks}
    last_track_selections: dict[str, SnipeSelection] = {
        track.name: _build_selection(
            signal=None,
            up_price=None,
            down_price=None,
            volatility=state.rolling_volatility(),
            seconds_remaining=window.seconds_remaining(int(time.time())),
            model_context=None,
            reason_code="WAITING",
            reason_detail="Waiting for tradable book",
        )
        for track in tracks
    }
    effective_start_seconds = max(SNIPE_START_SECONDS, SNIPE_EARLY_ENTRY_SECONDS)

    while True:
        if on_tick_callback is not None:
            await on_tick_callback()
        now = int(time.time())
        seconds_remaining = window.seconds_remaining(now)
        if seconds_remaining < SNIPE_HARD_DEADLINE_SECONDS:
            return {
                name: _build_selection(
                    signal=None,
                    up_price=selection.up_price,
                    down_price=selection.down_price,
                    volatility=selection.volatility,
                    seconds_remaining=seconds_remaining,
                    model_context=selection.model_context,
                    strongest_side=selection.strongest_side,
                    regime_candidate=selection.regime_candidate,
                    reason_code=selection.reason_code,
                    reason_detail=selection.reason_detail,
                    liquidity_score=selection.liquidity_score,
                    book_spread=selection.book_spread,
                    net_expected_profit_usdc=selection.net_expected_profit_usdc,
                    gross_expected_profit_usdc=selection.gross_expected_profit_usdc,
                estimated_cost_usdc=selection.estimated_cost_usdc,
                probabilities=selection.probabilities,
            )
                for name, selection in last_track_selections.items()
            }
        if seconds_remaining > effective_start_seconds and not allow_waiting_conviction:
            for track in tracks:
                if track.stats is not None:
                    track.stats.note(f"Monitoring entry window | T-{seconds_remaining}s")
            await asyncio.sleep(min(1.0, max(0.2, seconds_remaining - effective_start_seconds)))
            continue

        up_book, down_book = await asyncio.gather(
            asyncio.to_thread(orderer.get_book_snapshot, window.up_token_id),
            asyncio.to_thread(orderer.get_book_snapshot, window.down_token_id),
        )
        if up_book is None or down_book is None:
            reason_code, reason_detail = _book_unavailable_reason(state)
            emit_alert_throttled(
                "ERROR" if reason_code == "STALE_POLYMARKET_BOOK" else "WARNING",
                reason_detail,
                throttle_seconds=120.0,
                alert_key=f"{reason_code}:{window.end_ts}",
                asset=asset,
                window_end_ts=window.end_ts,
                reason_code=reason_code,
                book_age_seconds=state.polymarket_book_age_seconds(),
            )
            for track in tracks:
                last_track_selections[track.name] = _build_selection(
                    signal=None,
                    up_price=up_book.best_ask if up_book is not None else None,
                    down_price=down_book.best_ask if down_book is not None else None,
                    volatility=state.rolling_volatility(),
                    seconds_remaining=seconds_remaining,
                    model_context=None,
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                )
                track.ledger.record_candidate(
                    window_end_ts=window.end_ts,
                    track=track.name,
                    model=track.model,
                    direction=None,
                    regime_candidate="NONE",
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                    would_trade=False,
                    skip_reason=reason_code,
                    market_price=None,
                    up_price=up_book.best_ask if up_book is not None else None,
                    down_price=down_book.best_ask if down_book is not None else None,
                    btc_price=state.btc_price,
                    open_price=window.open_price,
                    volatility=state.rolling_volatility(),
                    probabilities={},
                    details={
                        "freshness": {
                            "polymarket_book_age_seconds": state.polymarket_book_age_seconds(),
                            "polymarket_book_stale": state.is_polymarket_book_stale(),
                        },
                        "kalshi": {
                            "up_probability": state.kalshi_up_probability,
                            "updated_ts": state.kalshi_updated_ts,
                        },
                    },
                )
                if track.stats is not None:
                    track.stats.last_signal_direction = "-"
                    track.stats.last_signal_edge = None
                    track.stats.last_signal_theo = None
                    track.stats.last_signal_market_price = None
                    track.stats.last_no_trade_reason = reason_code
                    track.stats.note(reason_detail)
            LOGGER.warning("%s", reason_detail)
            await asyncio.sleep(5)
            continue

        up_price = up_book.best_ask
        down_price = down_book.best_ask
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
        volatility = state.rolling_volatility()
        polymarket_features = state.polymarket_features()
        strongest_side = str(polymarket_features.get("strongest_side", "NONE"))
        book_assessment = assess_book_quality(
            up_book,
            down_book,
            strongest_side=strongest_side,
            market_conviction=float(polymarket_features.get("market_conviction", 0.0)),
        )

        if state.is_price_stale() and not ALLOW_TRADING_WITHOUT_BTC_PRICE:
            stale_age = state.btc_price_age_seconds()
            healthy_source_count = state.healthy_price_source_count()
            healthy_sources = ",".join(state.healthy_price_source_names()) or "-"
            divergence_bps = state.price_feed_divergence_bps()
            detail = (
                f"BTC price stale ({stale_age:.1f}s old from {state.price_source or 'unknown'}) | "
                f"quorum={healthy_source_count}/{MIN_HEALTHY_PRICE_FEEDS} | "
                f"div={divergence_bps:.1f}bps | sources={healthy_sources} | "
                f"score={book_assessment.liquidity_score:.3f} | "
                f"{format_book_snapshot('UP', up_book)} | {format_book_snapshot('DOWN', down_book)}"
            )
            emit_alert_throttled(
                "ERROR",
                "BTC price stale",
                throttle_seconds=120.0,
                alert_key=f"stale_btc:{asset}:{window.end_ts}",
                asset=asset,
                window_end_ts=window.end_ts,
                btc_price_age_seconds=stale_age,
                btc_price_source=state.price_source or "",
                btc_healthy_source_count=healthy_source_count,
                btc_healthy_sources=state.healthy_price_source_names(),
                btc_price_divergence_bps=round(divergence_bps, 2),
            )
            for track in tracks:
                last_track_selections[track.name] = _build_selection(
                    signal=None,
                    up_price=up_price,
                    down_price=down_price,
                    volatility=volatility,
                    seconds_remaining=seconds_remaining,
                    model_context=None,
                    strongest_side=strongest_side,
                    regime_candidate="NONE",
                    up_book=up_book,
                    down_book=down_book,
                    reason_code="STALE_BTC_PRICE",
                    reason_detail=detail,
                    liquidity_score=book_assessment.liquidity_score,
                    book_spread=book_assessment.combined_spread,
                    probabilities={},
                )
                track.ledger.record_candidate(
                    window_end_ts=window.end_ts,
                    track=track.name,
                    model=track.model,
                    direction=None,
                    regime_candidate="NONE",
                    reason_code="STALE_BTC_PRICE",
                    reason_detail=detail,
                    would_trade=False,
                    skip_reason="STALE_BTC_PRICE",
                    market_price=None,
                    up_price=up_price,
                    down_price=down_price,
                    liquidity_score=book_assessment.liquidity_score,
                    book_spread=book_assessment.combined_spread,
                    strongest_side=strongest_side,
                    market_conviction=float(polymarket_features.get("market_conviction", 0.0)),
                    btc_confirmed=False,
                    seconds_remaining=seconds_remaining,
                    btc_price=state.btc_price,
                    open_price=window.open_price,
                    volatility=volatility,
                    probabilities={},
                    details={
                        "freshness": {
                            "btc_price_age_seconds": stale_age,
                            "btc_price_source": state.price_source or "",
                        }
                    },
                )
                if track.stats is not None:
                    track.stats.last_signal_direction = "-"
                    track.stats.last_signal_edge = None
                    track.stats.last_signal_theo = None
                    track.stats.last_signal_market_price = None
                    track.stats.last_no_trade_reason = "STALE_BTC_PRICE"
                    track.stats.last_liquidity_score = book_assessment.liquidity_score
                    track.stats.last_book_spread = book_assessment.combined_spread
                    track.stats.last_net_expected_profit_usdc = None
                    track.stats.last_gross_expected_profit_usdc = None
                    track.stats.last_estimated_cost_usdc = None
                    track.stats.last_bet_size = None
                    track.stats.last_strongest_side = strongest_side
                    track.stats.last_regime_candidate = "NONE"
                    track.stats.note(detail)
                    track.stats.current_up_ask = up_price
                    track.stats.current_down_ask = down_price
                    track.stats.current_up_bid = up_book.best_bid if up_book else None
                    track.stats.current_down_bid = down_book.best_bid if down_book else None
            LOGGER.warning(detail)
            await asyncio.sleep(SNIPE_POLL_SECONDS)
            continue

        tick_features = state.tick_trend_features()
        model_context = ModelContext(**tick_features, **polymarket_features)
        strongest_side = model_context.strongest_side
        candidate_probabilities = compute_candidate_probabilities(
            tracks=tracks,
            window=window,
            state=state,
            seconds_remaining=seconds_remaining,
            volatility=volatility,
            model_context=model_context,
        )

        if not book_assessment.trade_ready or up_price is None or down_price is None:
            detail = (
                f"{book_assessment.reason_detail} | score={book_assessment.liquidity_score:.3f} | "
                f"{format_book_snapshot('UP', up_book)} | {format_book_snapshot('DOWN', down_book)}"
            )
            for track in tracks:
                last_track_selections[track.name] = _build_selection(
                    signal=None,
                    up_price=up_price,
                    down_price=down_price,
                    volatility=volatility,
                    seconds_remaining=seconds_remaining,
                    model_context=model_context,
                    strongest_side=strongest_side,
                    regime_candidate="NONE",
                    up_book=up_book,
                    down_book=down_book,
                    reason_code=book_assessment.reason_code,
                    reason_detail=detail,
                    liquidity_score=book_assessment.liquidity_score,
                    book_spread=book_assessment.combined_spread,
                    probabilities=candidate_probabilities,
                )
                track.ledger.record_candidate(
                    window_end_ts=window.end_ts,
                    track=track.name,
                    model=track.model,
                    direction=None,
                    regime_candidate="NONE",
                    reason_code=book_assessment.reason_code,
                    reason_detail=detail,
                    would_trade=False,
                    skip_reason=book_assessment.reason_code,
                    market_price=None,
                    up_price=up_price,
                    down_price=down_price,
                    liquidity_score=book_assessment.liquidity_score,
                    book_spread=book_assessment.combined_spread,
                    strongest_side=strongest_side,
                    market_conviction=model_context.market_conviction,
                    btc_confirmed=model_context.btc_poly_agreement > 0,
                    seconds_remaining=seconds_remaining,
                    btc_price=state.btc_price,
                    open_price=window.open_price,
                    volatility=volatility,
                    probabilities=candidate_probabilities,
                    details={
                        "microstructure": {
                            "up_queue_imbalance": model_context.up_queue_imbalance,
                            "down_queue_imbalance": model_context.down_queue_imbalance,
                            "up_microprice_offset": model_context.up_microprice_offset,
                            "down_microprice_offset": model_context.down_microprice_offset,
                            "strongest_queue_imbalance": model_context.strongest_queue_imbalance,
                            "strongest_microprice_offset": model_context.strongest_microprice_offset,
                            "depth_imbalance": model_context.depth_imbalance,
                            "strongest_depth_to_fill_usdc": model_context.strongest_depth_to_fill_usdc,
                            "weakest_depth_to_fill_usdc": model_context.weakest_depth_to_fill_usdc,
                        }
                    },
                )
                if track.stats is not None:
                    track.stats.last_signal_direction = "-"
                    track.stats.last_signal_edge = None
                    track.stats.last_signal_theo = None
                    track.stats.last_signal_market_price = None
                    track.stats.last_no_trade_reason = book_assessment.reason_code
                    track.stats.last_liquidity_score = book_assessment.liquidity_score
                    track.stats.last_book_spread = book_assessment.combined_spread
                    track.stats.last_net_expected_profit_usdc = None
                    track.stats.last_gross_expected_profit_usdc = None
                    track.stats.last_estimated_cost_usdc = None
                    track.stats.last_bet_size = None
                    track.stats.last_strongest_side = strongest_side
                    track.stats.last_regime_candidate = "NONE"
                    track.stats.note(detail)
                    track.stats.current_up_ask = up_price
                    track.stats.current_down_ask = down_price
                    track.stats.current_up_bid = up_book.best_bid if up_book else None
                    track.stats.current_down_bid = down_book.best_bid if down_book else None
            LOGGER.info(detail)
            await asyncio.sleep(SNIPE_POLL_SECONDS)
            continue

        btc_price_available = not state.is_price_stale() or ALLOW_TRADING_WITHOUT_BTC_PRICE
        ready_active_track = False

        for track in tracks:
            candidate_eval = evaluate_with_diagnostics(
                window,
                state.btc_price,
                seconds_remaining,
                up_price,
                down_price,
                volatility=volatility,
                model=track.model,
                ledger_path=track.ledger.filepath,
                calibration_path=MODEL_C_CALIBRATION_FILE,
                model_context=model_context,
                btc_price_available=btc_price_available,
                day_vol_v24_bps=state.day_vol_v24_bps(),
            )
            candidate_signal = candidate_eval.signal
            reason_code = candidate_eval.reason_code or book_assessment.reason_code
            reason_detail = candidate_eval.reason_detail or book_assessment.reason_detail
            regime_candidate = candidate_eval.regime_candidate or "NONE"
            candidate_bet = 0.0
            gross_expected_profit_usdc = 0.0
            net_expected_profit_usdc = 0.0
            total_cost_estimate = 0.0
            spread_penalty_pctile = 0.0
            min_net_profit_required = MIN_NET_EXPECTED_PROFIT_USDC

            if candidate_signal is not None:
                (
                    candidate_bet,
                    gross_expected_profit_usdc,
                    net_expected_profit_usdc,
                    total_cost_estimate,
                    _spread_penalty_usdc,
                    spread_penalty_pctile,
                ) = evaluate_trade_economics(
                    asset=asset,
                    signal=candidate_signal,
                    state=track.state,
                    up_book=up_book,
                    down_book=down_book,
                    liquidity_score=book_assessment.liquidity_score,
                )
                min_net_profit_required = _scaled_net_profit_floor_usdc(spread_penalty_pctile)
                if net_expected_profit_usdc < min_net_profit_required:
                    reason_code = "NET_PROFIT_TOO_LOW"
                    reason_detail = (
                        f"Net expected profit ${net_expected_profit_usdc:.2f} below "
                        f"${min_net_profit_required:.2f} (scaled floor, spread pctile {spread_penalty_pctile:.2f}) "
                        f"after est costs ${total_cost_estimate:.2f}"
                    )
                    candidate_signal = None

            best_signal = choose_better_signal(best_signals[track.name], candidate_signal)
            selected_signal = None
            selected_net_profit = net_expected_profit_usdc
            selected_gross_profit = gross_expected_profit_usdc
            selected_cost_estimate = total_cost_estimate
            selected_bet = candidate_bet

            if should_fire_snipe_signal(
                candidate_signal,
                best_signal=best_signal,
                previous_signal=previous_signals[track.name],
                seconds_remaining=seconds_remaining,
                force_trade=force_trade,
                liquidity_score=book_assessment.liquidity_score,
                net_expected_profit_usdc=net_expected_profit_usdc,
                min_net_profit_required=min_net_profit_required,
            ):
                selected_signal = choose_better_signal(best_signal, candidate_signal)
                (
                    selected_bet,
                    selected_gross_profit,
                    selected_net_profit,
                    selected_cost_estimate,
                    _selected_spread_penalty,
                    selected_spread_pctile,
                ) = evaluate_trade_economics(
                    asset=asset,
                    signal=selected_signal,
                    state=track.state,
                    up_book=up_book,
                    down_book=down_book,
                    liquidity_score=book_assessment.liquidity_score,
                )
                selected_min_net_profit = _scaled_net_profit_floor_usdc(selected_spread_pctile)
                if selected_net_profit < selected_min_net_profit and not force_trade:
                    selected_signal = None
                else:
                    reason_code = "READY"
                    reason_detail = (
                        f"Book ready | score={book_assessment.liquidity_score:.3f} "
                        f"spread={book_assessment.combined_spread if book_assessment.combined_spread is not None else '-'} "
                        f"net=${selected_net_profit:.2f} min=${selected_min_net_profit:.2f}"
                    )
                    regime_candidate = (
                        selected_signal.entry_regime if selected_signal is not None else regime_candidate
                    )
                    if track.active:
                        ready_active_track = True

            selection_signal = selected_signal if selected_signal is not None else None
            selection_strongest_side = (
                selected_signal.strongest_side if selected_signal is not None else strongest_side
            )
            selection_regime_candidate = (
                selected_signal.entry_regime if selected_signal is not None else regime_candidate
            )
            selection_net_profit = (
                selected_net_profit if selected_signal is not None else net_expected_profit_usdc
            )
            selection_gross_profit = (
                selected_gross_profit if selected_signal is not None else gross_expected_profit_usdc
            )
            selection_cost_estimate = (
                selected_cost_estimate if selected_signal is not None else total_cost_estimate
            )
            track_skip_reason = ""
            if selected_signal is not None:
                track_skip_reason = _track_entry_skip_reason(
                    track,
                    _build_selection(
                        signal=selected_signal,
                        up_price=up_price,
                        down_price=down_price,
                        volatility=volatility,
                        seconds_remaining=seconds_remaining,
                        model_context=model_context,
                        strongest_side=selection_strongest_side,
                        regime_candidate=selection_regime_candidate,
                        up_book=up_book,
                        down_book=down_book,
                        reason_code=reason_code,
                        reason_detail=reason_detail,
                        liquidity_score=book_assessment.liquidity_score,
                        book_spread=book_assessment.combined_spread,
                        net_expected_profit_usdc=selection_net_profit,
                        gross_expected_profit_usdc=selection_gross_profit,
                        estimated_cost_usdc=selection_cost_estimate,
                        probabilities=candidate_probabilities,
                    ),
                    window=window,
                )

            last_track_selections[track.name] = _build_selection(
                signal=selection_signal,
                up_price=up_price,
                down_price=down_price,
                volatility=volatility,
                seconds_remaining=seconds_remaining,
                model_context=model_context,
                strongest_side=selection_strongest_side,
                regime_candidate=selection_regime_candidate,
                up_book=up_book,
                down_book=down_book,
                reason_code=reason_code,
                reason_detail=reason_detail,
                liquidity_score=book_assessment.liquidity_score,
                book_spread=book_assessment.combined_spread,
                net_expected_profit_usdc=selection_net_profit,
                gross_expected_profit_usdc=selection_gross_profit,
                estimated_cost_usdc=selection_cost_estimate,
                probabilities=candidate_probabilities,
            )
            logged_signal = selected_signal or candidate_signal
            track.ledger.record_candidate(
                window_end_ts=window.end_ts,
                track=track.name,
                model=track.model,
                direction=logged_signal.direction.value if logged_signal is not None else None,
                regime_candidate=selection_regime_candidate,
                reason_code=reason_code,
                reason_detail=reason_detail,
                would_trade=selected_signal is not None and not track_skip_reason,
                skip_reason="" if selected_signal is not None and not track_skip_reason else track_skip_reason or reason_code,
                entry_regime=logged_signal.entry_regime if logged_signal is not None else "",
                market_price=logged_signal.market_price if logged_signal is not None else None,
                up_price=up_price,
                down_price=down_price,
                liquidity_score=book_assessment.liquidity_score,
                book_spread=book_assessment.combined_spread,
                gross_expected_profit_usdc=selection_gross_profit,
                net_expected_profit_usdc=selection_net_profit,
                estimated_cost_usdc=selection_cost_estimate,
                planned_bet_usdc=selected_bet if selected_signal is not None else candidate_bet,
                edge=logged_signal.edge if logged_signal is not None else None,
                ev=logged_signal.ev if logged_signal is not None else None,
                confidence=logged_signal.confidence if logged_signal is not None else None,
                selected_probability=logged_signal.theo_prob if logged_signal is not None else None,
                direction_probability=logged_signal.confidence if logged_signal is not None else None,
                strongest_side=selection_strongest_side,
                market_conviction=model_context.market_conviction,
                btc_confirmed=logged_signal.btc_confirmed if logged_signal is not None else (model_context.btc_poly_agreement > 0),
                seconds_remaining=seconds_remaining,
                btc_price=state.btc_price,
                open_price=window.open_price,
                volatility=volatility,
                probabilities=candidate_probabilities,
                details={
                    "microstructure": {
                        "up_queue_imbalance": model_context.up_queue_imbalance,
                        "down_queue_imbalance": model_context.down_queue_imbalance,
                        "up_microprice_offset": model_context.up_microprice_offset,
                        "down_microprice_offset": model_context.down_microprice_offset,
                        "strongest_queue_imbalance": model_context.strongest_queue_imbalance,
                        "strongest_microprice_offset": model_context.strongest_microprice_offset,
                        "depth_imbalance": model_context.depth_imbalance,
                        "strongest_depth_to_fill_usdc": model_context.strongest_depth_to_fill_usdc,
                        "weakest_depth_to_fill_usdc": model_context.weakest_depth_to_fill_usdc,
                    },
                    "calibration": {
                        "market_implied_up_probability": model_context.market_implied_up_probability,
                        "pooled_probability": candidate_probabilities.get("P"),
                        "calibration_regime": build_calibration_regime_key(
                            seconds_remaining=seconds_remaining,
                            context=model_context,
                            market_probability=model_context.market_implied_up_probability,
                        ),
                    },
                    "kalshi": {
                        "up_probability": state.kalshi_up_probability,
                        "updated_ts": state.kalshi_updated_ts,
                    },
                },
            )

            if track.stats is not None:
                track.stats.current_up_ask = up_price
                track.stats.current_down_ask = down_price
                track.stats.current_up_bid = up_book.best_bid if up_book else None
                track.stats.current_down_bid = down_book.best_bid if down_book else None
                track.stats.last_strongest_side = strongest_side
                track.stats.last_regime_candidate = regime_candidate
                track.stats.last_liquidity_score = book_assessment.liquidity_score
                track.stats.last_book_spread = book_assessment.combined_spread
                track.stats.last_net_expected_profit_usdc = selection_net_profit
                track.stats.last_gross_expected_profit_usdc = selection_gross_profit
                track.stats.last_estimated_cost_usdc = selection_cost_estimate
                if candidate_signal is not None:
                    track.stats.last_signal_direction = candidate_signal.direction.value
                    track.stats.last_signal_edge = candidate_signal.edge
                    track.stats.last_signal_theo = candidate_signal.theo_prob
                    track.stats.last_signal_market_price = candidate_signal.market_price
                    track.stats.last_no_trade_reason = ""
                    track.stats.last_bet_size = selected_bet if selected_signal is not None else candidate_bet
                    track.stats.note(
                        f"Snipe watch | T-{seconds_remaining}s | "
                        f"{candidate_signal.entry_regime} {candidate_signal.direction.value} "
                        f"c{candidate_signal.confidence:.3f} "
                        f"e{candidate_signal.edge:.3f} | score={book_assessment.liquidity_score:.3f} "
                        f"net ${selection_net_profit:.2f}"
                    )
                else:
                    track.stats.last_signal_direction = "-"
                    track.stats.last_signal_edge = None
                    track.stats.last_signal_theo = None
                    track.stats.last_signal_market_price = None
                    track.stats.last_no_trade_reason = reason_code
                    track.stats.last_bet_size = None
                    track.stats.note(
                        f"Snipe watch | T-{seconds_remaining}s | {reason_code} | "
                        f"score={book_assessment.liquidity_score:.3f} | net ${selection_net_profit:.2f}"
                    )

            best_signals[track.name] = best_signal
            previous_signals[track.name] = candidate_signal

        if ready_active_track:
            return last_track_selections

        await asyncio.sleep(SNIPE_POLL_SECONDS)


async def refresh_window_books(
    orderer: Orderer,
    window: WindowState,
) -> tuple[BookSnapshot | None, BookSnapshot | None]:
    up_result, down_result = await asyncio.gather(
        asyncio.to_thread(orderer.get_book_snapshot, window.up_token_id),
        asyncio.to_thread(orderer.get_book_snapshot, window.down_token_id),
        return_exceptions=True,
    )
    up_book = None if isinstance(up_result, Exception) else up_result
    down_book = None if isinstance(down_result, Exception) else down_result
    if isinstance(up_result, Exception):
        LOGGER.warning("UP order-book refresh failed for %s: %s", window.up_token_id, up_result)
    if isinstance(down_result, Exception):
        LOGGER.warning("DOWN order-book refresh failed for %s: %s", window.down_token_id, down_result)
    return up_book, down_book


def _bounded_fill_deadline(window: WindowState, *, timeout_seconds: int) -> int:
    now = int(time.time())
    latest_deadline = max(now + 1, window.end_ts - 1)
    return min(latest_deadline, now + max(1, timeout_seconds))


def _filled_amount_usdc(result: OrderResult, fallback_amount_usdc: float) -> float:
    fill_price = result.avg_price or result.submitted_price or 0.0
    if result.filled_size > 0 and fill_price > 0:
        return round(result.filled_size * fill_price, 4)
    return round(fallback_amount_usdc, 4)


def compute_client_order_id(
    *,
    track: str,
    window_end_ts: int,
    role: str,
    token_id: str,
    price: float,
    amount_usdc: float,
) -> str:
    """Deterministic id for an order intent.

    Two submissions that are the same logical order (same track, window, side,
    price, and size) produce the same id, so a retry after an ambiguous failure
    is recognised as a duplicate. Genuinely distinct orders (different size or
    side) get distinct ids and are never blocked.
    """
    raw = f"{track}|{int(window_end_ts)}|{role}|{token_id}|{price:.4f}|{amount_usdc:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def submit_trade(
    track: ModelTrack,
    *,
    window: WindowState,
    signal,
    amount_usdc: float,
    role: str,
    asset: str,
    feature_snapshot: dict | None = None,
    execution_snapshot: dict | None = None,
) -> SubmittedTrade | None:
    token_id = window.up_token_id if signal.direction.value == "UP" else window.down_token_id
    minimum_notional = 0.0
    try:
        minimum_notional = await asyncio.to_thread(
            track.orderer.minimum_usdc_amount,
            token_id,
            signal.market_price,
            entry_regime=getattr(signal, "entry_regime", "MISPRICING"),
            side="BUY",
        )
    except Exception:
        minimum_notional = 0.0
    if minimum_notional > 0 and amount_usdc < minimum_notional:
        available = track.state.available_bankroll_usdc()
        if available < minimum_notional:
            if track.stats is not None:
                track.stats.last_order_status = "REJECTED"
                track.stats.note(
                    f"Order skipped: minimum ${minimum_notional:.2f} exceeds available ${available:.2f}"
                )
            LOGGER.warning(
                "Order skipped for %s: min notional $%.2f exceeds available $%.2f",
                token_id,
                minimum_notional,
                available,
            )
            return None
        amount_usdc = minimum_notional

    client_order_id = compute_client_order_id(
        track=track.name,
        window_end_ts=window.end_ts,
        role=role,
        token_id=token_id,
        price=signal.market_price,
        amount_usdc=amount_usdc,
    )
    if track.ledger.has_open_order_intent(client_order_id):
        if track.stats is not None:
            track.stats.last_order_status = "DEDUPED"
            track.stats.note("Order skipped: duplicate of an in-flight submission")
        LOGGER.warning(
            "Order skipped for %s: client_order_id %s already in flight "
            "(prior submit may be live; awaiting reconciliation)",
            token_id,
            client_order_id,
        )
        return None
    track.ledger.record_order_intent(
        client_order_id,
        window_end_ts=window.end_ts,
        status="IN_FLIGHT",
        details={
            "token_id": token_id,
            "direction": signal.direction.value,
            "role": role,
            "amount_usdc": amount_usdc,
            "track": track.name,
            # Carried so inline reconciliation can adopt a held ambiguous order
            # as a fully resolvable trade without re-deriving market context.
            "model": signal.model_used,
            "market_url": window.market_url,
            "market_slug": extract_market_slug(window.market_url),
            "asset": asset,
            "open_price": window.open_price,
        },
    )

    try:
        result = await asyncio.to_thread(
            track.orderer.place_bet,
            token_id,
            signal.direction,
            amount_usdc,
            signal.market_price,
            entry_regime=getattr(signal, "entry_regime", "MISPRICING"),
        )
    except AmbiguousOrderError as exc:
        # The order may have reached the exchange. Leave the intent IN_FLIGHT so a
        # retry cannot duplicate a possibly-live order; reconciliation resolves it.
        if track.stats is not None:
            track.stats.last_order_status = "AMBIGUOUS"
            track.stats.note(f"Order submission ambiguous (held for reconciliation): {exc}")
        LOGGER.error("Ambiguous order submission for %s: %s", token_id, exc)
        emit_alert(
            "CRITICAL",
            "Order submission ambiguous",
            track=track.name,
            token_id=token_id,
            client_order_id=client_order_id,
            error=str(exc),
        )
        return None
    except RuntimeError as exc:
        # Pre-network rejection (slippage cap / validation): no order was placed,
        # so free the id for a clean retry on the next tick.
        track.ledger.record_order_intent(
            client_order_id, window_end_ts=window.end_ts, status="ABORTED"
        )
        if track.stats is not None:
            track.stats.last_order_status = "REJECTED"
            track.stats.note(f"Order rejected: {exc}")
        LOGGER.warning("Order rejected for %s: %s", token_id, exc)
        _record_order_failure_alert(track, token_id=token_id, error_text=str(exc))
        return None
    _ORDER_FAILURE_TS.pop(track.name, None)
    market_slug = extract_market_slug(window.market_url)
    merged_execution_snapshot = dict(execution_snapshot or {})
    merged_execution_snapshot.update(
        {
            "submitted_price": result.submitted_price,
            "fee_rate_bps": result.fee_rate_bps,
        }
    )
    if result.order_id:
        track.ledger.record_submission(
            result.order_id,
            window.end_ts,
            direction=signal.direction.value,
            amount_usdc=amount_usdc,
            submitted_price=result.submitted_price or signal.market_price,
            market_price=signal.market_price,
            model=signal.model_used,
            dry_run=track.orderer.dry_run,
            role=role,
            track=track.name,
            phase="ENTRY",
            client_order_id=client_order_id,
            details={
                "feature_snapshot": feature_snapshot,
                "execution_snapshot": merged_execution_snapshot,
            },
        )

    fill_result = result
    if result.order_id and result.status in {OrderStatus.SUBMITTED, OrderStatus.PENDING}:
        track.state.record_pending_order(
            direction=signal.direction.value,
            amount=amount_usdc,
            price=result.submitted_price or signal.market_price,
            model=signal.model_used,
            order_id=result.order_id,
            dry_run=track.orderer.dry_run,
            window_end_ts=window.end_ts,
            role=role,
        )
        if track.stats is not None:
            track.stats.last_order_status = result.status.value
            track.stats.last_order_id = result.order_id or ""
            track.stats.last_market_slug = market_slug
            track.stats.note(
                f"Submitted {signal.direction.value} ${amount_usdc:.2f} @ {signal.market_price:.3f}; awaiting fill"
            )
        fill_result = await track.orderer.wait_for_fill(
            result.order_id,
            _bounded_fill_deadline(window, timeout_seconds=20),
        )
        track.state.clear_pending_order(result.order_id)

    if fill_result.status == OrderStatus.CANCELLED:
        if fill_result.order_id:
            track.ledger.record_cancellation(
                fill_result.order_id,
                window.end_ts,
                status=fill_result.status.value,
                details={"phase": "ENTRY", "role": role, "track": track.name},
            )
        if track.stats is not None:
            track.stats.last_order_status = fill_result.status.value
            track.stats.last_order_id = fill_result.order_id or ""
            track.stats.last_market_slug = market_slug
            track.stats.note(
                f"Entry cancelled before fill | {signal.direction.value} | requested ${amount_usdc:.2f}"
            )
        LOGGER.info(
            "Entry cancelled before fill | order=%s | track=%s | role=%s",
            fill_result.order_id,
            track.name,
            role,
        )
        return None

    if fill_result.status not in {OrderStatus.FILLED, OrderStatus.PARTIAL}:
        if track.stats is not None:
            track.stats.last_order_status = fill_result.status.value
            track.stats.last_order_id = fill_result.order_id or ""
            track.stats.last_market_slug = market_slug
            track.stats.note(f"Entry did not reach a tradable fill state: {fill_result.status.value}")
        return None

    filled_price = fill_result.avg_price or fill_result.submitted_price or signal.market_price
    filled_amount_usdc = _filled_amount_usdc(fill_result, amount_usdc)
    if fill_result.order_id:
        track.ledger.record_fill(
            fill_result.order_id,
            window.end_ts,
            fill_result.status.value,
            filled_size=fill_result.filled_size,
            avg_price=filled_price,
            details={
                "phase": "ENTRY",
                "role": role,
                "track": track.name,
                "requested_amount_usdc": amount_usdc,
                "filled_amount_usdc": filled_amount_usdc,
            },
        )
    track.ledger.record_trade(
        window_end_ts=window.end_ts,
        direction=signal.direction.value,
        amount_usdc=filled_amount_usdc,
        price=filled_price,
        edge=signal.edge,
        ev=signal.ev,
        model=signal.model_used,
        order_id=fill_result.order_id,
        dry_run=track.orderer.dry_run,
        market_url=window.market_url,
        market_slug=market_slug,
        asset=asset,
        open_price=window.open_price,
        role=role,
        track=track.name,
        feature_snapshot=feature_snapshot,
        execution_snapshot={
            **merged_execution_snapshot,
            "fill_status": fill_result.status.value,
            "filled_price": filled_price,
            "filled_amount_usdc": filled_amount_usdc,
        },
    )
    track.state.record_trade(
        direction=signal.direction.value,
        amount=filled_amount_usdc,
        price=filled_price,
        edge=signal.edge,
        model=signal.model_used,
        order_id=fill_result.order_id,
        dry_run=track.orderer.dry_run,
        window_end_ts=window.end_ts,
        role=role,
        entry_regime=getattr(signal, "entry_regime", ""),
    )
    track.state.last_trade_high_conviction = signal.market_price >= HIGH_CONVICTION_ENTRY_THRESHOLD
    if track.state.current_window is not None and fill_result.order_id:
        position = create_position_from_trade(
            order_id=fill_result.order_id,
            window=track.state.current_window,
            direction=signal.direction.value,
            size_usdc=filled_amount_usdc,
            entry_price=filled_price,
            entry_ts=time.time(),
            role=role,
            fee_rate_bps=fill_result.fee_rate_bps,
            seconds_remaining=track.state.current_window.seconds_remaining(int(time.time())),
            signal=signal,
            feature_snapshot=feature_snapshot,
        )
        track.state.current_window.add_position(position)
        await place_resting_exits(
            track, window=track.state.current_window, position=position, asset=asset
        )
    if track.stats is not None:
        suffix = "" if role == "PRIMARY" else f" + {role.lower()}"
        fill_label = "Filled" if fill_result.status == OrderStatus.FILLED else "Partially filled"
        track.stats.last_order_status = fill_result.status.value
        track.stats.last_order_id = fill_result.order_id or ""
        track.stats.last_market_slug = market_slug
        track.stats.note(
            f"{fill_label} {signal.direction.value} ${filled_amount_usdc:.2f} @ {filled_price:.3f}{suffix}"
        )
    LOGGER.info(
        "Placed %s%s | edge=%.3f | theo=%.3f | market=%.3f | bet=$%.2f | order=%s",
        signal.direction.value,
        "" if role == "PRIMARY" else f" ({role})",
        signal.edge,
        signal.theo_prob,
        signal.market_price,
        filled_amount_usdc,
        fill_result.order_id,
    )
    return SubmittedTrade(
        role=role,
        signal=signal,
        amount_usdc=filled_amount_usdc,
        order_id=fill_result.order_id or "",
        submitted_price=filled_price,
        fee_rate_bps=fill_result.fee_rate_bps,
    )


async def resolve_submitted_trades(
    *,
    market_client,
    asset: str,
    prefer_binance: bool,
    window: WindowState,
    submitted_trades: list[SubmittedTrade],
    ledger: Ledger,
    state: BotState,
    stats: RuntimeStats | None,
    primary_signal,
    once: bool,
) -> None:
    primary_filled = any(trade.role == "PRIMARY" for trade in submitted_trades)
    resolution = await wait_for_market_resolution(
        market_client,
        asset,
        extract_market_slug(window.market_url),
        window.end_ts,
        prefer_binance=prefer_binance,
        timeout_seconds=90 if once else 180,
    )
    if resolution is None:
        if stats is not None:
            stats.last_resolution = "PENDING"
            stats.note(f"Resolution pending for {extract_market_slug(window.market_url)}")
        LOGGER.info(
            "Resolution pending for %s; leaving trade unresolved",
            extract_market_slug(window.market_url),
        )
        return

    resolved_up = bool(resolution["resolved_up"])
    for trade in submitted_trades:
        if ledger.has_terminal_record(trade.order_id):
            continue
        ledger.record_resolution(
            trade.order_id,
            window.end_ts,
            resolved_up,
            trade.signal.direction.value,
            trade.amount_usdc,
            trade.signal.market_price,
            final_price=resolution.get("final_price"),
            price_to_beat=resolution.get("price_to_beat"),
            closed_time=resolution.get("closed_time"),
            uma_resolution_status=resolution.get("uma_resolution_status"),
        )
        state.record_resolution(trade.order_id, resolved_up)

    if stats is not None:
        if primary_filled:
            stats.last_resolution = (
                "WIN"
                if (primary_signal.direction.value == "UP" and resolved_up)
                or (primary_signal.direction.value == "DOWN" and not resolved_up)
                else "LOSS"
            )
        else:
            stats.last_resolution = "HEDGE"
        stats.note(
            f"Resolved {stats.last_resolution} | final {resolution.get('final_price', '-')}"
        )

    summary = ledger.get_daily_summary()
    LOGGER.info(
        "Daily: %sW/%sL | PnL: $%.2f",
        summary["wins"],
        summary["losses"],
        summary["total_pnl"],
    )


async def trading_loop(
    tracks: list[ModelTrack],
    shared_state: BotState,
    market_client,
    *,
    once: bool,
    asset: str,
    live_mode: bool = False,
    stop_event: asyncio.Event | None = None,
) -> None:
    # Derived helpers — use BOTH track for shared signal generation
    both_track = next(t for t in tracks if t.name == "BOTH")
    finder = both_track.finder
    orderer = both_track.orderer
    model = both_track.model
    stats = both_track.stats
    ledger = both_track.ledger

    force_interval_trades = force_interval_trades_enabled(live_mode=live_mode)
    last_window_end_ts: int | None = None
    last_finished_window: tuple[int, str | None] | None = None
    prev_window_open_px: float = 0.0
    _last_market_id: str | None = None
    engine_scores: dict[str, float] = {}

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        if kill_switch_engaged(live_mode=live_mode):
            for track in tracks:
                if track.stats is not None:
                    track.stats.last_order_status = "HALTED"
                    track.stats.note("Kill switch engaged")
            LOGGER.error("Kill switch engaged; stopping trading loop")
            emit_alert(
                "CRITICAL",
                "Kill switch engaged",
                live_mode=live_mode,
                model=model,
                asset=asset,
            )
            if live_mode:
                for track in tracks:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(track.orderer.cancel_all)
            return

        if shared_state.current_window is None or shared_state.current_window.get_phase(int(time.time())) == WindowPhase.EXPIRED:
            # Close any open positions on the expired window before moving on
            # (check each track's own window state)
            for track in tracks:
                track_window = track.state.current_window
                if track_window is not None and track_window.has_open_positions():
                    LOGGER.info(
                        "[%s] Window %d expired with %d open positions — closing all",
                        track.name,
                        track_window.end_ts,
                        len(track_window.get_open_positions()),
                    )
                    exp_up_book = exp_down_book = None
                    with contextlib.suppress(Exception):
                        exp_up_book, exp_down_book = await refresh_window_books(track.orderer, track_window)
                    for pos in track_window.get_open_positions():
                        if pos.direction == "UP":
                            current_price = (
                                exp_up_book.best_bid
                                if exp_up_book and exp_up_book.best_bid
                                else pos.entry_price
                            )
                        else:
                            current_price = (
                                exp_down_book.best_bid
                                if exp_down_book and exp_down_book.best_bid
                                else pos.entry_price
                            )
                        fee_usdc = estimate_trade_fee_usdc(
                            asset=asset,
                            market_price=current_price,
                            bet_size=pos.size_usdc,
                        )
                        realized_pnl = pos.close(
                            exit_price=current_price,
                            exit_ts=time.time(),
                            reason=ExitReason.WINDOW_CLOSE,
                            fee_usdc=fee_usdc,
                        )
                        track.state.realized_pnl += realized_pnl
                        track.state.daily_pnl += realized_pnl
                        track.ledger.record_early_exit(
                            order_id=pos.order_id,
                            window_end_ts=track_window.end_ts,
                            pnl=realized_pnl + fee_usdc,
                            exit_price=current_price,
                            reason="WINDOW_CLOSE",
                        )
                        track.state.record_early_exit(
                            order_id=pos.order_id,
                            pnl=realized_pnl,
                            won=realized_pnl > 0,
                        )
                        if track.stats is not None:
                            track.stats.note(f"Window close exit | {pos.direction} | PnL: ${realized_pnl:+.2f}")
                        LOGGER.info(
                            "[%s] Closed position %s on window expiry | %s | PnL: $%.2f",
                            track.name, pos.order_id, pos.direction, realized_pnl,
                        )
            try:
                new_window = await asyncio.to_thread(finder.find_active_window)
            except Exception as exc:
                for track in tracks:
                    if track.stats is not None:
                        track.stats.last_no_trade_reason = "MARKET_FETCH_ERROR"
                        track.stats.note(f"Market discovery error: {exc}")
                LOGGER.warning("Market discovery error: %s", exc)
                if once:
                    return
                await asyncio.sleep(10)
                continue
            shared_state.current_window = new_window
            for track in tracks:
                track.state.current_window = clone_window(new_window)
                if track.stats is not None:
                    track.stats.update_window(track.state.current_window)
            if new_window is None:
                shared_state.clear_polymarket_book()
                for track in tracks:
                    track.state.clear_polymarket_book()
                    if track.stats is not None:
                        track.stats.note("No active market window found")
                LOGGER.info("No active market window found")
                if once:
                    return
                await asyncio.sleep(10)
                continue

        window = shared_state.current_window
        now = int(time.time())
        phase = window.get_phase(now)
        waiting_conviction_active = False

        if stats is not None:
            stats.update_window(window, phase=phase)
            stats.last_market_slug = extract_market_slug(window.market_url)

        # Window boundary detection
        if last_window_end_ts is not None and window.end_ts != last_window_end_ts:
            LOGGER.info(
                "=== Window boundary | old=%d new=%d ===",
                last_window_end_ts, window.end_ts,
            )
            if last_finished_window is not None:
                finished_end_ts, finished_slug = last_finished_window
                if finished_end_ts == last_window_end_ts and finished_slug:
                    asyncio.create_task(_log_window_outcome_once(
                        market_client,
                        asset,
                        finished_slug,
                        finished_end_ts,
                        [track.ledger for track in tracks],
                    ))
            if prev_window_open_px > 0 and shared_state.btc_price > 0:
                shared_state.note_finished_window(prev_window_open_px, shared_state.btc_price)
        last_window_end_ts = window.end_ts
        prev_window_open_px = window.open_price
        last_finished_window = (
            window.end_ts,
            extract_market_slug(window.market_url) if window.market_url else None,
        )

        if window.market_id != _last_market_id:
            _last_market_id = window.market_id
            ts_str = time.strftime("%H:%M:%S")
            price_str = f"BTC ${shared_state.btc_price:,.0f}" if shared_state.btc_price > 0 else "BTC —"
            secs = window.seconds_remaining(now)
            mins, secs_r = divmod(secs, 60)
            bar = f"── {ts_str} ──── WINDOW {time.strftime('%Y%m%d-%H:%M', time.localtime(window.start_ts))} | {price_str} | {mins}m {secs_r:02d}s "
            bar = bar.ljust(90, "─")
            print(bar, flush=True)

        # Always fetch order books for position exit checking (regardless of phase)
        up_book = down_book = None
        with contextlib.suppress(Exception):
            up_book, down_book = await refresh_window_books(orderer, window)

        if phase == WindowPhase.WAITING:
            if up_book is None or down_book is None:
                reason_code, reason_detail = _book_unavailable_reason(shared_state)
                emit_alert_throttled(
                    "ERROR" if reason_code == "STALE_POLYMARKET_BOOK" else "WARNING",
                    reason_detail,
                    throttle_seconds=120.0,
                    alert_key=f"waiting:{reason_code}:{window.end_ts}",
                    asset=asset,
                    window_end_ts=window.end_ts,
                    reason_code=reason_code,
                    book_age_seconds=shared_state.polymarket_book_age_seconds(),
                )
                if stats is not None:
                    stats.last_no_trade_reason = reason_code
                    stats.last_regime_candidate = "NONE"
                    stats.note(reason_detail)
                LOGGER.warning(
                    "Waiting for entry window | %s | end=%s | Price=$%.2f | source=%s",
                    reason_detail,
                    window.end_ts,
                    shared_state.btc_price,
                    shared_state.price_source or "-",
                )
                status = build_runtime_status(tracks, shared_state, engine_scores)
                write_runtime_status_file(status)
                if once:
                    return
                has_open = any(
                    t.state.current_window is not None and t.state.current_window.has_open_positions()
                    for t in tracks
                )
                await asyncio.sleep(1 if has_open else 5)
                continue
            window.set_open_price(shared_state.btc_price)
            if up_book is not None or down_book is not None:
                shared_state.update_polymarket_book(
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
            polymarket_features = shared_state.polymarket_features()
            if stats is not None:
                stats.open_price = window.open_price or None
                stats.current_up_ask = up_book.best_ask if up_book is not None else None
                stats.current_down_ask = down_book.best_ask if down_book is not None else None
                stats.current_up_bid = up_book.best_bid if up_book is not None else None
                stats.current_down_bid = down_book.best_bid if down_book is not None else None
                stats.last_strongest_side = str(polymarket_features.get("strongest_side", "NONE"))
                stats.last_regime_candidate = "NONE"
                stats.note("Waiting for entry window")
            early_conviction, early_reason = should_enter_on_waiting_conviction(
                up_book=up_book,
                down_book=down_book,
                state=shared_state,
                window=window,
            )
            if not early_conviction:
                if stats is not None:
                    stats.last_no_trade_reason = early_reason
                LOGGER.info(
                    "Waiting for entry window | %s | end=%s | Price=$%.2f | source=%s | up=%s | down=%s",
                    early_reason,
                    window.end_ts,
                    shared_state.btc_price,
                    shared_state.price_source or "-",
                    f"{up_book.best_ask:.3f}" if up_book and up_book.best_ask is not None else "-",
                    f"{down_book.best_ask:.3f}" if down_book and down_book.best_ask is not None else "-",
                )
                # Run position exits even during WAITING phase
                for track in tracks:
                    track_window = track.state.current_window
                    if track_window is not None and track_window.has_open_positions():
                        up_bid = up_book.best_bid if up_book and up_book.best_bid else None
                        down_bid = down_book.best_bid if down_book and down_book.best_bid else None
                        if up_bid is not None or down_bid is not None:
                            closed = await check_position_exits(
                                track,
                                asset=asset,
                                window=track_window,
                                up_price=up_bid or 0.0,
                                down_price=down_bid or 0.0,
                            )
                            for pos in closed:
                                prev = track.pnl_history[-1] if track.pnl_history else 0.0
                                track.pnl_history.append(round(prev + pos.pnl_usdc, 4))
                # Write status even while waiting
                status = build_runtime_status(tracks, shared_state, engine_scores)
                write_runtime_status_file(status)
                if once:
                    return
                # Poll fast when positions are open (reduces SL slippage), slow otherwise
                has_open = any(
                    t.state.current_window is not None and t.state.current_window.has_open_positions()
                    for t in tracks
                )
                await asyncio.sleep(1 if has_open else 5)
                continue
            if stats is not None:
                stats.last_regime_candidate = "LATE_CONVICTION"
                stats.note(
                    f"Early conviction trigger | {early_reason} | "
                    f"up={f'{up_book.best_ask:.3f}' if up_book and up_book.best_ask is not None else '-'} "
                    f"down={f'{down_book.best_ask:.3f}' if down_book and down_book.best_ask is not None else '-'}"
                )
            waiting_conviction_active = True
            LOGGER.info(
                "Early conviction trigger | end=%s | Price=$%.2f | source=%s | up=%s | down=%s",
                window.end_ts,
                shared_state.btc_price,
                shared_state.price_source or "-",
                f"{up_book.best_ask:.3f}" if up_book and up_book.best_ask is not None else "-",
                f"{down_book.best_ask:.3f}" if down_book and down_book.best_ask is not None else "-",
            )

        if phase in {WindowPhase.TOO_LATE, WindowPhase.EXPIRED}:
            shared_state.clear_polymarket_book()
            for track in tracks:
                track.state.clear_polymarket_book()
            if window.order_id and not window.traded:
                await asyncio.to_thread(orderer.cancel, window.order_id)
                if stats is not None:
                    stats.last_order_status = "CANCELLED"
                    stats.note(f"Cancelled stale order {window.order_id}")
            elif stats is not None:
                stats.note(f"Window is no longer tradeable: {phase.value}")
            LOGGER.info("Window is no longer tradeable: %s", phase.value)
            if once:
                return
            await asyncio.sleep(max(1, window.end_ts - now + 1))
            continue

        # -- Generate shared market snapshot and independent per-track signals --
        engine_scores = compute_all_engine_scores(shared_state, window)

        async def _check_exits_tick() -> None:
            """Run exit checks for all tracks on each snipe poll tick."""
            tick_up, tick_down = None, None
            with contextlib.suppress(Exception):
                tick_up, tick_down = await refresh_window_books(orderer, window)
            for _track in tracks:
                _tw = _track.state.current_window
                if _tw is not None and _tw.has_open_positions():
                    _up = tick_up.best_bid if tick_up and tick_up.best_bid else None
                    _dn = tick_down.best_bid if tick_down and tick_down.best_bid else None
                    if _up is not None or _dn is not None:
                        closed = await check_position_exits(
                            _track,
                            asset=asset,
                            window=_tw,
                            up_price=_up or 0.0,
                            down_price=_dn or 0.0,
                        )
                        for pos in closed:
                            prev = _track.pnl_history[-1] if _track.pnl_history else 0.0
                            _track.pnl_history.append(round(prev + pos.pnl_usdc, 4))

        selections = await select_snipe_signals(
            window=window,
            state=shared_state,
            orderer=both_track.orderer,
            tracks=tracks,
            asset=asset,
            force_trade=force_interval_trades,
            live_mode=live_mode,
            allow_waiting_conviction=waiting_conviction_active,
            on_tick_callback=_check_exits_tick,
        )
        engine_scores = next(
            (
                dict(selection.probabilities)
                for selection in selections.values()
                if getattr(selection, "probabilities", None)
            ),
            engine_scores,
        )

        # -- Fan out the track-specific decisions --
        for track in tracks:
            selection = selections.get(track.name)
            await _evaluate_track(
                track,
                selection,
                engine_scores,
                window=window,
                asset=asset,
                up_book=selection.up_book if selection is not None else up_book,
                down_book=selection.down_book if selection is not None else down_book,
            )

        # -- Write status file --
        status = build_runtime_status(tracks, shared_state, engine_scores)
        write_runtime_status_file(status)

        if once:
            return
        await asyncio.sleep(1)


def print_banner(*, mode_label: str, model: str, asset: str, bankroll: float) -> None:
    print("═══════════════════════════════════════════════════")
    print(f"  Polymarket {asset} 5-Min Bot")
    print(f"  Mode:         {mode_label}")
    print(f"  Model:        {model}")
    print(f"  Start bank:   ${bankroll:.2f} USDC")
    print(f"  Max bet:      ${MAX_BET_USDC:.2f} USDC")
    print(f"  Min bet:      ${MIN_BET_USDC:.2f} USDC")
    print(f"  Bet fraction: {BANKROLL_RISK_FRACTION * 100:.0f}% of free bank")
    print(f"  Edge req:     {EDGE_THRESHOLD * 100:.0f}¢")
    print(f"  Entry window: {ENTRY_WINDOW_START}s–{ENTRY_WINDOW_END}s before close")
    print("═══════════════════════════════════════════════════")


class DemoClient:
    def __init__(self, window_seconds: int = 25, *, start_ts: float | None = None):
        self.window_seconds = window_seconds
        self.start_ts = time.time() if start_ts is None else start_ts

    def get_markets(self):
        now = int(time.time())
        end_ts = now + self.window_seconds
        return {
            "data": [
                {
                    "market_slug": f"btc-updown-5m-{end_ts}",
                    "condition_id": "demo-condition",
                    "end_date_iso": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(end_ts)),
                    "tokens": [
                        {"outcome": "Up", "token_id": "demo-up"},
                        {"outcome": "Down", "token_id": "demo-down"},
                    ],
                }
            ]
        }

    def get_order_book(self, token_id: str):
        if token_id == "demo-up":
            best_ask = 0.60
            best_bid = 0.59
        else:
            best_ask = 0.40
            best_bid = 0.39

        asks = [
            {"price": f"{best_ask + (level * 0.01):.2f}"}
            for level in range(12)
        ]
        bids = [
            {"price": f"{max(0.01, best_bid - (level * 0.01)):.2f}"}
            for level in range(12)
        ]
        return {"asks": asks, "bids": bids}

    def get_market_resolution(self, slug: str) -> dict | None:
        try:
            end_ts = int(extract_market_slug(slug).rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            return None

        if time.time() < end_ts:
            return None

        window_index = max(0, int((end_ts - int(self.start_ts)) // self.window_seconds) - 1)
        resolved_up = (window_index % 2) == 0
        return {
            "resolved_up": resolved_up,
            "closed": True,
            "closed_time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(end_ts)),
            "final_price": None,
            "price_to_beat": None,
            "uma_resolution_status": "SIMULATED",
        }


class SimulatedFeed:
    def __init__(
        self,
        state_or_states: BotState | list[BotState],
        *,
        base_price: float = 82000.0,
        window_seconds: int = 25,
        start_ts: float | None = None,
    ):
        self.states = (
            state_or_states
            if isinstance(state_or_states, list)
            else [state_or_states]
        )
        self.base_price = base_price
        self.window_seconds = window_seconds
        self._start = time.time() if start_ts is None else start_ts

    async def connect(self) -> None:
        while True:
            elapsed = time.time() - self._start
            window_index = int(elapsed // self.window_seconds)
            phase_elapsed = elapsed % self.window_seconds
            direction = 1 if window_index % 2 == 0 else -1
            drift = direction * phase_elapsed * 32.0
            oscillation = math.sin(elapsed / 2.5) * 12.0
            price = self.base_price + (window_index * 8.0) + drift + oscillation
            apply_price(self.states, price)
            await asyncio.sleep(1)


def fetch_binance_price_snapshot(asset: str) -> float:
    try:
        price = fetch_binance_spot_price(asset, logger=LOGGER)
        if price is not None:
            return price
    except Exception as exc:
        LOGGER.warning(
            "Binance price snapshot failed for %s, trying Coinbase fallback: %s",
            asset,
            exc,
        )
    coinbase_price = fetch_coinbase_spot_price(asset)
    if coinbase_price is None:
        raise RuntimeError(f"No spot price fallback available for {asset}")
    return coinbase_price


def fetch_reference_price_snapshot(asset: str) -> float:
    prices: list[float] = []
    for loader in (
        fetch_kraken_spot_price,
        fetch_gemini_spot_price,
        fetch_coinbase_spot_price,
        fetch_bybit_spot_price,
    ):
        try:
            price = loader(asset)
        except Exception as exc:
            LOGGER.warning("Reference price snapshot failed for %s via %s: %s", asset, loader.__name__, exc)
            continue
        if price is None or price <= 0:
            continue
        prices.append(price)
    if not prices:
        raise RuntimeError(f"No reference price sources available for {asset}")
    return float(statistics.median(prices))


def fetch_binance_agg_trade_price(asset: str, target_ts_ms: int) -> float | None:
    return _fetch_binance_agg_trade_price(
        asset,
        target_ts_ms,
        binance_rest_symbols=BINANCE_REST_SYMBOLS,
        fetch_binance_rest_json=fetch_binance_rest_json,
        logger=LOGGER,
    )


def resolve_with_binance(asset: str, window_end_ts: int) -> dict | None:
    return _resolve_with_binance(
        asset,
        window_end_ts,
        fetch_binance_agg_trade_price_fn=fetch_binance_agg_trade_price,
    )


def resolve_market_outcome(
    market_client,
    *,
    asset: str,
    market_slug: str,
    window_end_ts: int,
    prefer_binance: bool,
) -> dict | None:
    return _resolve_market_outcome(
        market_client,
        asset=asset,
        market_slug=market_slug,
        window_end_ts=window_end_ts,
        prefer_binance=prefer_binance,
        resolve_with_binance_fn=resolve_with_binance,
    )


def build_demo_state_from_live_data(asset: str) -> tuple[BotState, object, str]:
    try:
        price = fetch_reference_price_snapshot(asset)
        state = BotState(btc_price=price, btc_price_updated_ts=time.time())
        client = PublicPolymarketClient(asset=asset)
        finder = MarketFinder(client, asset=asset)
        window = finder.find_active_window()
        if window is None:
            raise RuntimeError(f"No active {asset} 5-minute Polymarket window found")
        window.open_price = price * 0.998
        state.current_window = window
        return state, client, "shadow-live"
    except Exception as exc:
        LOGGER.warning("Falling back to local demo data: %s", exc)
        state = BotState(btc_price=82164.0, btc_price_updated_ts=time.time())
        return state, DemoClient(window_seconds=25), "local-fallback"


async def run_with_optional_timeout(
    *coroutines,
    duration: int | None,
    stop_event: asyncio.Event | None = None,
) -> None:
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    stop_task = (
        asyncio.create_task(stop_event.wait())
        if stop_event is not None
        else None
    )
    try:
        pending = set(tasks)
        if stop_task is not None:
            pending.add(stop_task)
        done, pending = await asyncio.wait(
            pending,
            timeout=duration,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task is not None and stop_task in done:
            LOGGER.info("Stop signal received; stopping bot")
        elif duration is not None and not done:
            LOGGER.info("Runtime limit reached after %ss; stopping bot", duration)
        else:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()
        if stop_task is not None:
            stop_task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if stop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task


async def price_snapshot_loop(
    states: BotState | list[BotState],
    asset: str,
    *,
    interval: float = 3.0,
) -> None:
    targets = states if isinstance(states, list) else [states]

    async def fetch_snapshot(source: str):
        if source == "KRAKEN":
            return source, await asyncio.to_thread(fetch_kraken_spot_price, asset)
        if source == "GEMINI":
            return source, await asyncio.to_thread(fetch_gemini_spot_price, asset)
        if source == "BYBIT":
            return source, await asyncio.to_thread(fetch_bybit_spot_price, asset)
        return source, await asyncio.to_thread(fetch_coinbase_spot_price, asset)

    while True:
        tasks = [
            asyncio.create_task(fetch_snapshot("KRAKEN")),
            asyncio.create_task(fetch_snapshot("GEMINI")),
            asyncio.create_task(fetch_snapshot("COINBASE")),
            asyncio.create_task(fetch_snapshot("BYBIT")),
        ]
        any_success = False
        try:
            for task in asyncio.as_completed(tasks):
                try:
                    source, price = await task
                except Exception as exc:
                    LOGGER.warning("Price snapshot refresh failed for %s: %s", asset, exc)
                    continue
                if price is None or price <= 0:
                    continue
                apply_price(targets, price, source=source)
                any_success = True
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        if not any_success:
            LOGGER.warning("All price snapshot refreshes failed for %s", asset)
            emit_alert_throttled(
                "ERROR",
                "All price snapshot refreshes failed",
                throttle_seconds=120.0,
                alert_key=f"price_snapshot_failed:{asset}",
                asset=asset,
            )
        await asyncio.sleep(interval)


async def kalshi_refresh_loop(
    states: BotState | list[BotState],
    *,
    interval: float = 45.0,
) -> None:
    """Poll Kalshi BTC cross-venue probability (public REST, cached 45s server-side too)."""
    targets = states if isinstance(states, list) else [states]
    while True:
        try:
            prob = await asyncio.to_thread(get_btc_yes_probability)
        except Exception as exc:
            LOGGER.warning("Kalshi refresh failed: %s", exc)
            prob = None
        if prob is not None:
            now = time.time()
            for target in targets:
                target.kalshi_up_probability = prob
                target.kalshi_updated_ts = now
        await asyncio.sleep(interval)


async def main() -> None:
    args = parse_args()
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    starting_bankroll = (
        STARTING_BANKROLL_USDC if args.bankroll is None else args.bankroll
    )
    if starting_bankroll <= 0:
        raise SystemExit("--bankroll must be greater than 0")
    if args.asset not in ASSET_KEYWORDS:
        raise SystemExit(
            f"Unsupported asset {args.asset}. Supported assets: "
            f"{', '.join(sorted(ASSET_KEYWORDS))}"
        )
    if args.live and len(args.models) > 1:
        raise SystemExit("Running multiple models at once is only supported in dry-run/demo modes")
    dashboard_enabled = (
        args.dashboard
        if args.dashboard is not None
        else (sys.stdout.isatty() and not args.once)
    )
    dashboard_enabled = dashboard_enabled and not args.once
    log_level = logging.DEBUG if args.debug else (
        logging.WARNING if dashboard_enabled else getattr(logging, LOG_LEVEL, logging.INFO)
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    global ALERTER
    ALERTER = AlertManager(ALERTS_FILE, ALERT_WEBHOOK_URL)

    with process_lock(BOT_LOCK_FILE):
        if not args.live and not args.demo and not args.demo_local and not args.shadow_live:
            args.shadow_live = True

        if args.live and not args.yes_live:
            confirm = input('Type "YES" to confirm live trading: ')
            if confirm.strip() != "YES":
                raise SystemExit("Live trading aborted")
        if args.live:
            if kill_switch_engaged(live_mode=True):
                emit_alert("CRITICAL", "Live trading aborted: kill switch file is present")
                raise SystemExit("Live trading aborted: kill switch file is present")

        if args.demo or args.demo_local:
            simulation_start_ts = time.time()
            if args.demo_local:
                initial_state = BotState(btc_price=82164.0, btc_price_updated_ts=time.time())
                client = DemoClient(window_seconds=25, start_ts=simulation_start_ts)
                demo_mode = "local-sim"
            else:
                initial_state, client, demo_mode = build_demo_state_from_live_data(args.asset)
            tracks = build_model_tracks(
                client=client,
                asset=args.asset,
                mode_label="DEMO",
                market_source=demo_mode,
                dry_run=True,
                initial_price=initial_state.btc_price,
                initial_price_updated_ts=initial_state.btc_price_updated_ts,
                initial_window=initial_state.current_window,
                starting_bankroll=starting_bankroll,
            )
            shared_state = BotState(
                btc_price=initial_state.btc_price,
                btc_price_updated_ts=initial_state.btc_price_updated_ts,
                current_window=clone_window(initial_state.current_window),
                starting_bankroll_usdc=starting_bankroll,
            )
            states = [track.state for track in tracks] + [shared_state]
            if not dashboard_enabled:
                print_banner(
                    mode_label="DEMO",
                    model=",".join(args.models),
                    asset=args.asset,
                    bankroll=starting_bankroll,
                )
            LOGGER.info("Demo mode: %s", demo_mode)
            live_feeds = (
                [
                    KrakenFeed(states, asset=args.asset),
                    GeminiFeed(states, asset=args.asset),
                    CoinbaseFeed(states, asset=args.asset),
                    BybitFeed(states, asset=args.asset),
                ]
                if demo_mode == "shadow-live"
                else []
            )
            feed = (
                live_feeds
                if demo_mode == "shadow-live"
                else SimulatedFeed(states, start_ts=simulation_start_ts)
            )
            demo_duration = 45 if args.duration is None else args.duration
            coroutines = [
                trading_loop(
                    tracks,
                    shared_state,
                    client,
                    once=args.once,
                    asset=args.asset,
                    live_mode=False,
                    stop_event=stop_event,
                )
            ]
            if not args.once:
                if demo_mode == "shadow-live":
                    coroutines.extend(live_feed.connect() for live_feed in feed)
                else:
                    coroutines.append(feed.connect())
                if demo_mode == "shadow-live":
                    coroutines.append(price_snapshot_loop(states, args.asset))
                    coroutines.append(kalshi_refresh_loop(states))
                coroutines.append(
                    unresolved_resolution_loop(
                        tracks,
                        client,
                        interval=UNRESOLVED_SWEEP_INTERVAL_SECONDS,
                    )
                )
            if RUNTIME_STATUS_FILE.strip():
                coroutines.append(
                    runtime_status_loop(
                        tracks,
                        path=RUNTIME_STATUS_FILE,
                        interval=RUNTIME_STATUS_UPDATE_SECONDS,
                        shared_state=shared_state,
                    )
                )
            await run_with_optional_timeout(
                *coroutines,
                duration=None if args.once else demo_duration,
                stop_event=stop_event,
            )
            if RUNTIME_STATUS_FILE.strip():
                final_status = build_runtime_status(tracks, shared_state, {})
                write_runtime_status_file(final_status)
            for track in tracks:
                LOGGER.info(
                    "Session summary [%s]: trades=%s resolved=%s wins=%s losses=%s pnl=$%.2f bank=$%.2f avail=$%.2f",
                    track.name,
                    track.state.total_trades,
                    track.state.wins + track.state.losses,
                    track.state.wins,
                    track.state.losses,
                    track.state.daily_pnl,
                    track.state.current_bankroll_usdc(),
                    track.state.available_bankroll_usdc(),
                )
            return

        validate_config(
            live_override=args.live,
            require_trading_credentials=args.live,
        )
        ledger = Ledger()

        if args.live:
            client = PolymarketClient(
                POLYMARKET_PRIVATE_KEY,
                POLYMARKET_API_KEY,
                POLYMARKET_SECRET,
                POLYMARKET_PASSPHRASE,
                asset=args.asset,
                signature_type=POLYMARKET_SIGNATURE_TYPE,
                funder=POLYMARKET_FUNDER,
            )
            mode_label = "LIVE"
            dry_run = False
            try:
                preflight = perform_live_preflight(client, starting_bankroll)
            except SystemExit as exc:
                emit_alert("CRITICAL", "Live preflight failed", error=str(exc), asset=args.asset)
                raise
            if preflight.effective_bankroll_usdc < starting_bankroll:
                LOGGER.warning(
                    "Capping live bankroll from $%.2f to $%.2f based on account state",
                    starting_bankroll,
                    preflight.effective_bankroll_usdc,
                )
            starting_bankroll = preflight.effective_bankroll_usdc
            LOGGER.info(
                "Live preflight passed | balance=$%.2f allowance=$%.2f available=$%.2f open_orders=%s",
                preflight.balance_usdc,
                preflight.allowance_usdc,
                preflight.available_usdc,
                preflight.open_order_count,
            )
            reconciliation = reconcile_live_ledger(client, ledger)
            LOGGER.info(
                "Live ledger reconciliation | resolved=%s cancelled=%s fills=%s unresolved=%s untracked=%s",
                reconciliation.resolved_count,
                reconciliation.cancelled_count,
                reconciliation.fill_count,
                reconciliation.unresolved_count,
                reconciliation.untracked_trade_count,
            )
            if reconciliation.unresolved_count or reconciliation.untracked_trade_count:
                emit_alert(
                    "CRITICAL",
                    "Live startup aborted by reconciliation",
                    unresolved=reconciliation.unresolved_count,
                    untracked_trade_count=reconciliation.untracked_trade_count,
                    reconciled_fills=reconciliation.fill_count,
                )
                raise SystemExit(
                    "Live startup aborted: reconciliation found unresolved or untracked live trades. "
                    "Review the ledger and exchange account state before restarting live trading."
                )
        else:
            client = PublicPolymarketClient(asset=args.asset)
            mode_label = "SHADOW LIVE"
            dry_run = True

        initial_price = 0.0
        initial_price_updated_ts = 0.0
        initial_snapshot_error: Exception | None = None
        try:
            initial_price = fetch_reference_price_snapshot(args.asset)
            initial_price_updated_ts = time.time()
        except Exception as exc:
            initial_snapshot_error = exc
            LOGGER.warning("Initial %s price snapshot failed: %s", args.asset, exc)
        tracks = build_model_tracks(
            client=client,
            asset=args.asset,
            mode_label=mode_label,
            market_source="kraken+gemini+coinbase+polymarket",
            dry_run=dry_run,
            initial_price=initial_price,
            initial_price_updated_ts=initial_price_updated_ts,
            starting_bankroll=starting_bankroll,
        )
        shared_state = BotState(
            btc_price=initial_price,
            btc_price_updated_ts=initial_price_updated_ts,
            starting_bankroll_usdc=starting_bankroll,
        )
        states = [track.state for track in tracks] + [shared_state]
        if initial_snapshot_error is not None:
            for track in tracks:
                track.stats.note(f"Initial snapshot failed: {initial_snapshot_error}")
        live_feeds = [
            KrakenFeed(states, asset=args.asset),
            GeminiFeed(states, asset=args.asset),
            CoinbaseFeed(states, asset=args.asset),
            BybitFeed(states, asset=args.asset),
        ]
        LOGGER.info("Runtime mode: %s", mode_label)
        if not dashboard_enabled:
            print_banner(
                mode_label=mode_label,
                model=",".join(args.models),
                asset=args.asset,
                bankroll=starting_bankroll,
            )
        if args.once:
            price = fetch_reference_price_snapshot(args.asset)
            apply_price(states, price)
            both_track = next(t for t in tracks if t.name == "BOTH")
            window = await asyncio.to_thread(both_track.finder.find_active_window)
            shared_state.current_window = window
            for track in tracks:
                track.state.current_window = clone_window(window)
            await run_with_optional_timeout(
                trading_loop(
                    tracks,
                    shared_state,
                    client,
                    once=True,
                    asset=args.asset,
                    live_mode=args.live,
                    stop_event=stop_event,
                ),
                duration=None,
                stop_event=stop_event,
            )
            if RUNTIME_STATUS_FILE.strip():
                final_status = build_runtime_status(tracks, shared_state, {})
                write_runtime_status_file(final_status)
            return
        coroutines = [
            *(feed.connect() for feed in live_feeds),
            price_snapshot_loop(states, args.asset),
            kalshi_refresh_loop(states),
            unresolved_resolution_loop(
                tracks,
                client,
                interval=UNRESOLVED_SWEEP_INTERVAL_SECONDS,
                reconcile_intents=args.live,
            ),
            trading_loop(
                tracks,
                shared_state,
                client,
                once=args.once,
                asset=args.asset,
                live_mode=args.live,
                stop_event=stop_event,
            ),
        ]
        if RUNTIME_STATUS_FILE.strip():
            coroutines.append(
                runtime_status_loop(
                    tracks,
                    path=RUNTIME_STATUS_FILE,
                    interval=RUNTIME_STATUS_UPDATE_SECONDS,
                    shared_state=shared_state,
                )
            )
        await run_with_optional_timeout(
            *coroutines,
            duration=None if args.once else args.duration,
            stop_event=stop_event,
        )
        if RUNTIME_STATUS_FILE.strip():
            final_status = build_runtime_status(tracks, shared_state, {})
            write_runtime_status_file(final_status)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        emit_alert("CRITICAL", "Bot crashed", error=str(exc))
        raise
