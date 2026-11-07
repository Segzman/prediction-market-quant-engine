"""Kalshi client tests. All transport is faked."""

import pytest

import market.kalshi as kalshi


def _markets_payload(tickers):
    return {
        "markets": [
            {"ticker": t, "status": "open", "close_time": f"2026-10-10T12:{i:02d}:00Z"}
            for i, t in enumerate(tickers)
        ]
    }


def _book_payload(yes_bids, no_bids):
    return {
        "orderbook_fp": {
            "yes_dollars": [[str(p), "10.00"] for p in yes_bids],
            "no_dollars": [[str(p), "10.00"] for p in no_bids],
        }
    }


def test_yes_mid_from_book():
    book = _book_payload([0.40, 0.42], [0.50, 0.55])
    # best yes 0.42, yes ask = 1 - 0.55 = 0.45 -> mid 0.435
    assert kalshi._yes_mid_from_book(book) == pytest.approx(0.435)


def test_yes_mid_none_when_side_missing():
    assert kalshi._yes_mid_from_book({"orderbook_fp": {"yes_dollars": [["0.4", "1"]]}}) is None
    assert kalshi._yes_mid_from_book({}) is None


def test_get_probability_uses_first_series_with_market(monkeypatch):
    calls = []

    def _fake_get_json(url, timeout=8.0):
        calls.append(url)
        if "series_ticker=BTCD" in url and "orderbook" not in url:
            return {"markets": []}
        if "series_ticker=BTCW" in url and "orderbook" not in url:
            return _markets_payload(["KXBTCW-26OCT10"])
        if "orderbook" in url:
            return _book_payload([0.60], [0.30])
        return None

    monkeypatch.setattr(kalshi, "_get_json", _fake_get_json)
    kalshi._cache = None
    try:
        prob = kalshi.get_btc_yes_probability()
    finally:
        kalshi._cache = None
    # best yes 0.60, yes ask = 1 - 0.30 = 0.70 -> mid 0.65
    assert prob == pytest.approx(0.65)
    assert any("orderbook" in url for url in calls)


def test_get_probability_none_when_no_markets(monkeypatch):
    monkeypatch.setattr(kalshi, "_get_json", lambda url, timeout=8.0: {"markets": []})
    kalshi._cache = None
    try:
        assert kalshi.get_btc_yes_probability() is None
    finally:
        kalshi._cache = None


def test_get_probability_caches(monkeypatch):
    calls = []

    def _fake_get_json(url, timeout=8.0):
        calls.append(url)
        return _book_payload([0.5], [0.4])

    monkeypatch.setattr(kalshi, "_active_btc_ticker", lambda: "T")
    monkeypatch.setattr(kalshi, "_get_json", _fake_get_json)
    kalshi._cache = None
    try:
        assert kalshi.get_btc_yes_probability() == pytest.approx(0.55)
        assert kalshi.get_btc_yes_probability() == pytest.approx(0.55)
    finally:
        kalshi._cache = None
    assert len(calls) == 1
