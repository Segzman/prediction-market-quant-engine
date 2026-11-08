from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from config import (
    DAY_VOL_LOOKBACK_WINDOWS,
    DAY_VOL_MAX_STALE_SECONDS,
    DAY_VOL_MIN_WINDOWS,
    MAX_PRICE_FEED_DIVERGENCE_BPS,
    MIN_HEALTHY_PRICE_FEEDS,
    POLYMARKET_FEATURE_LOOKBACK_SECONDS,
    POLYMARKET_HISTORY_MAX_SAMPLES,
    POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS,
    PRICE_SOURCE_SELECTION,
    PRICE_STALE_THRESHOLD_SECONDS,
    STARTING_BANKROLL_USDC,
    VOLATILITY_MAX_SAMPLES,
    VOLATILITY_MIN_SAMPLES,
)
from state.types import Direction
from state.window import WindowState


@dataclass
class TradeRecord:
    ts: float
    window_end_ts: int
    direction: str
    amount_usdc: float
    price: float
    edge: float
    model: str
    order_id: Optional[str]
    dry_run: bool
    role: str = "PRIMARY"
    resolved: bool = False
    status: str = "OPEN"
    resolved_up: Optional[bool] = None
    pnl: float = 0.0
    fee_usdc: float = 0.0
    entry_regime: str = ""


@dataclass
class PendingOrder:
    ts: float
    window_end_ts: int
    direction: str
    amount_usdc: float
    price: float
    model: str
    order_id: Optional[str]
    dry_run: bool
    role: str = "PRIMARY"


@dataclass(frozen=True)
class PolymarketObservation:
    ts: float
    window_end_ts: int
    up_ask: float | None
    down_ask: float | None
    up_bid: float | None
    down_bid: float | None
    up_ask_count: int = 0
    down_ask_count: int = 0
    up_bid_count: int = 0
    down_bid_count: int = 0
    up_ask_size: float = 0.0
    down_ask_size: float = 0.0
    up_bid_size: float = 0.0
    down_bid_size: float = 0.0
    up_total_ask_size: float = 0.0
    down_total_ask_size: float = 0.0
    up_total_bid_size: float = 0.0
    down_total_bid_size: float = 0.0


@dataclass(frozen=True)
class PriceObservation:
    source: str
    price: float
    updated_ts: float


@dataclass
class BotState:
    btc_price: float = 0.0
    btc_price_updated_ts: float = 0.0
    price_source: str = ""
    last_trade_ts: float = 0.0
    last_trade_high_conviction: bool = False  # True if last entry was high-conviction (price >= threshold)
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    daily_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    starting_bankroll_usdc: float = STARTING_BANKROLL_USDC
    current_window: Optional[WindowState] = None
    trade_history: list[TradeRecord] = field(default_factory=list)
    pending_orders: list[PendingOrder] = field(default_factory=list)
    price_samples: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=VOLATILITY_MAX_SAMPLES)
    )
    price_observations: dict[str, PriceObservation] = field(default_factory=dict)
    # Per-source tick-interval EMA (seconds) for fastest-feed ranking.
    price_tick_ema: dict[str, float] = field(default_factory=dict)
    # Kalshi cross-venue view (None = unavailable, e.g. weekends).
    kalshi_up_probability: float | None = None
    kalshi_updated_ts: float = 0.0
    polymarket_window_end_ts: int | None = None
    polymarket_samples: deque[PolymarketObservation] = field(
        default_factory=lambda: deque(maxlen=POLYMARKET_HISTORY_MAX_SAMPLES)
    )
    # Finished-window BTC moves for the day-volatility gate: (close_ts, |bps|).
    window_moves: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=DAY_VOL_LOOKBACK_WINDOWS)
    )

    def update_price(
        self,
        price: float,
        *,
        updated_ts: float | None = None,
        source: str | None = None,
    ) -> None:
        if price <= 0:
            return
        ts = time.time() if updated_ts is None else updated_ts
        if source:
            existing = self.price_observations.get(source)
            if existing is not None and ts < existing.updated_ts:
                return
            if existing is not None:
                interval = ts - existing.updated_ts
                if interval > 0:
                    prior = self.price_tick_ema.get(source)
                    self.price_tick_ema[source] = (
                        interval if prior is None else 0.7 * prior + 0.3 * interval
                    )
            self.price_observations[source] = PriceObservation(
                source=source,
                price=price,
                updated_ts=ts,
            )
            self._refresh_aggregate_price()
            return
        if ts < self.btc_price_updated_ts:
            return
        self._set_aggregate_price(price, ts, source_label=self.price_source or "")

    def _set_aggregate_price(self, price: float, updated_ts: float, *, source_label: str) -> None:
        if updated_ts < self.btc_price_updated_ts:
            return
        self.btc_price = price
        self.btc_price_updated_ts = updated_ts
        self.price_source = source_label
        self.price_samples.append((updated_ts, price))

    def fastest_price_source(self, *, now: float | None = None) -> str | None:
        """Freshest healthy feed; ties broken by tick-interval EMA.

        Called on every price refresh, so the primary feed switches live
        as latencies change. Returns None when no source is healthy.
        """
        active_now = time.time() if now is None else now
        healthy = self.healthy_price_sources(now=active_now)
        if not healthy:
            return None

        def _rank(quote: PriceObservation) -> tuple[float, float]:
            age = active_now - quote.updated_ts
            interval_ema = self.price_tick_ema.get(quote.source, 0.0)
            return (age, interval_ema)

        return min(healthy.values(), key=_rank).source

    def _refresh_aggregate_price(self) -> None:
        if not self.price_observations:
            return
        healthy_quotes = list(self.healthy_price_sources().values())
        if healthy_quotes and PRICE_SOURCE_SELECTION != "median":
            fastest = self.fastest_price_source()
            if fastest is not None:
                quote = self.price_observations[fastest]
                self._set_aggregate_price(
                    quote.price, quote.updated_ts, source_label=quote.source
                )
                return
        if healthy_quotes:
            prices = [quote.price for quote in healthy_quotes]
            aggregate_price = float(statistics.median(prices))
            aggregate_ts = min(quote.updated_ts for quote in healthy_quotes)
            sources = ",".join(sorted(quote.source for quote in healthy_quotes))
            source_label = (
                healthy_quotes[0].source
                if len(healthy_quotes) == 1
                else f"MEDIAN[{sources}]"
            )
            self._set_aggregate_price(aggregate_price, aggregate_ts, source_label=source_label)
            return
        newest_quote = max(
            self.price_observations.values(),
            key=lambda quote: quote.updated_ts,
        )
        self._set_aggregate_price(
            newest_quote.price,
            newest_quote.updated_ts,
            source_label=newest_quote.source,
        )

    def healthy_price_sources(self, *, now: float | None = None) -> dict[str, PriceObservation]:
        active_now = time.time() if now is None else now
        healthy: dict[str, PriceObservation] = {}
        for source, observation in self.price_observations.items():
            if active_now - observation.updated_ts <= PRICE_STALE_THRESHOLD_SECONDS:
                healthy[source] = observation
        return healthy

    def healthy_price_source_count(self, *, now: float | None = None) -> int:
        return len(self.healthy_price_sources(now=now))

    def price_feed_divergence_bps(self, *, now: float | None = None) -> float:
        quotes = list(self.healthy_price_sources(now=now).values())
        if len(quotes) < 2:
            return 0.0
        prices = [quote.price for quote in quotes]
        midpoint = statistics.median(prices)
        if midpoint <= 0:
            return 0.0
        return ((max(prices) - min(prices)) / midpoint) * 10_000

    def price_feed_quorum_met(self, *, now: float | None = None) -> bool:
        if not self.price_observations:
            return True
        healthy_count = self.healthy_price_source_count(now=now)
        if healthy_count < MIN_HEALTHY_PRICE_FEEDS:
            return False
        return self.price_feed_divergence_bps(now=now) <= MAX_PRICE_FEED_DIVERGENCE_BPS

    def healthy_price_source_names(self, *, now: float | None = None) -> list[str]:
        return sorted(self.healthy_price_sources(now=now))

    def rolling_volatility(self) -> float:
        if len(self.price_samples) < VOLATILITY_MIN_SAMPLES:
            return 0.0

        normalized_returns: list[float] = []
        previous_ts, previous_price = self.price_samples[0]
        for ts, price in list(self.price_samples)[1:]:
            if previous_price <= 0 or price <= 0 or ts <= previous_ts:
                previous_ts, previous_price = ts, price
                continue
            delta_t = ts - previous_ts
            normalized_returns.append(
                math.log(price / previous_price) / math.sqrt(delta_t)
            )
            previous_ts, previous_price = ts, price

        if len(normalized_returns) < VOLATILITY_MIN_SAMPLES - 1:
            return 0.0

        mean = sum(normalized_returns) / len(normalized_returns)
        variance = sum(
            (value - mean) ** 2 for value in normalized_returns
        ) / len(normalized_returns)
        return math.sqrt(max(variance, 0.0))

    def note_finished_window(self, open_price: float, close_price: float) -> None:
        """Record a finished 5-min window's BTC move for the day-vol gate."""
        if open_price <= 0 or close_price <= 0:
            return
        move_bps = abs(close_price - open_price) / open_price * 10_000
        self.window_moves.append((time.time(), move_bps))

    def day_vol_v24_bps(self, *, now: float | None = None) -> float | None:
        """Median window move over the trailing lookback. None = not usable."""
        if len(self.window_moves) < DAY_VOL_MIN_WINDOWS:
            return None
        latest_ts = self.window_moves[-1][0]
        if (time.time() if now is None else now) - latest_ts > DAY_VOL_MAX_STALE_SECONDS:
            return None
        return statistics.median(move for _, move in self.window_moves)

    def tick_trend_features(self, lookback_seconds: float = 12.0) -> dict[str, float]:
        if len(self.price_samples) < 3:
            return {
                "tick_move_pct": 0.0,
                "directional_consistency": 0.5,
                "acceleration_pct": 0.0,
            }

        latest_ts = self.price_samples[-1][0]
        samples = [
            (ts, price)
            for ts, price in self.price_samples
            if latest_ts - ts <= lookback_seconds
        ]
        if len(samples) < 3:
            samples = list(self.price_samples)[-3:]

        first_price = samples[0][1]
        last_price = samples[-1][1]
        tick_move_pct = ((last_price - first_price) / first_price) if first_price > 0 else 0.0

        signed_moves: list[float] = []
        for (_, previous_price), (_, price) in zip(samples, samples[1:]):
            if previous_price <= 0 or price <= 0:
                continue
            signed_moves.append(price - previous_price)

        if not signed_moves:
            directional_consistency = 0.5
        else:
            positive = sum(1 for move in signed_moves if move > 0)
            negative = sum(1 for move in signed_moves if move < 0)
            # Modal-tick ratio: fraction of ticks in the dominant direction.
            # (Not true Kaufman ER, which would be |net_move| / sum(|steps|) —
            # but modal-tick ratio is a valid trend-consistency proxy at this timescale.)
            directional_consistency = max(positive, negative) / len(signed_moves)

        midpoint = max(1, len(samples) // 2)
        early_samples = samples[: midpoint + 1]
        late_samples = samples[midpoint:]

        def _segment_move_pct(segment: list[tuple[float, float]]) -> float:
            start_price = segment[0][1]
            end_price = segment[-1][1]
            if start_price <= 0:
                return 0.0
            return (end_price - start_price) / start_price

        acceleration_pct = _segment_move_pct(late_samples) - _segment_move_pct(early_samples)
        return {
            "tick_move_pct": tick_move_pct,
            "directional_consistency": directional_consistency,
            "acceleration_pct": acceleration_pct,
        }

    def record_trade(
        self,
        direction: str,
        amount: float,
        price: float,
        edge: float,
        model: str,
        order_id: Optional[str],
        dry_run: bool,
        window_end_ts: int,
        role: str = "PRIMARY",
        entry_regime: str = "",
    ) -> TradeRecord:
        record = TradeRecord(
            ts=time.time(),
            window_end_ts=window_end_ts,
            direction=direction,
            amount_usdc=amount,
            price=price,
            edge=edge,
            model=model,
            order_id=order_id,
            dry_run=dry_run,
            role=role,
            entry_regime=entry_regime,
        )
        self.trade_history.append(record)
        if role != "HEDGE":
            self.last_trade_ts = record.ts
            self.daily_trades += 1
            self.total_trades += 1
        return record

    def record_pending_order(
        self,
        *,
        direction: str,
        amount: float,
        price: float,
        model: str,
        order_id: Optional[str],
        dry_run: bool,
        window_end_ts: int,
        role: str = "PRIMARY",
    ) -> PendingOrder:
        record = PendingOrder(
            ts=time.time(),
            window_end_ts=window_end_ts,
            direction=direction,
            amount_usdc=amount,
            price=price,
            model=model,
            order_id=order_id,
            dry_run=dry_run,
            role=role,
        )
        self.pending_orders.append(record)
        return record

    def clear_pending_order(self, order_id: str) -> bool:
        for idx, order in enumerate(self.pending_orders):
            if order.order_id != order_id:
                continue
            self.pending_orders.pop(idx)
            return True
        return False

    def record_resolution(self, order_id: str, resolved_up: bool) -> None:
        for trade in self.trade_history:
            if trade.order_id != order_id:
                continue
            if trade.resolved:
                return

            direction = Direction(trade.direction)
            won = (direction == Direction.UP and resolved_up) or (
                direction == Direction.DOWN and not resolved_up
            )
            gross_pnl = (
                trade.amount_usdc * (1 / trade.price - 1)
                if won
                else -trade.amount_usdc
            )
            pnl = gross_pnl - trade.fee_usdc
            trade.resolved = True
            trade.status = "WIN" if won else "LOSS"
            trade.resolved_up = resolved_up
            trade.pnl = pnl
            self.daily_pnl += pnl
            self.realized_pnl += pnl

            if trade.role == "HEDGE":
                return

            if won:
                self.wins += 1
                self.consecutive_wins += 1
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_wins = 0
                self.consecutive_losses += 1
            return

    def record_early_exit(self, order_id: str, pnl: float, won: bool) -> None:
        for trade in self.trade_history:
            if trade.order_id != order_id:
                continue
            if trade.resolved:
                return
            
            trade.resolved = True
            trade.status = "WIN" if won else "LOSS"
            trade.pnl = pnl
            
            # Note: We do NOT increment daily_pnl/realized_pnl here because
            # check_position_exits() already does it via state.daily_pnl += realized_pnl.
            # We just need to update the trade record status.
            
            if trade.role == "HEDGE":
                return
            
            if won:
                self.wins += 1
                self.consecutive_wins += 1
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_wins = 0
                self.consecutive_losses += 1
            return

    def record_fee(self, order_id: str, fee_usdc: float) -> bool:
        for trade in self.trade_history:
            if trade.order_id != order_id:
                continue
            trade.fee_usdc = max(0.0, fee_usdc)
            return True
        return False

    def update_trade_amount(self, order_id: str, amount_usdc: float) -> bool:
        for trade in self.trade_history:
            if trade.order_id != order_id:
                continue
            trade.amount_usdc = max(0.0, amount_usdc)
            return True
        return False

    def record_cancellation(self, order_id: str) -> bool:
        if self.clear_pending_order(order_id):
            return True
        for trade in self.trade_history:
            if trade.order_id != order_id:
                continue
            if trade.resolved:
                return False
            trade.resolved = True
            trade.status = "CANCELLED"
            trade.pnl = 0.0
            return True
        return False

    def current_bankroll_usdc(self) -> float:
        return max(0.0, self.starting_bankroll_usdc + self.realized_pnl)

    def pending_exposure_usdc(self) -> float:
        return max(
            0.0,
            sum(trade.amount_usdc for trade in self.trade_history if not trade.resolved)
            + sum(order.amount_usdc for order in self.pending_orders),
        )

    def available_bankroll_usdc(self) -> float:
        return max(0.0, self.current_bankroll_usdc() - self.pending_exposure_usdc())

    def open_directional_exposure_usdc(self, direction: str) -> float:
        target = str(direction).upper()
        exposure = sum(
            trade.amount_usdc
            for trade in self.trade_history
            if not trade.resolved and str(trade.direction).upper() == target
        )
        exposure += sum(
            order.amount_usdc
            for order in self.pending_orders
            if str(order.direction).upper() == target
        )
        return max(0.0, exposure)

    def open_window_exposure_usdc(self) -> float:
        if self.current_window is None:
            return 0.0
        return max(0.0, self.current_window.total_exposure())

    def unresolved_primary_count(self) -> int:
        return sum(
            1
            for trade in self.trade_history
            if trade.role != "HEDGE" and not trade.resolved
        ) + sum(1 for order in self.pending_orders if order.role != "HEDGE")

    def open_primary_positions_count(self) -> int:
        return sum(
            1
            for trade in self.trade_history
            if trade.role != "HEDGE" and not trade.resolved
        )

    def open_primary_orders_count(self) -> int:
        return sum(1 for order in self.pending_orders if order.role != "HEDGE")

    def cancelled_primary_count(self) -> int:
        return sum(
            1
            for trade in self.trade_history
            if trade.role != "HEDGE" and trade.status == "CANCELLED"
        )

    def is_price_stale(self) -> bool:
        if self.btc_price_updated_ts == 0:
            return True
        now = time.time()
        if (now - self.btc_price_updated_ts) > PRICE_STALE_THRESHOLD_SECONDS:
            return True
        if self.price_observations and not self.price_feed_quorum_met(now=now):
            return True
        return False

    def btc_price_age_seconds(self) -> float | None:
        if self.btc_price_updated_ts == 0:
            return None
        return max(0.0, time.time() - self.btc_price_updated_ts)

    def latest_polymarket_book_ts(self) -> float | None:
        if not self.polymarket_samples:
            return None
        return float(self.polymarket_samples[-1].ts)

    def polymarket_book_age_seconds(self) -> float | None:
        latest_ts = self.latest_polymarket_book_ts()
        if latest_ts is None:
            return None
        return max(0.0, time.time() - latest_ts)

    def is_polymarket_book_stale(self) -> bool:
        age_seconds = self.polymarket_book_age_seconds()
        if age_seconds is None:
            return True
        return age_seconds > POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS

    def clear_polymarket_book(self) -> None:
        self.polymarket_window_end_ts = None
        self.polymarket_samples.clear()

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.daily_trades = 0

    def update_polymarket_book(
        self,
        *,
        window_end_ts: int,
        up_ask: float | None,
        down_ask: float | None,
        up_bid: float | None,
        down_bid: float | None,
        up_ask_count: int = 0,
        down_ask_count: int = 0,
        up_bid_count: int = 0,
        down_bid_count: int = 0,
        up_ask_size: float = 0.0,
        down_ask_size: float = 0.0,
        up_bid_size: float = 0.0,
        down_bid_size: float = 0.0,
        up_total_ask_size: float = 0.0,
        down_total_ask_size: float = 0.0,
        up_total_bid_size: float = 0.0,
        down_total_bid_size: float = 0.0,
        updated_ts: float | None = None,
    ) -> None:
        if self.polymarket_window_end_ts != window_end_ts:
            self.polymarket_window_end_ts = window_end_ts
            self.polymarket_samples.clear()
        ts = time.time() if updated_ts is None else updated_ts
        self.polymarket_samples.append(
            PolymarketObservation(
                ts=ts,
                window_end_ts=window_end_ts,
                up_ask=up_ask,
                down_ask=down_ask,
                up_bid=up_bid,
                down_bid=down_bid,
                up_ask_count=up_ask_count,
                down_ask_count=down_ask_count,
                up_bid_count=up_bid_count,
                down_bid_count=down_bid_count,
                up_ask_size=up_ask_size,
                down_ask_size=down_ask_size,
                up_bid_size=up_bid_size,
                down_bid_size=down_bid_size,
                up_total_ask_size=up_total_ask_size,
                down_total_ask_size=down_total_ask_size,
                up_total_bid_size=up_total_bid_size,
                down_total_bid_size=down_total_bid_size,
            )
        )

    def polymarket_features(
        self, lookback_seconds: float = POLYMARKET_FEATURE_LOOKBACK_SECONDS
    ) -> dict[str, float | str]:
        if len(self.polymarket_samples) < 2:
            return {
                "up_price_momentum": 0.0,
                "down_price_momentum": 0.0,
                "up_price_acceleration": 0.0,
                "down_price_acceleration": 0.0,
                "spread_change": 0.0,
                "one_sided_ratio": 0.0,
                "midpoint_shift": 0.0,
                "strongest_side": "NONE",
                "market_conviction": 0.0,
                "btc_poly_agreement": 0.0,
                "up_queue_imbalance": 0.0,
                "down_queue_imbalance": 0.0,
                "up_microprice_offset": 0.0,
                "down_microprice_offset": 0.0,
                "strongest_queue_imbalance": 0.0,
                "strongest_microprice_offset": 0.0,
                "depth_imbalance": 0.0,
                "strongest_depth_to_fill_usdc": 0.0,
                "weakest_depth_to_fill_usdc": 0.0,
                "market_implied_up_probability": 0.5,
            }

        latest_ts = self.polymarket_samples[-1].ts
        samples = [
            sample
            for sample in self.polymarket_samples
            if latest_ts - sample.ts <= lookback_seconds
        ]
        if len(samples) < 2:
            samples = list(self.polymarket_samples)[-2:]

        def _valid_values(attr: str) -> list[float]:
            values = [getattr(sample, attr) for sample in samples]
            return [float(value) for value in values if value is not None]

        def _momentum(values: list[float]) -> float:
            if len(values) < 2:
                return 0.0
            start = values[0]
            end = values[-1]
            if start <= 0:
                return 0.0
            return (end - start) / start

        def _acceleration(values: list[float]) -> float:
            if len(values) < 3:
                return 0.0
            midpoint = max(1, len(values) // 2)
            early = values[: midpoint + 1]
            late = values[midpoint:]
            return _momentum(late) - _momentum(early)

        def _midpoint(sample: PolymarketObservation) -> float | None:
            mids: list[float] = []
            if sample.up_ask is not None and sample.up_bid is not None:
                mids.append((sample.up_ask + sample.up_bid) / 2)
            if sample.down_ask is not None and sample.down_bid is not None:
                mids.append((sample.down_ask + sample.down_bid) / 2)
            if not mids:
                return None
            return sum(mids) / len(mids)

        up_values = _valid_values("up_ask")
        down_values = _valid_values("down_ask")
        up_momentum = _momentum(up_values)
        down_momentum = _momentum(down_values)
        up_acceleration = _acceleration(up_values)
        down_acceleration = _acceleration(down_values)

        spreads: list[float] = []
        for sample in samples:
            if sample.up_ask is None or sample.down_ask is None:
                continue
            spreads.append(abs((sample.up_ask + sample.down_ask) - 1.0))
        spread_change = 0.0 if len(spreads) < 2 else spreads[-1] - spreads[0]

        one_sided_count = sum(
            1
            for sample in samples
            if sample.up_ask is None
            or sample.down_ask is None
            or sample.up_bid is None
            or sample.down_bid is None
        )
        one_sided_ratio = one_sided_count / max(len(samples), 1)

        midpoints = [value for value in (_midpoint(sample) for sample in samples) if value is not None]
        midpoint_shift = 0.0 if len(midpoints) < 2 else midpoints[-1] - midpoints[0]

        latest = samples[-1]

        def _mid_price(ask: float | None, bid: float | None) -> float | None:
            if ask is not None and bid is not None:
                return (ask + bid) / 2
            return ask if ask is not None else bid
        def _queue_imbalance(bid_size: float, ask_size: float) -> float:
            total = bid_size + ask_size
            if total <= 0:
                return 0.0
            return (bid_size - ask_size) / total

        def _microprice_offset(
            ask: float | None,
            bid: float | None,
            ask_size: float,
            bid_size: float,
        ) -> float:
            if (
                ask is None
                or bid is None
                or ask_size <= 0
                or bid_size <= 0
            ):
                return 0.0
            mid = (ask + bid) / 2
            if mid <= 0:
                return 0.0
            microprice = ((ask * bid_size) + (bid * ask_size)) / (bid_size + ask_size)
            return (microprice - mid) / mid

        strongest_side = "NONE"
        strongest_price = 0.0
        if latest.up_ask is not None or latest.down_ask is not None:
            up_latest = latest.up_ask if latest.up_ask is not None else -1.0
            down_latest = latest.down_ask if latest.down_ask is not None else -1.0
            if up_latest > down_latest:
                strongest_side = "UP"
                strongest_price = max(up_latest, 0.0)
            elif down_latest > up_latest:
                strongest_side = "DOWN"
                strongest_price = max(down_latest, 0.0)

        up_queue_imbalance = _queue_imbalance(latest.up_bid_size, latest.up_ask_size)
        down_queue_imbalance = _queue_imbalance(latest.down_bid_size, latest.down_ask_size)
        up_microprice_offset = _microprice_offset(
            latest.up_ask,
            latest.up_bid,
            latest.up_ask_size,
            latest.up_bid_size,
        )
        down_microprice_offset = _microprice_offset(
            latest.down_ask,
            latest.down_bid,
            latest.down_ask_size,
            latest.down_bid_size,
        )
        up_depth_to_fill_usdc = (latest.up_total_ask_size * latest.up_ask) if latest.up_ask is not None else 0.0
        down_depth_to_fill_usdc = (latest.down_total_ask_size * latest.down_ask) if latest.down_ask is not None else 0.0
        total_depth = up_depth_to_fill_usdc + down_depth_to_fill_usdc
        depth_imbalance = (
            (up_depth_to_fill_usdc - down_depth_to_fill_usdc) / total_depth
            if total_depth > 0
            else 0.0
        )
        strongest_queue_imbalance = 0.0
        strongest_microprice_offset = 0.0
        strongest_depth_to_fill_usdc = 0.0
        weakest_depth_to_fill_usdc = 0.0
        up_mid_price = _mid_price(latest.up_ask, latest.up_bid)
        down_mid_price = _mid_price(latest.down_ask, latest.down_bid)
        market_implied_up_probability = 0.5
        if up_mid_price is not None and down_mid_price is not None:
            total_mid = up_mid_price + down_mid_price
            if total_mid > 0:
                market_implied_up_probability = up_mid_price / total_mid
        elif up_mid_price is not None:
            market_implied_up_probability = max(0.0, min(1.0, up_mid_price))
        if strongest_side == "UP":
            strongest_queue_imbalance = up_queue_imbalance
            strongest_microprice_offset = up_microprice_offset
            strongest_depth_to_fill_usdc = up_depth_to_fill_usdc
            weakest_depth_to_fill_usdc = down_depth_to_fill_usdc
        elif strongest_side == "DOWN":
            strongest_queue_imbalance = down_queue_imbalance
            strongest_microprice_offset = down_microprice_offset
            strongest_depth_to_fill_usdc = down_depth_to_fill_usdc
            weakest_depth_to_fill_usdc = up_depth_to_fill_usdc

        supporting_momentum = 0.0
        supporting_acceleration = 0.0
        if strongest_side == "UP":
            supporting_momentum = max(0.0, up_momentum)
            supporting_acceleration = max(0.0, up_acceleration)
        elif strongest_side == "DOWN":
            supporting_momentum = max(0.0, down_momentum)
            supporting_acceleration = max(0.0, down_acceleration)
        market_conviction = min(
            1.0,
            max(
                0.0,
                strongest_price * 0.72
                + min(supporting_momentum * 10.0, 0.18)
                + min(supporting_acceleration * 10.0, 0.08)
                + (1.0 - min(one_sided_ratio, 1.0)) * 0.12
                + max(strongest_queue_imbalance, 0.0) * 0.06
                + max(strongest_microprice_offset * 40.0, 0.0) * 0.04
            ),
        )

        btc_poly_agreement = 0.0
        if len(self.price_samples) >= 2:
            tick_move = self.tick_trend_features().get("tick_move_pct", 0.0)
            if strongest_side == "UP" and tick_move > 0:
                btc_poly_agreement = 1.0
            elif strongest_side == "DOWN" and tick_move < 0:
                btc_poly_agreement = 1.0
            elif tick_move != 0:
                btc_poly_agreement = -1.0

        return {
            "up_price_momentum": up_momentum,
            "down_price_momentum": down_momentum,
            "up_price_acceleration": up_acceleration,
            "down_price_acceleration": down_acceleration,
            "spread_change": spread_change,
            "one_sided_ratio": one_sided_ratio,
            "midpoint_shift": midpoint_shift,
            "strongest_side": strongest_side,
            "market_conviction": market_conviction,
            "btc_poly_agreement": btc_poly_agreement,
            "up_queue_imbalance": up_queue_imbalance,
            "down_queue_imbalance": down_queue_imbalance,
            "up_microprice_offset": up_microprice_offset,
            "down_microprice_offset": down_microprice_offset,
            "strongest_queue_imbalance": strongest_queue_imbalance,
            "strongest_microprice_offset": strongest_microprice_offset,
            "depth_imbalance": depth_imbalance,
            "strongest_depth_to_fill_usdc": strongest_depth_to_fill_usdc,
            "weakest_depth_to_fill_usdc": weakest_depth_to_fill_usdc,
            "market_implied_up_probability": market_implied_up_probability,
        }


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
