from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config import ENTRY_WINDOW_END, ENTRY_WINDOW_START
from .position import WindowPositions, Position, PositionStatus


class WindowPhase(Enum):
    WAITING = "WAITING"
    ENTRY_WINDOW = "ENTRY_WINDOW"
    TOO_LATE = "TOO_LATE"
    EXPIRED = "EXPIRED"


@dataclass
class WindowState:
    start_ts: int
    end_ts: int
    market_id: str
    up_token_id: str
    down_token_id: str
    open_price: float = 0.0

    # Legacy single-trade tracking (deprecated, kept for compatibility)
    traded: bool = False
    order_id: Optional[str] = None
    market_url: str = ""
    
    # New multi-position tracking
    positions: WindowPositions = field(default=None)
    
    def __post_init__(self):
        if self.positions is None:
            self.positions = WindowPositions(window_end_ts=self.end_ts)

    def seconds_remaining(self, now: int) -> int:
        return max(0, self.end_ts - now)

    def get_phase(self, now: int) -> WindowPhase:
        if now >= self.end_ts:
            return WindowPhase.EXPIRED

        seconds_remaining = self.seconds_remaining(now)
        if seconds_remaining < ENTRY_WINDOW_END:
            return WindowPhase.TOO_LATE
        if seconds_remaining <= ENTRY_WINDOW_START:
            return WindowPhase.ENTRY_WINDOW
        return WindowPhase.WAITING

    def is_in_entry_window(self, now: int) -> bool:
        return self.get_phase(now) == WindowPhase.ENTRY_WINDOW

    def set_open_price(self, price: float) -> None:
        if self.open_price == 0:
            self.open_price = price
    
    # New multi-position methods
    def has_open_positions(self) -> bool:
        """Check if window has any open positions."""
        return self.positions.has_open_positions()
    
    def get_open_positions(self) -> list[Position]:
        """Get all open positions in this window."""
        return self.positions.get_open_positions()
    
    def total_exposure(self) -> float:
        """Total USD exposure in open positions."""
        return self.positions.total_exposure()
    
    def add_position(self, position: Position) -> None:
        """Add a new position to this window."""
        self.positions.add_position(position)
        # Update legacy flag for compatibility
        self.traded = True
        if self.order_id is None:
            self.order_id = position.order_id
    
    def can_trade(self, direction: str, max_positions: int = 3) -> bool:
        """Check if new position can be added."""
        return self.positions.can_add_new_position(direction, max_positions)
    
    def net_direction(self) -> str:
        """Return net directional exposure."""
        return self.positions.net_direction()

