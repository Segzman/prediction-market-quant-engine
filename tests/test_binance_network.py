from __future__ import annotations

import urllib.error

from bot import fetch_binance_agg_trade_price, fetch_binance_price_snapshot
from feeds.binance import fetch_binance_rest_json


class _FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_binance_rest_json_falls_back_to_next_endpoint(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("handshake timeout")
        return _FakeResponse('{"price":"70123.45"}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    payload = fetch_binance_rest_json("/api/v3/ticker/price?symbol=BTCUSDT")

    assert payload["price"] == "70123.45"
    assert len(calls) == 3
    assert calls[0] == calls[1]
    assert calls[1] != calls[2]


def test_fetch_binance_price_snapshot_uses_shared_rest_fallback(monkeypatch):
    monkeypatch.setattr(
        "bot.fetch_binance_spot_price",
        lambda asset, logger=None: 70200.0,
    )

    assert fetch_binance_price_snapshot("BTC") == 70200.0


def test_fetch_binance_price_snapshot_falls_back_to_coinbase(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("binance down")

    monkeypatch.setattr("bot.fetch_binance_spot_price", fail)
    monkeypatch.setattr("bot.fetch_coinbase_spot_price", lambda asset: 70444.12)

    assert fetch_binance_price_snapshot("BTC") == 70444.12


def test_fetch_binance_agg_trade_price_uses_shared_rest_fallback(monkeypatch):
    monkeypatch.setattr(
        "bot.fetch_binance_rest_json",
        lambda path, logger=None: [{"p": "70300.50"}],
    )

    assert fetch_binance_agg_trade_price("BTC", 1234567890000) == 70300.5
