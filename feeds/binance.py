from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

import websockets

from config import BINANCE_REST_SYMBOLS, BINANCE_SYMBOLS
from state.session import BotState

BINANCE_WS_BASE_URLS = (
    "wss://stream.binance.com:9443/ws",
    "wss://data-stream.binance.vision/ws",
)
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

BINANCE_REST_BASE_URLS = (
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://data-api.binance.vision",
)

COINBASE_PRODUCT_IDS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}


def fetch_binance_rest_json(
    path: str,
    *,
    attempts: int = 2,
    timeout: float = 8.0,
    logger: logging.Logger | None = None,
) -> dict | list:
    last_error: Exception | None = None
    for base_url in BINANCE_REST_BASE_URLS:
        url = f"{base_url}{path}"
        for attempt in range(1, attempts + 1):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if logger is not None:
                    logger.debug(
                        "Binance REST request failed (%s attempt %s/%s): %s",
                        base_url,
                        attempt,
                        attempts,
                        exc,
                    )
    if last_error is None:
        raise RuntimeError("No Binance REST endpoints configured")
    raise last_error


def fetch_coinbase_spot_price(
    asset: str,
    *,
    timeout: float = 8.0,
) -> float | None:
    product_id = COINBASE_PRODUCT_IDS.get(asset.upper())
    if product_id is None:
        return None
    request = urllib.request.Request(
        f"https://api.exchange.coinbase.com/products/{product_id}/ticker",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    price = payload.get("price")
    return float(price) if price is not None else None


def fetch_binance_spot_price(
    asset: str,
    *,
    logger: logging.Logger | None = None,
) -> float | None:
    symbol = BINANCE_REST_SYMBOLS.get(asset.upper())
    if symbol is None:
        return None
    payload = fetch_binance_rest_json(
        f"/api/v3/ticker/price?symbol={symbol}",
        logger=logger,
    )
    return float(payload["price"])


class BinanceFeed:
    def __init__(self, state_or_states: BotState | list[BotState], *, asset: str = "BTC"):
        self.states = (
            state_or_states
            if isinstance(state_or_states, list)
            else [state_or_states]
        )
        self.asset = asset.upper()
        self._reconnect_delay = 1.0
        self._logger = logging.getLogger(__name__)
        self._ws_urls = self._build_ws_urls(self.asset)
        self._ws_url = self._ws_urls[0]
        self._ws_index = 0

    async def connect(self) -> None:
        while True:
            started_at = time.time()
            try:
                initial_snapshot = await asyncio.to_thread(self._fetch_rest_snapshot)
                if initial_snapshot is not None:
                    self._apply_price(initial_snapshot, source="BINANCE")
                ws_url = self._ws_urls[self._ws_index % len(self._ws_urls)]
                async with websockets.connect(
                    ws_url,
                    open_timeout=8.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                ) as websocket:
                    while True:
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        except TimeoutError:
                            snapshot = await asyncio.to_thread(self._fetch_rest_snapshot)
                            if snapshot is None:
                                continue
                            self._apply_price(snapshot, source="BINANCE")
                            continue
                        self._apply_price(self._parse_trade(raw), source="BINANCE")
                if time.time() - started_at > 10:
                    self._reconnect_delay = 1.0
            except Exception as exc:  # pragma: no cover - network loop
                self._ws_index = (self._ws_index + 1) % len(self._ws_urls)
                self._logger.warning(
                    "Binance feed disconnected, reconnecting in %.1fs via next endpoint: %s",
                    self._reconnect_delay,
                    exc,
                )
                await _sleep(self._reconnect_delay)
                self._reconnect_delay = min(30.0, self._reconnect_delay * 2)

    def _parse_trade(self, raw: str) -> float:
        payload = json.loads(raw)
        return float(payload["p"])

    def _build_ws_urls(self, asset: str) -> list[str]:
        symbol = BINANCE_SYMBOLS.get(asset)
        if symbol is None:
            raise ValueError(f"Unsupported asset for BinanceFeed: {asset}")
        return [f"{base_url}/{symbol}@trade" for base_url in BINANCE_WS_BASE_URLS]

    def _apply_price(self, price: float, *, source: str) -> None:
        updated_ts = time.time()
        for state in self.states:
            state.update_price(price, updated_ts=updated_ts, source=source)

    def _fetch_rest_snapshot(self) -> float | None:
        return fetch_binance_spot_price(self.asset, logger=self._logger)


class CoinbaseFeed:
    def __init__(self, state_or_states: BotState | list[BotState], *, asset: str = "BTC"):
        self.states = (
            state_or_states
            if isinstance(state_or_states, list)
            else [state_or_states]
        )
        self.asset = asset.upper()
        self._logger = logging.getLogger(__name__)
        self._reconnect_delay = 1.0
        self._product_id = COINBASE_PRODUCT_IDS.get(self.asset)
        if self._product_id is None:
            raise ValueError(f"Unsupported asset for CoinbaseFeed: {asset}")
        self._ws_url = COINBASE_WS_URL

    async def connect(self) -> None:
        while True:
            try:
                initial_snapshot = await asyncio.to_thread(fetch_coinbase_spot_price, self.asset)
                if initial_snapshot is not None:
                    self._apply_price(initial_snapshot, source="COINBASE")
                async with websockets.connect(
                    self._ws_url,
                    open_timeout=8.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "product_ids": [self._product_id],
                                "channels": ["ticker"],
                            }
                        )
                    )
                    while True:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                        price = self._parse_message(raw)
                        if price is None:
                            continue
                        self._apply_price(price, source="COINBASE")
            except Exception as exc:  # pragma: no cover - network loop
                self._logger.warning(
                    "Coinbase feed disconnected, reconnecting in %.1fs: %s",
                    self._reconnect_delay,
                    exc,
                )
                await _sleep(self._reconnect_delay)
                self._reconnect_delay = min(30.0, self._reconnect_delay * 2)

    def _parse_message(self, raw: str) -> float | None:
        payload = json.loads(raw)
        if payload.get("type") != "ticker":
            return None
        price = payload.get("price")
        return float(price) if price is not None else None

    def _apply_price(self, price: float, *, source: str) -> None:
        updated_ts = time.time()
        for state in self.states:
            state.update_price(price, updated_ts=updated_ts, source=source)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
