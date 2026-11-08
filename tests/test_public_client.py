from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from market.client import PolymarketClient, PublicPolymarketClient
from market.orderer import AmbiguousOrderError


class StubPublicClient(PublicPolymarketClient):
    def __init__(self, payloads, *, asset: str = "BTC"):
        super().__init__(asset=asset)
        self.payloads = payloads

    def _get_json(self, url: str):
        if "/series/" in url:
            return self.payloads["series"]
        if "/events/" in url:
            return self.payloads["event_detail"]
        return self.payloads["events"]


def test_public_client_falls_back_to_event_detail_when_markets_not_inline():
    """An event from the /events query without inline markets is hydrated via a
    per-event /events/{id} detail fetch."""
    end_date = (datetime.now(UTC) + timedelta(minutes=5)).replace(microsecond=0)
    end_date_iso = end_date.isoformat().replace("+00:00", "Z")
    client = StubPublicClient(
        {
            # /events?series_id=... returns the window but no markets inline.
            "events": [
                {
                    "id": "332828",
                    "slug": "btc-updown-5m-1775144700",
                    "title": "Bitcoin Up or Down - April 2, 11:45AM-11:50AM ET",
                    "endDate": end_date_iso,
                    "seriesSlug": "btc-up-or-down-5m",
                }
            ],
            # /events/{id} detail carries the markets.
            "event_detail": {
                "id": "332828",
                "slug": "btc-updown-5m-1775144700",
                "title": "Bitcoin Up or Down - April 2, 11:45AM-11:50AM ET",
                "endDate": end_date_iso,
                "markets": [
                    {
                        "slug": "btc-updown-5m-1775144700",
                        "conditionId": "cond-1",
                        "question": "Bitcoin Up or Down - April 2, 11:45AM-11:50AM ET",
                        "endDate": end_date_iso,
                        "outcomes": "[\"Up\", \"Down\"]",
                        "clobTokenIds": "[\"up-token\", \"down-token\"]",
                    }
                ],
            },
            "series": {},
        }
    )

    markets = client.get_markets()["data"]
    assert len(markets) == 1
    assert markets[0]["market_slug"] == "btc-updown-5m-1775144700"
    assert markets[0]["series_slug"] == "btc-up-or-down-5m"
    assert markets[0]["tokens"][0]["token_id"] == "up-token"
    assert markets[0]["tokens"][1]["token_id"] == "down-token"


def test_public_client_discovers_near_term_window_when_series_returns_far_batch():
    """Regression: /series/{id} returns a far-future daily batch, but the live
    windows are only on /events?series_id=... — discovery must use that source."""
    end_date = (datetime.now(UTC) + timedelta(minutes=4)).replace(microsecond=0)
    end_date_iso = end_date.isoformat().replace("+00:00", "Z")
    far_future = (datetime.now(UTC) + timedelta(hours=19)).replace(microsecond=0)
    far_future_iso = far_future.isoformat().replace("+00:00", "Z")

    client = StubPublicClient(
        {
            # /series/{id} only surfaces a batch ~19h out (reproduces prod).
            "series": {
                "slug": "btc-up-or-down-5m",
                "events": [
                    {"id": "999", "slug": "btc-updown-5m-future", "endDate": far_future_iso}
                ],
            },
            # /events?series_id=... returns the active near-term window with
            # markets inline (no per-event detail fetch needed).
            "events": [
                {
                    "id": "332828",
                    "slug": "btc-updown-5m-now",
                    "endDate": end_date_iso,
                    "seriesSlug": "btc-up-or-down-5m",
                    "markets": [
                        {
                            "slug": "btc-updown-5m-now",
                            "conditionId": "cond-now",
                            "question": "Bitcoin Up or Down - now",
                            "endDate": end_date_iso,
                            "outcomes": "[\"Up\", \"Down\"]",
                            "clobTokenIds": "[\"up-now\", \"down-now\"]",
                        }
                    ],
                }
            ],
            "event_detail": {},
        }
    )

    markets = client.get_markets()["data"]
    assert len(markets) == 1
    assert markets[0]["market_slug"] == "btc-updown-5m-now"
    assert markets[0]["series_slug"] == "btc-up-or-down-5m"
    assert markets[0]["tokens"][0]["token_id"] == "up-now"
    assert markets[0]["tokens"][1]["token_id"] == "down-now"


class ReadTimeout(Exception):
    """Stand-in matching requests.exceptions.ReadTimeout by class name."""


def _client_with_stub(post_side_effect):
    client = PolymarketClient.__new__(PolymarketClient)
    stub = MagicMock()
    stub.create_order.return_value = object()  # signed order, no network
    stub.post_order.side_effect = post_side_effect
    client._client = stub
    return client


@patch("market.client.OrderArgs", lambda **kwargs: object())
def test_create_order_wraps_transport_timeout_as_ambiguous():
    client = _client_with_stub(ReadTimeout("read timed out"))
    with pytest.raises(AmbiguousOrderError):
        client.create_order("tok", 0.58, 5.0, "BUY", order_type="GTC")


@patch("market.client.OrderArgs", lambda **kwargs: object())
def test_create_order_does_not_wrap_clean_api_error():
    client = _client_with_stub(ValueError("invalid price"))
    with pytest.raises(ValueError):
        client.create_order("tok", 0.58, 5.0, "BUY", order_type="GTC")


def test_normalize_order_book_accepts_dict_payload():
    client = PolymarketClient.__new__(PolymarketClient)
    book = {"asks": [{"price": "0.80", "size": "10"}], "bids": []}
    out = client._normalize_order_book(book)
    assert out["asks"] == [{"price": "0.80", "size": "10"}]
    assert out["bids"] == []


def test_normalize_order_book_keeps_object_payload():
    ask = MagicMock()
    ask.price = "0.81"
    ask.size = "5"
    client = PolymarketClient.__new__(PolymarketClient)
    book = MagicMock()
    book.asks = [ask]
    book.bids = []
    out = client._normalize_order_book(book)
    assert out["asks"] == [{"price": "0.81", "size": "5"}]
