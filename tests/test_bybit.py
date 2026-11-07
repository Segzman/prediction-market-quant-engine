"""Bybit feed message parsing. No network access."""

import json

from feeds.bybit import BybitFeed
from state.session import BotState


def _feed():
    return BybitFeed(BotState(starting_bankroll_usdc=10.0), asset="BTC")


def test_parse_public_trade_message():
    feed = _feed()
    raw = json.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "type": "snapshot",
            "ts": 1700000000000,
            "data": [{"T": 1700000000000, "s": "BTCUSDT", "p": "82965.5", "v": "0.001", "S": "Buy"}],
        }
    )
    assert feed._parse_message(raw) == 82965.5


def test_parse_ignores_subscribe_confirmation():
    feed = _feed()
    raw = json.dumps({"success": True, "op": "subscribe", "ret_msg": ""})
    assert feed._parse_message(raw) is None


def test_parse_ignores_malformed_payload():
    feed = _feed()
    assert feed._parse_message("not json") is None
    assert feed._parse_message(json.dumps({"data": []})) is None


def test_apply_price_records_bybit_source():
    state = BotState(starting_bankroll_usdc=10.0)
    BybitFeed(state, asset="BTC")._apply_price(83000.0)
    assert state.price_observations["BYBIT"].price == 83000.0
