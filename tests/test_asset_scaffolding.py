import time

from feeds.binance import BinanceFeed, CoinbaseFeed
from market.finder import MarketFinder
from state.session import BotState


class StubClient:
    def __init__(self, data):
        self.data = data

    def get_markets(self):
        return {"data": self.data}


def test_eth_window_market_is_matched():
    now = int(time.time())
    # Snap to next aligned 5-minute boundary (Polymarket windows are always % 300 == 0)
    end_ts = ((now // 300) + 1) * 300
    client = StubClient(
        [
            {
                "market_slug": f"eth-updown-5m-{end_ts}",
                "condition_id": "eth-cond",
                "question": "Ethereum Up or Down - now",
                "end_date_iso": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(end_ts)),
                "series_slug": "eth-up-or-down-5m",
                "tokens": [
                    {"outcome": "Up", "token_id": "eth-up"},
                    {"outcome": "Down", "token_id": "eth-down"},
                ],
            }
        ]
    )
    finder = MarketFinder(client, asset="ETH")
    window = finder.find_active_window()
    assert window is not None, f"Expected window with end_ts={end_ts} (mod 300={end_ts % 300})"
    assert window.up_token_id == "eth-up"


def test_binance_feed_uses_asset_specific_symbol():
    feed = BinanceFeed(BotState(), asset="POL")
    assert "polusdt@trade" in feed._ws_url


def test_coinbase_feed_uses_asset_specific_product():
    feed = CoinbaseFeed(BotState(), asset="BTC")
    assert feed._product_id == "BTC-USD"
