import time
import asyncio
import pytest

from market.orderer import BookSnapshot, OrderStatus, Orderer
from state.types import Direction


def test_dry_run_does_not_call_api(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    mock_client.create_order.assert_not_called()
    assert result.status == OrderStatus.FILLED
    assert result.dry_run is True
    assert result.submitted_price == 0.58
    assert result.fee_rate_bps == 0


def test_dry_run_returns_fake_order_id(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    assert result.order_id is not None
    assert "dry-run" in result.order_id


def test_live_mode_calls_api_with_correct_params(mock_client):
    mock_client.create_order.return_value = {"orderID": "real-id-123"}
    mock_client.get_fee_rate_bps.return_value = 17
    orderer = Orderer(mock_client, dry_run=False)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args
    assert call_kwargs[1]["price"] == 0.58
    assert call_kwargs[1]["token_id"] == "token_up"
    assert call_kwargs[1]["order_type"] == "GTC"
    assert result.status == OrderStatus.SUBMITTED
    assert result.order_id == "real-id-123"
    assert result.submitted_price == 0.58
    assert result.fee_rate_bps == 17


def test_live_mode_rejects_ask_outside_slippage_cap(mock_client):
    mock_client.get_market.return_value = {
        "enable_order_book": True,
        "minimum_order_size": "1.0",
        "tick_size": "0.01",
    }
    mock_client.get_market_info.return_value = {
        "best_ask": 0.70,
        "tick_size": 0.01,
        "min_order_size": 0.0,
        "neg_risk": False,
    }
    mock_client.get_order_book.return_value = {
        "asks": [{"price": "0.70"}],
        "bids": [{"price": "0.68"}],
    }
    mock_client.create_order.return_value = {"orderID": "real-id-123"}
    orderer = Orderer(mock_client, dry_run=False)
    with pytest.raises(RuntimeError, match="slippage cap"):
        orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)


def test_late_conviction_allows_wider_slippage_cap(mock_client):
    # Using 0.70 ask with 0.69 signal (within 200 bps slippage cap)
    mock_client.get_market_metadata.return_value = {
        "best_ask": 0.70,
        "tick_size": 0.01,
        "min_order_size": 0.0,
        "neg_risk": False,
    }
    mock_client.get_order_book.return_value = {
        "asks": [{"price": "0.70"}],
        "bids": [{"price": "0.68"}],
    }
    mock_client.get_fee_rate_bps.return_value = 17
    mock_client.create_order.return_value = {"orderID": "real-id-123"}
    orderer = Orderer(mock_client, dry_run=False)

    result = orderer.place_bet(
        "token_up",
        Direction.UP,
        5.0,
        0.69,
        entry_regime="LATE_CONVICTION",
    )

    assert result.order_id == "real-id-123"
    assert result.submitted_price == 0.70


def test_cancel_called_on_window_expiry(mock_client):
    mock_client.get_order.return_value = {"status": "OPEN"}
    mock_client.cancel_order.return_value = {"success": True}
    orderer = Orderer(mock_client, dry_run=False)
    result = orderer.cancel("real-id-123")
    mock_client.cancel_order.assert_called_once_with("real-id-123")
    assert result is True


def test_wait_for_fill_dry_run_returns_immediately(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = asyncio.run(
        orderer.wait_for_fill("dry-run-xyz", deadline_ts=int(time.time()) + 60)
    )
    assert result.status == OrderStatus.FILLED
    assert result.fee_rate_bps == 0


def test_wait_for_fill_cancels_past_deadline(mock_client):
    mock_client.get_order.return_value = {"status": "OPEN"}
    mock_client.cancel_order.return_value = {}
    orderer = Orderer(mock_client, dry_run=False)
    past_deadline = int(time.time()) - 1
    result = asyncio.run(orderer.wait_for_fill("real-id", deadline_ts=past_deadline))
    assert result.status == OrderStatus.CANCELLED


def test_cancel_all_delegates_when_supported(mock_client):
    orderer = Orderer(mock_client, dry_run=False)
    assert orderer.cancel_all() is True
    mock_client.cancel_all_orders.assert_called_once()


def test_wait_for_fill_propagates_fill_economics(mock_client):
    mock_client.get_order.return_value = {
        "status": "FILLED",
        "filled_size": "8.62",
        "avg_price": "0.59",
        "price": "0.58",
        "fee_rate_bps": 23,
    }
    orderer = Orderer(mock_client, dry_run=False)
    result = asyncio.run(orderer.wait_for_fill("real-id", deadline_ts=int(time.time()) + 60))
    assert result.status == OrderStatus.FILLED
    assert result.filled_size == pytest.approx(8.62)
    assert result.avg_price == pytest.approx(0.59)
    assert result.submitted_price == pytest.approx(0.58)
    assert result.fee_rate_bps == 23


def test_wait_for_fill_returns_partial_before_deadline_cancel(mock_client):
    mock_client.get_order.return_value = {
        "status": "PARTIALLY_FILLED",
        "filled_size": "4.00",
        "avg_price": "0.60",
        "price": "0.58",
        "fee_rate_bps": 9,
    }
    orderer = Orderer(mock_client, dry_run=False)
    result = asyncio.run(
        orderer.wait_for_fill(
            "real-id",
            deadline_ts=int(time.time()) + 1,
            poll_interval=1.0,
        )
    )
    assert result.status == OrderStatus.PARTIAL
    assert result.filled_size == pytest.approx(4.0)
    assert result.avg_price == pytest.approx(0.60)
    mock_client.cancel_order.assert_called_once_with("real-id")


def test_get_book_snapshot_reports_side_depth(mock_client):
    mock_client.get_order_book.return_value = {
        "asks": [{"price": "0.44", "size": "5.0"}, {"price": "0.46", "size": "3.0"}],
        "bids": [{"price": "0.41", "size": "7.0"}, {"price": "0.39", "size": "4.0"}],
    }
    orderer = Orderer(mock_client, dry_run=True)

    snapshot = orderer.get_book_snapshot("token_up")

    assert snapshot.ask_count == 2
    assert snapshot.bid_count == 2
    assert snapshot.best_ask == pytest.approx(0.44)
    assert snapshot.best_bid == pytest.approx(0.41)
    assert snapshot.best_ask_size == pytest.approx(5.0)
    assert snapshot.best_bid_size == pytest.approx(7.0)
    assert snapshot.total_ask_size == pytest.approx(8.0)
    assert snapshot.total_bid_size == pytest.approx(11.0)


def test_book_snapshot_reports_queue_imbalance_microprice_and_depth():
    book = BookSnapshot(
        ask_count=2,
        bid_count=2,
        best_ask=0.44,
        best_bid=0.41,
        best_ask_size=5.0,
        best_bid_size=7.0,
        total_ask_size=8.0,
        total_bid_size=11.0,
    )

    assert book.queue_imbalance() == pytest.approx((7.0 - 5.0) / 12.0)
    assert book.microprice() == pytest.approx(((0.44 * 7.0) + (0.41 * 5.0)) / 12.0)
    assert book.depth_to_fill_usdc() == pytest.approx(8.0 * 0.44)


def test_place_bet_adjusts_to_market_minimum_size(mock_client):
    mock_client.get_market_metadata.return_value = {
        "best_ask": 0.80,
        "tick_size": 0.01,
        "min_order_size": 5.0,
        "neg_risk": False,
    }
    mock_client.get_order_book.return_value = {
        "asks": [{"price": "0.80"}],
        "bids": [{"price": "0.79"}],
    }
    orderer = Orderer(mock_client, dry_run=True)

    result = orderer.place_bet("token_up", Direction.UP, 2.5, 0.80)

    assert result.status == OrderStatus.FILLED
    assert result.filled_size >= 5.0


def test_minimum_usdc_amount_reflects_exchange_minimum(mock_client):
    mock_client.get_market_metadata.return_value = {
        "best_ask": 0.80,
        "tick_size": 0.01,
        "min_order_size": 5.0,
        "neg_risk": False,
    }
    mock_client.get_order_book.return_value = {
        "asks": [{"price": "0.80"}],
        "bids": [{"price": "0.79"}],
    }
    orderer = Orderer(mock_client, dry_run=True)

    minimum = orderer.minimum_usdc_amount("token_up", 0.80)

    assert minimum == pytest.approx(4.0)
