from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request

import websockets

from config import BYBIT_REST_SYMBOLS, BYBIT_WS_SYMBOLS
from state.session import BotState


BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
BYBIT_REST_URL = "https://api.bybit.com/v5/market/tickers"


def fetch_bybit_spot_price(
    asset: str,
    *,
    timeout: float = 8.0,
) -> float | None:
    symbol = BYBIT_REST_SYMBOLS.get(asset.upper())
    if symbol is None:
        return None
    query = urllib.parse.urlencode({"category": "spot", "symbol": symbol})
    request = urllib.request.Request(
        f"{BYBIT_REST_URL}?{query}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tickers = ((payload.get("result") or {}).get("list")) or []
    if not tickers or not isinstance(tickers[0], dict):
        return None
    last = tickers[0].get("lastPrice")
    return float(last) if last is not None else None


class BybitFeed:
    def __init__(self, state_or_states: BotState | list[BotState], *, asset: str = "BTC"):
        self.states = state_or_states if isinstance(state_or_states, list) else [state_or_states]
        self.asset = asset.upper()
        self._logger = logging.getLogger(__name__)
        self._reconnect_delay = 1.0
        self._symbol = BYBIT_WS_SYMBOLS.get(self.asset)
        if self._symbol is None:
            raise ValueError(f"Unsupported asset for BybitFeed: {asset}")

    async def connect(self) -> None:
        while True:
            try:
                initial_snapshot = await asyncio.to_thread(fetch_bybit_spot_price, self.asset)
                if initial_snapshot is not None:
                    self._apply_price(initial_snapshot)
                async with websockets.connect(
                    BYBIT_WS_URL,
                    open_timeout=8.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "args": [f"publicTrade.{self._symbol}"],
                            }
                        )
                    )
                    while True:
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            snapshot = await asyncio.to_thread(fetch_bybit_spot_price, self.asset)
                            if snapshot is None:
                                continue
                            self._apply_price(snapshot)
                            continue
                        price = self._parse_message(raw)
                        if price is None:
                            continue
                        self._apply_price(price)
                if self._reconnect_delay <= 1.0:
                    self._reconnect_delay = 1.0
            except Exception as exc:  # pragma: no cover - network loop
                self._logger.warning(
                    "Bybit feed disconnected, reconnecting in %.1fs: %s",
                    self._reconnect_delay,
                    exc,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(30.0, self._reconnect_delay * 2)

    def _parse_message(self, raw: str) -> float | None:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("op") == "subscribe" or "success" in payload:
            return None
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        last_trade = data[-1]
        if not isinstance(last_trade, dict):
            return None
        price = last_trade.get("p")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    def _apply_price(self, price: float) -> None:
        for state in self.states:
            state.update_price(price, source="BYBIT")
