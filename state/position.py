"""Position tracking for multi-trade windows with early exits."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import time


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARTIAL = "PARTIAL"  # For partial exits


class ExitReason(Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIME_EXIT = "TIME_EXIT"
    SIGNAL_REVERSE = "SIGNAL_REVERSE"
    WINDOW_CLOSE = "WINDOW_CLOSE"
    MANUAL = "MANUAL"


@dataclass
class Position:
    """Represents an open position in a market window."""
    order_id: str
    window_end_ts: int
    direction: str  # "UP" or "DOWN"
    size_usdc: float
    entry_price: float
    entry_ts: float
    role: str = "PRIMARY"  # PRIMARY or HEDGE
    
    # Exit tracking
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    exit_ts: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    pnl_usdc: float = 0.0
    fee_usdc: float = 0.0
    
    # Exit targets (set at entry)
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    # Tiered TP1: bank a fraction early, let the rest run to TP2.
    take_profit_1_price: Optional[float] = None
    take_profit_1_fraction: float = 0.0
    take_profit_1_taken: bool = False
    # Trailing ratchet: highest price seen; SL never ratchets below its start.
    highest_price: float = 0.0
    time_exit_seconds: Optional[int] = None
    time_exit_hard_seconds: Optional[int] = None  # Hard exit at this time regardless of P&L (low-conviction fallback)

    # Internal state
    tp_sl_trigger_ts: Optional[float] = None  # Unused legacy field (kept for serialisation compat)
    time_exit_triggered: bool = False  # True once time_exit_seconds threshold has been passed
    sl_breach_ts: Optional[float] = None  # Timestamp when price first crossed below stop_loss_price

    # Resting GTC sell orders placed at entry (live mode only; None in dry-run)
    sl_order_id: Optional[str] = None  # Order ID of resting SL sell at stop_loss_price
    tp_order_id: Optional[str] = None  # Order ID of resting TP sell at take_profit_price
    
    # Trade number in window for tiered exits (1st=15%, 2nd=25%, 3rd+=ride out)
    trade_number: int = 1
    
    # High-conviction mode: wider SL + time extension when price is rising
    is_high_conviction: bool = False
    _price_samples: list = field(default_factory=list, repr=False)  # [(ts, price), ...] last ~15 ticks

    # Conviction decay exit: exit if market_conviction drops below floor for N consecutive seconds
    conviction_decay_floor: float = 0.0      # 0 = disabled; set at entry (e.g. 0.60)
    conviction_decay_seconds: float = 0.0    # how long conviction must stay below floor before exit
    _conviction_breach_ts: Optional[float] = field(default=None, repr=False)

    # Break-even trailing exit: once price crosses break_even_price (entry + fees),
    # exit immediately if it dips back below that line. Worst case: flat after fees.
    # Best case: price runs far above BE before dipping, locking in the full gain.
    break_even_price: float = 0.0            # 0 = disabled; set at entry to entry*(1+round_trip_fee)
    _break_even_crossed: bool = field(default=False, repr=False)

    # Velocity-based momentum SL: if price crashes >momentum_sl_velocity_pct in the last
    # momentum_sl_velocity_window seconds, exit immediately — don't ride a hard reversal to the SL floor.
    momentum_sl_velocity_pct: float = 0.0    # 0 = disabled; e.g. 0.04 = exit on >4% drop in window
    momentum_sl_velocity_window: float = 3.0 # seconds to look back for velocity calculation

    # Entry-evidence fields used to deform later time-exit behavior.
    entry_confidence: float = 0.0
    entry_edge: float = 0.0
    entry_market_conviction: float = 0.0
    entry_pooled_probability: float = 0.0
    entry_regime: str = "MISPRICING"

    # Partial exit tracking
    initial_size_usdc: float = field(init=False)
    remaining_size_usdc: float = field(init=False)

    def __post_init__(self):
        self.initial_size_usdc = self.size_usdc
        self.remaining_size_usdc = self.size_usdc
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """Calculate P&L if exited at current price.
        
        In Polymarket binary options, you OWN the token (UP or DOWN).
        Profit = current_price - entry_price for BOTH directions,
        since token appreciation = profit regardless of which side you hold.
        """
        if self.entry_price > 0:
            shares = self.remaining_size_usdc / self.entry_price
            gross_return = shares * current_price
            return gross_return - self.remaining_size_usdc
        return 0.0
    
    def take_partial_profit(self, current_price: float) -> float:
        """Fraction of remaining size to bank at TP1 now (0.0 = hold).

        One-shot: once taken, the runner continues to TP2/time/SL.
        """
        if self.status != PositionStatus.OPEN:
            return 0.0
        if self.take_profit_1_taken or self.take_profit_1_fraction <= 0:
            return 0.0
        if self.take_profit_1_price is None or current_price < self.take_profit_1_price:
            return 0.0
        self.take_profit_1_taken = True
        self.status = PositionStatus.PARTIAL
        return self.take_profit_1_fraction

    def trailing_stop_price(self, trail_pct: float) -> Optional[float]:
        """Ratcheted SL: highest price minus trail %, floored at the original SL."""
        if trail_pct <= 0 or self.highest_price <= 0:
            return self.stop_loss_price
        ratchet = self.highest_price * (1.0 - trail_pct)
        if self.stop_loss_price is None:
            return ratchet
        return max(self.stop_loss_price, ratchet)

    def should_exit(self, current_price: float, seconds_elapsed: float,
                    sl_confirmation_seconds: float = 2.0,
                    sl_instant_breach_pct: float = 0.015,
                    min_hold_seconds: float = 0.0,
                    time_extension_seconds: int = 0,
                    market_conviction: float = 1.0,
                    trail_pct: float = 0.0) -> tuple[bool, Optional[ExitReason]]:
        """Check if position should be exited based on targets.

        Both UP and DOWN tokens are owned assets — profit when price rises.
        TP triggers when token price >= take_profit_price (INSTANT).
        SL uses a tiered approach:
          - Borderline breach (price just below stop_loss_price): confirmation window of
            sl_confirmation_seconds. If price recovers above stop_loss_price before then,
            the breach timer resets (false alarm / dip that recovered).
          - Hard crash (price more than sl_instant_breach_pct below stop_loss_price):
            exit immediately — no confirmation, the market is in free-fall.
        TIME_EXIT: at time_exit_seconds, exit only if profitable/break-even.
                   If underwater, extend indefinitely and exit as soon as price >= entry.
        """
        if self.status != PositionStatus.OPEN:
            return False, None

        # Track price history for trend detection (high-conviction time extension)
        now_ts = time.time()
        self._price_samples.append((now_ts, current_price))
        if len(self._price_samples) > 15:
            self._price_samples = self._price_samples[-15:]
        if current_price > self.highest_price:
            self.highest_price = current_price
        effective_sl = self.trailing_stop_price(trail_pct)

        # Min hold window — only instant hard-crash SL fires before this
        if min_hold_seconds > 0 and seconds_elapsed < min_hold_seconds:
            if effective_sl is not None and current_price <= effective_sl:
                drop_past_sl = (effective_sl - current_price) / effective_sl
                if drop_past_sl >= sl_instant_breach_pct:
                    return True, ExitReason.STOP_LOSS
            return False, None

        # Conviction decay exit: if market_conviction drops below floor for N consecutive seconds,
        # the crowd has changed its mind — exit regardless of price
        if self.conviction_decay_floor > 0 and self.conviction_decay_seconds > 0:
            if market_conviction < self.conviction_decay_floor:
                if self._conviction_breach_ts is None:
                    self._conviction_breach_ts = now_ts
                elif now_ts - self._conviction_breach_ts >= self.conviction_decay_seconds:
                    return True, ExitReason.SIGNAL_REVERSE
            else:
                self._conviction_breach_ts = None  # conviction recovered, reset timer

        # Take profit — INSTANT EXIT to secure wicks
        if self.take_profit_price is not None and current_price >= self.take_profit_price:
            return True, ExitReason.TAKE_PROFIT

        # Break-even trailing exit: arm once price crosses break-even (entry + fees).
        # Once armed, exit the moment price dips back below break-even — BUT only if
        # price is still close to entry (within 5%). If price has crashed hard, skip
        # this and let the SL handle it — avoids labelling a -47% loss as TAKE_PROFIT.
        if self.break_even_price > 0:
            if current_price >= self.break_even_price:
                self._break_even_crossed = True
            if self._break_even_crossed and current_price < self.break_even_price:
                if current_price >= self.entry_price * 0.95:
                    return True, ExitReason.TAKE_PROFIT
                # else: crashed too far — disarm and let SL fire instead
                self._break_even_crossed = False

        # Velocity SL: if price is crashing faster than momentum_sl_velocity_pct over the
        # last momentum_sl_velocity_window seconds, exit now — don't ride a hard reversal
        # all the way to the 30% SL floor.
        if self.momentum_sl_velocity_pct > 0 and len(self._price_samples) >= 2:
            window_samples = [
                (t, p) for t, p in self._price_samples
                if now_ts - t <= self.momentum_sl_velocity_window
            ]
            if len(window_samples) >= 2:
                oldest_price = window_samples[0][1]
                if oldest_price > 0:
                    velocity = (current_price - oldest_price) / oldest_price
                    if velocity <= -self.momentum_sl_velocity_pct:
                        return True, ExitReason.STOP_LOSS

        # Stop loss — tiered: instant on hard crashes, confirmation on borderline dips.
        if effective_sl is not None:
            if current_price <= effective_sl:
                # Hard crash: already well below SL — exit immediately, no recovery possible
                drop_past_sl = (effective_sl - current_price) / effective_sl
                if drop_past_sl >= sl_instant_breach_pct:
                    return True, ExitReason.STOP_LOSS
                # Borderline breach: start/check confirmation timer
                if self.sl_breach_ts is None:
                    self.sl_breach_ts = time.time()
                elif (time.time() - self.sl_breach_ts) >= sl_confirmation_seconds:
                    return True, ExitReason.STOP_LOSS
            else:
                # Price recovered above SL — cancel breach timer
                if self.sl_breach_ts is not None:
                    self.sl_breach_ts = None

        # Time-based exit: exit at time_exit_seconds only if at break-even or better.
        # For high-conviction entries: if price is slowly rising when the timer fires,
        # extend the hold by time_extension_seconds (up to max_time_exit_seconds).
        if self.time_exit_seconds is not None and seconds_elapsed >= self.time_exit_seconds:
            self.time_exit_triggered = True
            if self.is_high_conviction and time_extension_seconds > 0:
                # Check price trend: compare current to a sample ~5s ago
                old_samples = [(t, p) for t, p in self._price_samples if now_ts - t >= 4.0]
                if old_samples and current_price > old_samples[-1][1]:
                    # Price is rising — extend the timer indefinitely
                    self.time_exit_seconds = int(seconds_elapsed) + time_extension_seconds
                    self.time_exit_triggered = False  # reset so we don't exit this tick
                    return False, None

        if self.time_exit_triggered:
            conviction_floor = max(
                self.conviction_decay_floor,
                self.entry_market_conviction - 0.15 if self.entry_market_conviction > 0 else 0.0,
            )
            if (
                current_price < self.entry_price
                and conviction_floor > 0
                and market_conviction < conviction_floor
            ):
                return True, ExitReason.TIME_EXIT
            if self.is_high_conviction:
                # High-conviction: wait for break-even (SL is wide, recovery expected)
                if current_price >= self.entry_price:
                    return True, ExitReason.TIME_EXIT
            else:
                # Low-conviction: exit at 5s only if green; hard exit at 15s regardless
                if current_price >= self.entry_price:
                    return True, ExitReason.TIME_EXIT

        # Low-conviction hard time cap: exit regardless of P&L at 15s
        if self.time_exit_hard_seconds is not None and seconds_elapsed >= self.time_exit_hard_seconds:
            return True, ExitReason.TIME_EXIT

        return False, None
    
    def close(self, exit_price: float, exit_ts: float, reason: ExitReason, 
              fee_usdc: float = 0.0, partial_pct: float = 1.0) -> float:
        """Close position (fully or partially). Returns realized P&L."""
        self.exit_price = exit_price
        self.exit_ts = exit_ts
        self.exit_reason = reason
        
        closed_size = self.remaining_size_usdc * partial_pct
        
        # Mathematical fix: Polymarket position value is determined by number of shares
        # shares = Amount USDC / Entry Price
        # Gross realization = shares * Exit Price
        # PnL = Gross realization - Initial Capital
        if self.entry_price > 0:
            shares = closed_size / self.entry_price
            gross_return = shares * exit_price
            pnl = gross_return - closed_size - fee_usdc
        else:
            pnl = 0.0 - fee_usdc
            
        self.pnl_usdc += pnl
        self.fee_usdc += fee_usdc
        self.remaining_size_usdc -= closed_size
        
        if self.remaining_size_usdc <= 0.01:  # Min $0.01 remaining
            self.status = PositionStatus.CLOSED
            self.remaining_size_usdc = 0
        else:
            self.status = PositionStatus.PARTIAL
        
        return pnl
    
    def is_fully_closed(self) -> bool:
        return self.status == PositionStatus.CLOSED or self.remaining_size_usdc == 0
    
    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Return unrealized P&L as percentage of position size."""
        if self.entry_price == 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price


@dataclass
class WindowPositions:
    """Tracks all positions for a single window."""
    window_end_ts: int
    positions: list[Position] = field(default_factory=list)
    
    def add_position(self, position: Position) -> None:
        self.positions.append(position)
    
    def get_open_positions(self) -> list[Position]:
        return [
            p
            for p in self.positions
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
        ]

    def has_open_positions(self) -> bool:
        return any(
            p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
            for p in self.positions
        )
    
    def total_exposure(self) -> float:
        """Total USD exposure in open positions."""
        return sum(
            p.remaining_size_usdc
            for p in self.positions
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
        )
    
    def net_direction(self) -> str:
        """Return 'UP', 'DOWN', or 'NEUTRAL' based on position balance."""
        up_exposure = sum(
            p.remaining_size_usdc
            for p in self.positions
            if p.direction == "UP" and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
        )
        down_exposure = sum(
            p.remaining_size_usdc
            for p in self.positions
            if p.direction == "DOWN" and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
        )
        if up_exposure > down_exposure:
            return "UP"
        elif down_exposure > up_exposure:
            return "DOWN"
        return "NEUTRAL"
    
    def realized_pnl(self) -> float:
        return sum(p.pnl_usdc for p in self.positions)
    
    def unrealized_pnl(self, up_price: float, down_price: float) -> float:
        """Calculate total unrealized P&L using current market prices."""
        total = 0.0
        for p in self.positions:
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                current_price = up_price if p.direction == "UP" else down_price
                total += p.calculate_unrealized_pnl(current_price)
        return total
    
    def can_add_new_position(self, direction: str, max_positions_per_window: int = 3) -> bool:
        """Check if we can add a new position in this direction."""
        open_in_direction = len(
            [
                p
                for p in self.positions
                if p.direction == direction
                and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
            ]
        )
        return open_in_direction < max_positions_per_window
