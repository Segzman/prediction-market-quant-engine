from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from config import (
    ALLOW_TRADING_WITHOUT_BTC_PRICE,
    CONSECUTIVE_LOSS_LIMIT,
    CONSECUTIVE_LOSS_PAUSE_SECONDS,
    COOLDOWN_SECONDS,
    LOW_CONVICTION_COOLDOWN_SECONDS,
    MAX_CANARY_DRAWDOWN_USDC,
    MAX_CONCURRENT_POSITIONS,
    MAX_DAILY_LOSS_USDC,
    MAX_OPEN_ORDERS,
    MAX_UNRESOLVED_EXPOSURE_USDC,
    MAX_TRADES_PER_DAY,
    MAX_WINDOW_EXPOSURE_USDC,
    MAX_POSITIONS_PER_WINDOW,
)
from state.session import BotState


class DailyGuard:
    def __init__(self, state: BotState):
        self.state = state
        self._reset_date: Optional[datetime.date] = datetime.now(timezone.utc).date()

    def can_enter(self, direction: Optional[str] = None) -> tuple[bool, str]:
        """Check if trading is allowed. Optionally check for specific direction."""
        if self._should_reset_daily():
            self.state.reset_daily()

        if self.state.daily_pnl <= -MAX_DAILY_LOSS_USDC:
            return False, f"Daily loss limit reached: ${self.state.daily_pnl:.2f}"

        drawdown = max(
            0.0,
            self.state.starting_bankroll_usdc - self.state.current_bankroll_usdc(),
        )
        if drawdown >= MAX_CANARY_DRAWDOWN_USDC:
            return False, f"Drawdown limit reached: ${drawdown:.2f}"

        if self.state.daily_trades >= MAX_TRADES_PER_DAY:
            return False, f"Max daily trades reached: {self.state.daily_trades}"

        if self.state.available_bankroll_usdc() <= 0:
            return False, "No bankroll available"

        if self.state.open_primary_orders_count() >= MAX_OPEN_ORDERS:
            return False, f"Max open orders reached: {self.state.open_primary_orders_count()}"

        if self.state.open_primary_positions_count() >= MAX_CONCURRENT_POSITIONS:
            return (
                False,
                f"Max concurrent positions reached: {self.state.open_primary_positions_count()}",
            )

        unresolved_exposure = self.state.pending_exposure_usdc()
        if unresolved_exposure >= MAX_UNRESOLVED_EXPOSURE_USDC:
            return False, f"Max unresolved exposure reached: ${unresolved_exposure:.2f}"

        if self.state.current_window and self.state.current_window.total_exposure() >= MAX_WINDOW_EXPOSURE_USDC:
            return (
                False,
                f"Max window exposure reached: ${self.state.current_window.total_exposure():.2f}",
            )

        elapsed_since_trade = time.time() - self.state.last_trade_ts
        if (
            self.state.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT
            and self.state.last_trade_ts
            and elapsed_since_trade < CONSECUTIVE_LOSS_PAUSE_SECONDS
        ):
            return (
                False,
                "Consecutive loss pause: "
                f"{CONSECUTIVE_LOSS_PAUSE_SECONDS - elapsed_since_trade:.0f}s remaining",
            )

        active_cooldown = COOLDOWN_SECONDS if self.state.last_trade_high_conviction else LOW_CONVICTION_COOLDOWN_SECONDS
        if self.state.last_trade_ts and elapsed_since_trade < active_cooldown:
            return (
                False,
                f"Cooldown active: {active_cooldown - elapsed_since_trade:.0f}s remaining",
            )

        if self.state.is_price_stale() and not ALLOW_TRADING_WITHOUT_BTC_PRICE:
            return False, "BTC price is stale"

        # Multi-trade: Check if specific direction allows new positions
        if direction and self.state.current_window:
            if not self.state.current_window.can_trade(direction, MAX_POSITIONS_PER_WINDOW):
                return False, f"Max {MAX_POSITIONS_PER_WINDOW} {direction} positions in window"

        return True, ""

    def _should_reset_daily(self) -> bool:
        today = datetime.now(timezone.utc).date()
        if self._reset_date != today:
            self._reset_date = today
            return True
        return False
