from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

import websockets

from config import GEMINI_SYMBOLS
from state.session import BotState


GEMINI_WS_URL = "wss://ws.gemini.com"
GEMINI_REST_URL = "https://api.gemini.com/v1/pubticker"


def fetch_gemini_spot_price(
    asset: str,
    *,
    timeout: float = 8.0,
) -> float | None:
    symbol = GEMINI_SYMBOLS.get(asset.upper())
    if symbol is None:
        return None
    request = urllib.request.Request(
        f"{GEMINI_REST_URL}/{symbol}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    last = payload.get("last")
    return float(last) if last is not None else None


class GeminiFeed:
    def __init__(self, state_or_states: BotState | list[BotState], *, asset: str = "BTC"):
        self.states = state_or_states if isinstance(state_or_states, list) else [state_or_states]
        self.asset = asset.upper()
        self._logger = logging.getLogger(__name__)
        self._reconnect_delay = 1.0
        self._symbol = GEMINI_SYMBOLS.get(self.asset)
        if self._symbol is None:
            raise ValueError(f"Unsupported asset for GeminiFeed: {asset}")

    async def connect(self) -> None:
        while True:
            try:
                initial_snapshot = await asyncio.to_thread(fetch_gemini_spot_price, self.asset)
                if initial_snapshot is not None:
                    self._apply_price(initial_snapshot)
                async with websockets.connect(
                    GEMINI_WS_URL,
                    open_timeout=8.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "id": "price-stream",
                                "method": "subscribe",
                                "params": [f"{self._symbol}@trade"],
                            }
                        )
                    )
                    while True:
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            snapshot = await asyncio.to_thread(fetch_gemini_spot_price, self.asset)
                            if snapshot is None:
                                continue
                            self._apply_price(snapshot)
                            continue
                        price = self._parse_message(raw)
                        if price is None:
                            continue
                        self._apply_price(price)
            except Exception as exc:  # pragma: no cover - network loop
                self._logger.warning(
                    "Gemini feed disconnected, reconnecting in %.1fs: %s",
                    self._reconnect_delay,
                    exc,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(30.0, self._reconnect_delay * 2)

    def _parse_message(self, raw: str) -> float | None:
        payload = json.loads(raw)
        price = payload.get("p")
        return float(price) if price is not None else None

    def _apply_price(self, price: float) -> None:
        for state in self.states:
            state.update_price(price, source="GEMINI")
