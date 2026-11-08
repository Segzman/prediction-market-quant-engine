from __future__ import annotations

import asyncio
import math
import time

from state import BotState
from state.session import apply_price


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
