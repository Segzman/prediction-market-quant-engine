from __future__ import annotations

import time
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from persistence.ledger import Ledger
from state.session import BotState
from state.window import WindowState


@pytest.fixture(autouse=True)
def isolate_jev_log(tmp_path, monkeypatch):
    import strategy.jev as jev

    monkeypatch.setattr(jev, "JEV_LOG_FILE", str(tmp_path / "jev_calls.jsonl"))
    jev._cache.clear()
    yield
    jev._cache.clear()


@pytest.fixture
def sample_window() -> WindowState:
    end_ts = int(time.time()) + 120
    return WindowState(
        start_ts=end_ts - 300,
        end_ts=end_ts,
        market_id="condition-1",
        up_token_id="up-token",
        down_token_id="down-token",
    )


@pytest.fixture
def bot_state() -> BotState:
    return BotState(btc_price=82000.0)


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.get_order_book.return_value = {
        "asks": [{"price": "0.58"}],
        "bids": [{"price": "0.56"}],
    }
    client.get_market_metadata.return_value = {
        "best_ask": 0.58,
        "tick_size": 0.01,
        "min_order_size": 0.0,
        "neg_risk": False,
    }
    client.get_markets.return_value = {"data": []}
    client.create_order.return_value = {"orderID": "mock-order"}
    client.cancel_order.return_value = {"success": True}
    client.cancel_all_orders.return_value = {"success": True}
    client.get_order.return_value = {"status": "FILLED"}
    client.get_fee_rate_bps.return_value = 0
    return client


@pytest.fixture
def tmp_ledger(tmp_path) -> Ledger:
    return Ledger(filepath=str(tmp_path / "trades.jsonl"))
