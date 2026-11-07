import time
from datetime import datetime, timezone

from market.finder import MarketFinder


def _next_aligned_ts() -> int:
    """Return the next 5-minute aligned timestamp (Polymarket windows are always % 300 == 0)."""
    now = int(time.time())
    return ((now // 300) + 1) * 300


def test_finds_active_window(mock_client):
    end_ts = _next_aligned_ts()
    mock_client.get_markets.return_value = {
        "data": [
            {
                "market_slug": "btc-updown-5m-1234",
                "condition_id": "cond-abc",
                "end_date_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
                "tokens": [
                    {"outcome": "Up", "token_id": "up-token"},
                    {"outcome": "Down", "token_id": "down-token"},
                ],
            }
        ]
    }
    finder = MarketFinder(mock_client)
    window = finder.find_active_window()
    assert window is not None
    assert window.up_token_id == "up-token"
    assert window.down_token_id == "down-token"


def test_skips_expired_windows(mock_client):
    now = int(time.time())
    mock_client.get_markets.return_value = {
        "data": [
            {
                "market_slug": "btc-updown-5m-1234",
                "condition_id": "cond-abc",
                "end_date_iso": datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat(),
                "tokens": [
                    {"outcome": "Up", "token_id": "up"},
                    {"outcome": "Down", "token_id": "down"},
                ],
            }
        ]
    }
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None


def test_skips_non_btc_markets(mock_client):
    end_ts = _next_aligned_ts()
    mock_client.get_markets.return_value = {
        "data": [
            {
                "market_slug": "eth-updown-5m-9999",
                "condition_id": "cond-xyz",
                "end_date_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
                "tokens": [
                    {"outcome": "Up", "token_id": "u"},
                    {"outcome": "Down", "token_id": "d"},
                ],
            }
        ]
    }
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None


def test_uses_cache_within_ttl(mock_client):
    mock_client.get_markets.return_value = {"data": []}
    finder = MarketFinder(mock_client)
    finder.find_active_window()
    finder.find_active_window()
    assert mock_client.get_markets.call_count == 1


def test_returns_none_when_no_active_window(mock_client):
    mock_client.get_markets.return_value = {"data": []}
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None


def test_returns_none_when_market_discovery_raises(mock_client):
    mock_client.get_markets.side_effect = TimeoutError("gamma timeout")
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None


def test_uses_cached_window_when_refresh_raises(mock_client):
    end_ts = _next_aligned_ts()
    mock_client.get_markets.return_value = {
        "data": [
            {
                "market_slug": "btc-updown-5m-1234",
                "condition_id": "cond-abc",
                "end_date_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
                "tokens": [
                    {"outcome": "Up", "token_id": "up-token"},
                    {"outcome": "Down", "token_id": "down-token"},
                ],
            }
        ]
    }
    finder = MarketFinder(mock_client)
    first = finder.find_active_window()
    assert first is not None

    finder._cache_ts = time.time() - 999
    mock_client.get_markets.side_effect = TimeoutError("gamma timeout")

    recovered = finder.find_active_window()
    assert recovered is not None
    assert recovered.up_token_id == "up-token"
