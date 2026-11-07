from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request

import websockets

from config import KRAKEN_REST_SYMBOLS, KRAKEN_WS_SYMBOLS
from state.session import BotState


KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
KRAKEN_REST_URL = "https://api.kraken.com/0/public/Ticker"


def fetch_kraken_spot_price(
    asset: str,
    *,
    timeout: float = 8.0,
) -> float | None:
    pair = KRAKEN_REST_SYMBOLS.get(asset.upper())
    if pair is None:
        return None
    query = urllib.parse.urlencode({"pair": pair})
    request = urllib.request.Request(
        f"{KRAKEN_REST_URL}?{query}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return None
    for value in result.values():
        if not isinstance(value, dict):
            continue
        last_trade = value.get("c")
        if isinstance(last_trade, list) and last_trade:
            return float(last_trade[0])
    return None


class KrakenFeed:
    def __init__(self, state_or_states: BotState | list[BotState], *, asset: str = "BTC"):
        self.states = state_or_states if isinstance(state_or_states, list) else [state_or_states]
        self.asset = asset.upper()
        self._logger = logging.getLogger(__name__)
        self._reconnect_delay = 1.0
        self._symbol = KRAKEN_WS_SYMBOLS.get(self.asset)
        if self._symbol is None:
            raise ValueError(f"Unsupported asset for KrakenFeed: {asset}")

    async def connect(self) -> None:
        while True:
            try:
                initial_snapshot = await asyncio.to_thread(fetch_kraken_spot_price, self.asset)
                if initial_snapshot is not None:
                    self._apply_price(initial_snapshot)
                async with websockets.connect(
                    KRAKEN_WS_URL,
                    open_timeout=8.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "params": {
                                    "channel": "trade",
                                    "symbol": [self._symbol],
                                    "snapshot": True,
                                },
                            }
                        )
                    )
                    while True:
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            snapshot = await asyncio.to_thread(fetch_kraken_spot_price, self.asset)
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
                    "Kraken feed disconnected, reconnecting in %.1fs: %s",
                    self._reconnect_delay,
                    exc,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(30.0, self._reconnect_delay * 2)

    def _parse_message(self, raw: str) -> float | None:
        payload = json.loads(raw)
        if payload.get("channel") != "trade":
            return None
        trades = payload.get("data")
        if not isinstance(trades, list) or not trades:
            return None
        latest = trades[-1]
        if not isinstance(latest, dict):
            return None
        price = latest.get("price")
        return float(price) if price is not None else None

    def _apply_price(self, price: float) -> None:
        for state in self.states:
            state.update_price(price, source="KRAKEN")
