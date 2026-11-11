from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import (
    HOLD_TO_SETTLEMENT_TRACKS,
    MAX_PRICE_FEED_DIVERGENCE_BPS,
    MIN_HEALTHY_PRICE_FEEDS,
    POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS,
    PRICE_STALE_THRESHOLD_SECONDS,
    RUNTIME_STATUS_FILE,
)
from market.finder import MarketFinder
from market.orderer import Orderer
from market.slug import extract_market_slug
from ops.formatting import fit_text, format_age, format_price, format_prob, format_runtime
from risk.guard import DailyGuard
from state import BotState, WindowPhase, WindowState


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


def format_model_signal(stats: RuntimeStats) -> str:
    if stats.last_signal_direction == "-" or stats.last_signal_edge is None:
        return "-"
    return (
        f"{stats.last_signal_direction} e{stats.last_signal_edge:.3f} p{stats.last_signal_market_price:.3f}"
    )


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
        format_age(now - primary.state.btc_price_updated_ts) if primary.state.btc_price_updated_ts else "n/a"
    )
    active_slug = next((runtime.stats.window_slug for runtime in runtimes if runtime.stats.window_slug), "-")
    active_phase = next(
        (runtime.stats.window_phase for runtime in runtimes if runtime.stats.window_phase != "-"), "-"
    )
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
            f"Source    {primary.stats.market_source or '-'} | Ledger {primary.stats.ledger_path or '-'}",
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
    price_age = format_age(now - state.btc_price_updated_ts) if state.btc_price_updated_ts else "n/a"
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
            f"Orderbook UP {format_prob(stats.current_up_ask)} | DOWN {format_prob(stats.current_down_ask)}",
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
        max(0.0, now - primary_state.btc_price_updated_ts) if primary_state.btc_price_updated_ts else None
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


def _pnl_curve(resolved: list) -> list[float]:
    """Cumulative net P&L after each resolved trade, oldest first (starts at 0)."""
    curve, total = [0.0], 0.0
    for t in sorted(resolved, key=lambda t: t.resolved_ts or t.ts):
        total += t.pnl
        curve.append(round(total, 4))
    return curve


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
    active_tracks[0] if active_tracks else tracks[-1]
    if len(active_tracks) == 1:
        active_track_name = active_tracks[0].name
    elif len(active_tracks) > 1 and all(t.orderer.dry_run for t in active_tracks):
        active_track_name = "ALL_SHADOW"
    elif len(active_tracks) > 1:
        active_track_name = "MULTI"
    else:
        active_track_name = "NONE"
    mode = "LIVE" if any(not t.orderer.dry_run for t in active_tracks) else "DRY_RUN"

    # P&L curve, total and win stats all use resolved trades' net-of-fee P&L. The bankroll also
    # includes partial closes of still-open positions, so it can lead the total until they resolve.
    total_pnl = round(sum(
        t.pnl for tr in tracks for t in getattr(tr.state, "trade_history", [])
        if t.resolved and t.status in ("WIN", "LOSS")
    ), 4)
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
        open_positions = state.current_window.get_open_positions() if state.current_window is not None else []
        open_pos_by_id = {p.order_id: p for p in open_positions}
        all_open_positions_payload = []
        for pos in open_positions:
            if pos.direction == "UP":
                cur_price = (
                    (track.stats.current_up_bid or pos.entry_price) if track.stats else pos.entry_price
                )
                ask_price = track.stats.current_up_ask if track.stats else None
            else:
                cur_price = (
                    (track.stats.current_down_bid or pos.entry_price) if track.stats else pos.entry_price
                )
                ask_price = track.stats.current_down_ask if track.stats else None
            all_open_positions_payload.append(
                {
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
                }
            )
        # Keep legacy single open_position for backwards compat
        open_pos_payload = all_open_positions_payload[0] if all_open_positions_payload else None

        # Win stats from trade_history
        trade_history = getattr(state, "trade_history", [])
        resolved = [t for t in trade_history if t.resolved and t.status in ("WIN", "LOSS")]
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
                    cur = (
                        (track.stats.current_down_bid or pos.entry_price) if track.stats else pos.entry_price
                    )
                row["live_pnl"] = round(pos.calculate_unrealized_pnl(cur), 4)
                row["live_pnl_pct"] = round(pos.unrealized_pnl_pct(cur) * 100, 2)
                row["current_price"] = round(cur, 3)
            all_trades_payload.append(row)

        track_payloads.append(
            {
                "name": track.name,
                "hold_to_settlement": track.name in HOLD_TO_SETTLEMENT_TRACKS,
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
                    "realized_pnl": round(state.realized_pnl, 4),  # unclamped; current floors at 0
                },
                "all_trades": all_trades_payload,
                "pnl_history": _pnl_curve(resolved),
            }
        )

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
            "btc_price_age_seconds": round(btc_price_age_seconds, 1)
            if btc_price_age_seconds is not None
            else None,
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
            "down_ask": next(
                (t.stats.current_down_ask for t in tracks if t.name == "BOTH" and t.stats), None
            ),
            "down_bid": next(
                (t.stats.current_down_bid for t in tracks if t.name == "BOTH" and t.stats), None
            ),
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
