import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import (
    assess_book_quality,
    DemoClient,
    build_feature_snapshot,
    build_model_runtimes,
    build_runtime_status,
    choose_better_signal,
    estimate_trade_fee_usdc,
    force_interval_trades_enabled,
    hedge_enabled_for_mode,
    kill_switch_engaged,
    parse_model_names,
    refresh_window_books,
    reconcile_live_ledger,
    resolve_with_binance,
    perform_live_preflight,
    should_enter_on_waiting_conviction,
    should_fire_snipe_signal,
    sweep_unresolved_trades_once,
    write_runtime_status_file,
)
from market.client import AccountSnapshot
from market.orderer import BookSnapshot, OrderResult, OrderStatus
from persistence.ledger import Ledger
from state.position import ExitReason, Position, PositionStatus
from state.session import BotState
from state.types import Direction
from state.window import WindowState
from strategy.entry_helpers import BookAssessment
from strategy.signal import Signal, SignalEvaluation


def test_parse_model_names_defaults_to_single_model():
    assert parse_model_names("A", None) == ("A",)


def test_parse_model_names_deduplicates_and_preserves_order():
    assert parse_model_names("A", "b,a,b,c") == ("B", "A", "C")


def test_parse_model_names_rejects_unknown_model():
    with pytest.raises(ValueError, match="Unsupported model"):
        parse_model_names("A", "A,D")


def test_build_model_tracks_each_has_own_state(mock_client):
    """Each ModelTrack gets its own BotState, not a shared reference."""
    from bot import build_model_tracks

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    assert len(tracks) == 3
    # Each track must have an independent BotState
    states = [t.state for t in tracks]
    for i, s1 in enumerate(states):
        for j, s2 in enumerate(states):
            if i != j:
                assert s1 is not s2, f"tracks[{i}] and tracks[{j}] share the same BotState"


def test_live_mode_uses_safe_defaults():
    assert force_interval_trades_enabled(live_mode=True) is False
    assert hedge_enabled_for_mode(live_mode=True) is True  # Hedging now enabled for loss protection
    assert force_interval_trades_enabled(live_mode=False) is False  # Disabled to avoid forced bad trades


def test_kill_switch_detects_file(tmp_path, monkeypatch):
    target = tmp_path / "stop.flag"
    monkeypatch.setattr("bot.LIVE_KILL_SWITCH_FILE", str(target))
    assert kill_switch_engaged(live_mode=True) is False
    target.write_text("stop", encoding="utf-8")
    assert kill_switch_engaged(live_mode=True) is True


def test_perform_live_preflight_caps_bankroll_and_cancels_open_orders():
    class FakeClient:
        def __init__(self):
            self.cancelled = False

        def get_account_snapshot(self):
            return AccountSnapshot(
                balance_usdc=25.0,
                allowance_usdc=25.0,
                available_to_trade_usdc=25.0,
                open_orders=[] if self.cancelled else [{"id": "open-1"}],
            )

        def cancel_all_orders(self):
            self.cancelled = True

    client = FakeClient()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("bot.LIVE_STARTUP_CANCEL_ALL", True)
        monkeypatch.setattr("bot.LIVE_REQUIRE_EMPTY_OPEN_ORDERS", True)
        monkeypatch.setattr("bot.LIVE_BALANCE_BUFFER_USDC", 2.0)
        result = perform_live_preflight(client, 30.0)
    assert client.cancelled is True
    assert result.effective_bankroll_usdc == 23.0
    assert result.open_order_count == 0


def test_perform_live_preflight_fails_on_open_orders_without_cancel():
    class FakeClient:
        def get_account_snapshot(self):
            return AccountSnapshot(
                balance_usdc=25.0,
                allowance_usdc=25.0,
                available_to_trade_usdc=25.0,
                open_orders=[{"id": "open-1"}],
            )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("bot.LIVE_STARTUP_CANCEL_ALL", False)
        monkeypatch.setattr("bot.LIVE_REQUIRE_EMPTY_OPEN_ORDERS", True)
        with pytest.raises(SystemExit, match="open orders"):
            perform_live_preflight(FakeClient(), 10.0)


def test_build_feature_snapshot_captures_calibration_fields():
    window = WindowState(
        start_ts=100,
        end_ts=400,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=100.0,
    )
    signal = Signal(
        direction=Direction.UP,
        confidence=0.73,
        theo_prob=0.73,
        market_price=0.61,
        edge=0.12,
        ev=0.5,
        model_used="A",
    )
    snapshot = build_feature_snapshot(
        window=window,
        signal=signal,
        btc_price=101.0,
        seconds_remaining=27,
        up_price=0.61,
        down_price=0.39,
        volatility=0.002,
        model_context=None,
        force_trade=False,
        live_mode=True,
    )
    assert snapshot["move_pct_bin"] == ">0.0010"
    assert snapshot["time_bin"] == "0-30"
    assert snapshot["current_price"] == 101.0
    assert snapshot["volatility"] == 0.002
    assert "model_c_bucket" in snapshot
    assert "base_prob_bin" in snapshot
    assert "pooled_probability" in snapshot
    assert "calibration_regime" in snapshot


def test_create_position_from_trade_shortens_time_exit_for_weak_entry():
    from bot import create_position_from_trade

    window = WindowState(
        start_ts=100,
        end_ts=400,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=100.0,
    )
    signal = Signal(
        direction=Direction.UP,
        confidence=0.64,
        theo_prob=0.64,
        market_price=0.58,
        edge=0.03,
        ev=0.2,
        model_used="B",
        entry_regime="MISPRICING",
        market_conviction=0.74,
    )

    position = create_position_from_trade(
        order_id="entry-1",
        window=window,
        direction="UP",
        size_usdc=5.0,
        entry_price=0.58,
        entry_ts=time.time(),
        signal=signal,
        feature_snapshot={"pooled_probability": 0.74},
        seconds_remaining=140,
    )

    assert position.time_exit_seconds is not None
    assert position.time_exit_seconds < 5
    assert position.conviction_decay_floor >= 0.60


def test_reconcile_live_ledger_resolves_and_cancels(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    ledger.record_trade(
        window_end_ts=100,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="resolved-order",
        dry_run=False,
        market_slug="btc-updown-5m-100",
        feature_snapshot={"move_pct_bin": "<=0.0000", "time_bin": "30-60"},
    )
    ledger.record_trade(
        window_end_ts=9999999999,
        direction="DOWN",
        amount_usdc=5.0,
        price=0.42,
        edge=0.05,
        ev=0.3,
        model="A",
        order_id="cancelled-order",
        dry_run=False,
    )

    class FakeClient:
        def get_market_resolution(self, slug):
            if slug == "btc-updown-5m-100":
                return {"resolved_up": True}
            return None

        def get_order(self, order_id):
            return {"status": "CANCELED"} if order_id == "cancelled-order" else {"status": "FILLED"}

    result = reconcile_live_ledger(FakeClient(), ledger)
    assert result.resolved_count == 1
    assert result.cancelled_count == 1
    assert result.unresolved_count == 0

    resolutions = ledger.get_all_resolutions()
    assert resolutions[0]["move_pct_bin"] == "<=0.0000"


def test_reconcile_live_ledger_records_fill_and_untracked_trade(tmp_path, monkeypatch):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    ledger.record_trade(
        window_end_ts=9999999999,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="filled-order",
        dry_run=False,
        execution_snapshot={"token_id": "up-1"},
    )

    class FakeClient:
        def get_trades(self, after=None):
            del after
            return [
                {"order_id": "filled-order", "size": "8.62", "price": "0.59"},
                {"order_id": "manual-order", "size": "4.20", "price": "0.44"},
            ]

        def get_market_resolution(self, slug):
            del slug
            return None

        def get_order(self, order_id):
            del order_id
            return {"status": "FILLED", "filled_size": "8.62", "avg_price": "0.59"}

    monkeypatch.setattr("bot.LIVE_RECON_LOOKBACK_SECONDS", 3600)
    result = reconcile_live_ledger(FakeClient(), ledger)

    assert result.fill_count == 1
    assert result.unresolved_count == 1
    assert result.untracked_trade_count == 1
    fills = ledger.get_all_fills()
    assert fills[0]["details"]["startup_reconciliation"] is True


def test_runtime_status_file_captures_model_state(mock_client, tmp_path):
    runtimes = build_model_runtimes(
        models=("A", "B"),
        client=mock_client,
        asset="BTC",
        mode_label="SHADOW LIVE",
        market_source="binance+polymarket",
        ledger_path=str(tmp_path / "trades.jsonl"),
        dry_run=True,
        initial_price=82100.0,
        initial_price_updated_ts=time.time(),
        starting_bankroll=10.0,
    )
    runtimes[0].stats.window_slug = "btc-updown-5m-123"
    runtimes[0].stats.window_phase = "ENTRY"
    runtimes[0].stats.seconds_remaining = 27
    runtimes[0].stats.last_order_status = "FILLED"
    runtimes[0].stats.last_resolution = "WIN"
    runtimes[0].stats.last_event = "Placed trade"
    runtimes[0].stats.current_up_ask = 0.41
    runtimes[0].stats.current_down_ask = 0.59
    runtimes[0].stats.last_strongest_side = "DOWN"
    runtimes[0].stats.last_regime_candidate = "LATE_CONVICTION"
    runtimes[0].stats.last_hedge_decision = "HEDGE_SKIPPED_MIN_SIZE"
    runtimes[0].state.total_trades = 2
    runtimes[0].state.wins = 1
    runtimes[0].state.losses = 0
    runtimes[0].state.daily_pnl = 1.25

    payload = build_runtime_status(runtimes)
    assert payload["mode"] == "SHADOW LIVE"
    assert payload["asset"] == "BTC"
    assert payload["price"]["source"] is None
    assert payload["market"]["slug"] == "btc-updown-5m-123"
    assert payload["market"]["up_ask"] == 0.41
    assert payload["models"][0]["last_regime_candidate"] == "LATE_CONVICTION"
    assert payload["models"][0]["last_hedge_decision"] == "HEDGE_SKIPPED_MIN_SIZE"
    assert payload["models"][0]["model"] == "A"
    assert payload["models"][0]["last_order_status"] == "FILLED"
    assert payload["models"][0]["wins"] == 1

    status_path = tmp_path / "runtime_status.json"
    write_runtime_status_file(runtimes, str(status_path))
    written = json.loads(status_path.read_text(encoding="utf-8"))
    assert written["models"][0]["daily_pnl"] == 1.25
    assert written["models"][1]["model"] == "B"


def test_runtime_status_file_includes_price_source(mock_client, tmp_path):
    runtimes = build_model_runtimes(
        models=("A",),
        client=mock_client,
        asset="BTC",
        mode_label="SHADOW LIVE",
        market_source="binance+coinbase+polymarket",
        ledger_path=str(tmp_path / "trades.jsonl"),
        dry_run=True,
        initial_price=82100.0,
        initial_price_updated_ts=time.time(),
        starting_bankroll=10.0,
    )
    runtimes[0].state.update_price(82101.0, updated_ts=time.time(), source="COINBASE")

    payload = build_runtime_status(runtimes)

    assert payload["price"]["source"] == "COINBASE"


def test_demo_client_resolves_finished_window(monkeypatch):
    client = DemoClient(window_seconds=25, start_ts=1000.0)
    monkeypatch.setattr("bot.time.time", lambda: 1031.0)
    resolution = client.get_market_resolution("btc-updown-5m-1025")
    assert resolution is not None
    assert resolution["resolved_up"] is True
    assert resolution["uma_resolution_status"] == "SIMULATED"


def test_demo_client_order_book_has_depth_on_both_sides():
    client = DemoClient(window_seconds=25, start_ts=1000.0)

    up_book = client.get_order_book("demo-up")
    down_book = client.get_order_book("demo-down")

    assert len(up_book["asks"]) >= 10
    assert len(up_book["bids"]) >= 10
    assert len(down_book["asks"]) >= 10
    assert len(down_book["bids"]) >= 10


def test_sweep_unresolved_trades_once_resolves_stale_trade(mock_client, tmp_path, monkeypatch):
    from bot import build_model_tracks
    monkeypatch.setattr("bot.fetch_binance_agg_trade_price", lambda asset, target_ts_ms: None)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DEMO",
        market_source="local-sim",
        dry_run=True,
    )
    # Use the CONVICTION_ONLY track (model "A")
    track = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    # Replace ledger with a fresh tmp-backed one so prior test runs don't pollute state.
    track.ledger = Ledger(str(tmp_path / "trades_conviction_test.jsonl"))
    track.ledger.record_trade(
        window_end_ts=100,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="order-1",
        dry_run=True,
        market_slug="btc-updown-5m-100",
        feature_snapshot={"move_pct_bin": "<=0.0000", "time_bin": "30-60"},
    )
    track.state.record_trade("UP", 5.0, 0.58, 0.07, "A", "order-1", True, 100)

    class FakeClient:
        def get_market_resolution(self, slug):
            assert slug == "btc-updown-5m-100"
            return {"resolved_up": True, "uma_resolution_status": "RESOLVED"}

    resolved_count = sweep_unresolved_trades_once(track, FakeClient(), now_ts=120)
    assert resolved_count == 1
    assert track.state.unresolved_primary_count() == 0
    assert track.state.wins == 1
    assert track.ledger.get_all_resolutions()[0]["resolved_up"] is True


def test_choose_better_signal_prefers_higher_edge():
    weaker = Signal(
        direction=Direction.UP,
        confidence=0.54,
        theo_prob=0.54,
        market_price=0.50,
        edge=0.04,
        ev=0.2,
        model_used="B",
    )
    stronger = Signal(
        direction=Direction.UP,
        confidence=0.57,
        theo_prob=0.57,
        market_price=0.50,
        edge=0.07,
        ev=0.35,
        model_used="B",
    )
    assert choose_better_signal(weaker, stronger) is stronger


def test_should_fire_snipe_signal_on_confidence_threshold():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.60,
        theo_prob=0.60,
        market_price=0.42,
        edge=0.18,
        ev=0.9,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=8,
        force_trade=False,
        liquidity_score=0.8,
        net_expected_profit_usdc=0.10,
    ) is True


def test_should_fire_snipe_signal_allows_early_entry_for_high_quality_setup():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.57,
        theo_prob=0.57,
        market_price=0.40,
        edge=0.17,
        ev=0.8,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=20,
        force_trade=False,
        liquidity_score=0.85,
        net_expected_profit_usdc=0.08,
    ) is True


def test_should_fire_snipe_signal_rejects_negative_net_profit():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.60,
        theo_prob=0.60,
        market_price=0.42,
        edge=0.18,
        ev=0.9,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=8,
        force_trade=False,
        liquidity_score=0.8,
        net_expected_profit_usdc=0.0,
    ) is False


def test_should_fire_snipe_signal_waits_for_regular_window_without_early_quality():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.60,
        theo_prob=0.60,
        market_price=0.42,
        edge=0.18,
        ev=0.9,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=25,
        force_trade=False,
        liquidity_score=0.50,
        net_expected_profit_usdc=0.05,
    ) is False


def test_should_fire_snipe_signal_allows_early_entry_on_high_quality_setup():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.60,
        theo_prob=0.60,
        market_price=0.42,
        edge=0.18,
        ev=0.9,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=25,
        force_trade=False,
        liquidity_score=0.85,
        net_expected_profit_usdc=0.10,
    ) is True


def test_assess_book_quality_flags_one_sided_book():
    up_book = BookSnapshot(ask_count=0, bid_count=99, best_ask=None, best_bid=0.99)
    down_book = BookSnapshot(ask_count=99, bid_count=0, best_ask=0.01, best_bid=None)

    assessment = assess_book_quality(up_book, down_book)

    assert assessment.trade_ready is False
    assert assessment.reason_code == "ONE_SIDED_BOOK"


def test_assess_book_quality_rejects_low_liquidity_score():
    up_book = BookSnapshot(ask_count=10, bid_count=10, best_ask=0.60, best_bid=0.56)
    down_book = BookSnapshot(ask_count=10, bid_count=10, best_ask=0.40, best_bid=0.36)

    assessment = assess_book_quality(up_book, down_book)

    assert assessment.trade_ready is False
    assert assessment.reason_code == "LOW_LIQUIDITY_SCORE"


def test_assess_book_quality_relaxes_for_strong_conviction_with_tight_spread():
    up_book = BookSnapshot(ask_count=30, bid_count=30, best_ask=0.62, best_bid=0.60)
    down_book = BookSnapshot(ask_count=30, bid_count=1, best_ask=0.38, best_bid=0.37)

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.90,
    )

    assert assessment.trade_ready is True
    assert assessment.reason_code == "READY_RELAXED"


def test_assess_book_quality_does_not_relax_when_conviction_is_weak():
    up_book = BookSnapshot(ask_count=30, bid_count=30, best_ask=0.62, best_bid=0.60)
    down_book = BookSnapshot(ask_count=30, bid_count=1, best_ask=0.38, best_bid=0.37)

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.70,
    )

    assert assessment.trade_ready is False
    assert assessment.reason_code == "THIN_BIDS"


def test_assess_book_quality_relaxes_thin_asks_with_full_bet_depth():
    up_book = BookSnapshot(
        ask_count=8, bid_count=30, best_ask=0.62, best_bid=0.60,
        best_ask_size=2.0, best_bid_size=2.0, total_ask_size=20.0, total_bid_size=20.0,
    )
    down_book = BookSnapshot(
        ask_count=30, bid_count=30, best_ask=0.38, best_bid=0.37,
        best_ask_size=2.0, best_bid_size=2.0, total_ask_size=20.0, total_bid_size=20.0,
    )

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.90,
    )

    assert assessment.trade_ready is True
    assert assessment.reason_code == "READY_RELAXED"


def test_assess_book_quality_holds_thin_asks_without_depth():
    up_book = BookSnapshot(
        ask_count=8, bid_count=30, best_ask=0.62, best_bid=0.60,
        best_ask_size=0.1, best_bid_size=0.1, total_ask_size=0.5, total_bid_size=20.0,
    )
    down_book = BookSnapshot(
        ask_count=30, bid_count=30, best_ask=0.38, best_bid=0.37,
        best_ask_size=2.0, best_bid_size=2.0, total_ask_size=20.0, total_bid_size=20.0,
    )

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.90,
    )

    assert assessment.trade_ready is False
    assert assessment.reason_code == "THIN_ASKS"


def test_assess_book_quality_relaxes_one_sided_with_weak_bids_and_depth():
    up_book = BookSnapshot(
        ask_count=9, bid_count=30, best_ask=0.62, best_bid=0.60,
        best_ask_size=2.0, best_bid_size=2.0, total_ask_size=20.0, total_bid_size=20.0,
    )
    down_book = BookSnapshot(
        ask_count=0, bid_count=12, best_ask=None, best_bid=0.37,
        best_ask_size=0.0, best_bid_size=2.0, total_ask_size=0.0, total_bid_size=20.0,
    )

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.90,
    )

    assert assessment.trade_ready is True
    assert assessment.reason_code == "READY_RELAXED"


def test_assess_book_quality_holds_one_sided_without_weak_bids():
    up_book = BookSnapshot(
        ask_count=9, bid_count=30, best_ask=0.62, best_bid=0.60,
        best_ask_size=2.0, best_bid_size=2.0, total_ask_size=20.0, total_bid_size=20.0,
    )
    down_book = BookSnapshot(ask_count=0, bid_count=0, best_ask=None, best_bid=None)

    assessment = assess_book_quality(
        up_book,
        down_book,
        strongest_side="UP",
        market_conviction=0.90,
    )

    assert assessment.trade_ready is False
    assert assessment.reason_code == "ONE_SIDED_BOOK"


def test_should_fire_snipe_signal_respects_scaled_min_profit_floor():
    signal = Signal(
        direction=Direction.UP,
        confidence=0.60,
        theo_prob=0.60,
        market_price=0.42,
        edge=0.18,
        ev=0.9,
        model_used="B",
    )
    assert should_fire_snipe_signal(
        signal,
        best_signal=signal,
        previous_signal=None,
        seconds_remaining=8,
        force_trade=False,
        liquidity_score=0.80,
        net_expected_profit_usdc=0.04,
        min_net_profit_required=0.05,
    ) is False


def test_should_enter_on_waiting_conviction_when_polymarket_side_is_above_threshold():
    now = time.time()
    state = BotState(starting_bankroll_usdc=10.0)
    for index, price in enumerate([100.0, 100.02, 100.03, 100.04], start=1):
        state.update_price(price, updated_ts=now - (4 - index), source="BINANCE")
    # Second fresh feed so the price-feed quorum (MIN_HEALTHY_PRICE_FEEDS) is met;
    # otherwise is_price_stale() short-circuits to STALE_BTC_PRICE before the
    # early-conviction liquidity check this test is exercising.
    state.update_price(100.04, updated_ts=now, source="KRAKEN")
    window = WindowState(
        start_ts=0,
        end_ts=300,
        market_id="m1",
        up_token_id="u1",
        down_token_id="d1",
        open_price=100.0,
        market_url="https://polymarket.com/event/test",
    )
    up_book = BookSnapshot(ask_count=40, bid_count=25, best_ask=0.22, best_bid=0.20)
    down_book = BookSnapshot(ask_count=40, bid_count=25, best_ask=0.68, best_bid=0.66)
    state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.34,
        down_ask=0.57,
        up_bid=0.32,
        down_bid=0.55,
        up_ask_count=35,
        down_ask_count=35,
        up_bid_count=25,
        down_bid_count=25,
        updated_ts=now - 2.0,
    )
    state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.28,
        down_ask=0.63,
        up_bid=0.26,
        down_bid=0.61,
        up_ask_count=35,
        down_ask_count=35,
        up_bid_count=25,
        down_bid_count=25,
        updated_ts=now - 1.0,
    )

    triggered, reason = should_enter_on_waiting_conviction(
        up_book=up_book,
        down_book=down_book,
        state=state,
        window=window,
    )

    # EARLY_CONVICTION_MIN_SCORE=1.0 effectively disables early conviction;
    # realistic liquidity scores never reach 1.0 so the function now returns False.
    assert triggered is False
    assert "EARLY_CONVICTION_LOW_LIQUIDITY" in reason


def test_refresh_window_books_returns_none_on_fetch_error():
    class ExplodingOrderer:
        def get_book_snapshot(self, token_id):
            raise RuntimeError(f"boom:{token_id}")

    window = WindowState(
        start_ts=0,
        end_ts=300,
        market_id="m1",
        up_token_id="u1",
        down_token_id="d1",
        open_price=100.0,
        market_url="https://polymarket.com/event/test",
    )

    up_book, down_book = asyncio.run(refresh_window_books(ExplodingOrderer(), window))

    assert up_book is None
    assert down_book is None


def test_estimate_trade_fee_usdc_uses_crypto_fallback():
    fee = estimate_trade_fee_usdc(asset="BTC", market_price=0.40, bet_size=10.0)
    assert fee == pytest.approx(0.1728)


def test_build_model_tracks_returns_three_tracks(mock_client):
    from bot import build_model_tracks
    from state.track import ModelTrack
    from config import ACTIVE_MODEL_TRACK, SHADOW_MULTI_TRACK_EXECUTION

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    assert len(tracks) == 3
    names = {t.name for t in tracks}
    assert names == {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}

    # Each track has independent state
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    mispricing = next(t for t in tracks if t.name == "MISPRICING_ONLY")
    assert conviction.state is not mispricing.state
    assert conviction.ledger is not mispricing.ledger
    assert conviction.guard is not mispricing.guard

    # Regime filters — all tracks run late-conviction with different engines
    assert conviction.regime_filter == {"LATE_CONVICTION"}
    assert mispricing.regime_filter == {"LATE_CONVICTION"}
    assert next(t for t in tracks if t.name == "BOTH").regime_filter == {"LATE_CONVICTION"}

    # In dry_run mode, all orderers are dry_run
    for t in tracks:
        assert t.orderer.dry_run is True

    # Shadow mode can optionally activate all tracks for parallel evaluation.
    both = next(t for t in tracks if t.name == "BOTH")
    if SHADOW_MULTI_TRACK_EXECUTION:
        assert all(t.active for t in tracks)
    elif ACTIVE_MODEL_TRACK == "BOTH":
        assert both.active is True
        assert conviction.active is False


def test_build_model_tracks_live_mode_only_active_track_is_live(mock_client, monkeypatch):
    """When dry_run=False, only the active track gets dry_run=False; others stay dry_run=True."""
    import bot
    from bot import build_model_tracks

    monkeypatch.setattr(bot, "ACTIVE_MODEL_TRACK", "BOTH")

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )

    both = next(t for t in tracks if t.name == "BOTH")
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    mispricing = next(t for t in tracks if t.name == "MISPRICING_ONLY")

    # Active track (BOTH) should have a live orderer
    assert both.orderer.dry_run is False
    assert both.active is True

    # Inactive tracks must stay shadow regardless
    assert conviction.orderer.dry_run is True
    assert conviction.active is False
    assert mispricing.orderer.dry_run is True
    assert mispricing.active is False


def test_resolve_with_binance_uses_window_start_and_end_prices(monkeypatch):
    prices = {
        1775454300 * 1000 - 300_000: 69207.048,
        1775454300 * 1000: 69133.12094,
    }

    def fake_fetch(asset, target_ts_ms):
        assert asset == "BTC"
        return prices[target_ts_ms]

    monkeypatch.setattr("bot.fetch_binance_agg_trade_price", fake_fetch)
    resolution = resolve_with_binance("BTC", 1775454300)
    assert resolution is not None
    assert resolution["resolved_up"] is False
    assert resolution["price_to_beat"] == 69207.048
    assert resolution["final_price"] == 69133.12094


def test_compute_all_engine_scores_returns_dict_for_a_b_c(mock_client):
    from bot import compute_all_engine_scores
    from state.session import BotState
    from state.window import WindowState
    import time

    state = BotState(btc_price=83000.0)
    end_ts = ((int(time.time()) // 300) + 1) * 300
    window = WindowState(
        start_ts=end_ts - 300,
        end_ts=end_ts,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="dn-1",
        open_price=82500.0,
    )
    scores = compute_all_engine_scores(state, window)
    assert set(scores.keys()) == {"A", "B", "C", "P"}
    for v in scores.values():
        assert 0.0 <= v <= 1.0


def test_evaluate_track_skips_wrong_regime(mock_client):
    """CONVICTION_ONLY track must skip MISPRICING signals."""
    from bot import build_model_tracks, _evaluate_track
    from unittest.mock import MagicMock
    import asyncio

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    # Create a mock selection whose signal has regime=MISPRICING
    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "MISPRICING"
    signal.confidence = 0.7

    selection = MagicMock()
    selection.signal = signal
    selection.up_price = 0.45
    selection.down_price = 0.55

    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}
    window = MagicMock()
    window.seconds_remaining.return_value = 120
    window.has_open_positions.return_value = False  # no exits needed

    conviction.guard.can_enter = MagicMock(return_value=(True, ""))
    conviction.orderer = MagicMock()
    asyncio.run(
        _evaluate_track(conviction, selection, engine_scores, window=window, asset="BTC")
    )
    # orderer.place_bet should NOT have been called
    conviction.orderer.place_bet.assert_not_called()


def test_sweep_unresolved_uses_track_ledger(mock_client):
    from bot import build_model_tracks, sweep_unresolved_trades_once
    from unittest.mock import MagicMock

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    # Ledger returns no unresolved trades
    conviction.ledger.get_unresolved_trades = MagicMock(return_value=[])
    count = sweep_unresolved_trades_once(conviction, mock_client)
    assert count == 0
    conviction.ledger.get_unresolved_trades.assert_called_once()


def test_build_runtime_status_multi_track_schema(mock_client):
    from bot import build_model_tracks, build_runtime_status
    from state.session import BotState

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    shared_state = BotState(btc_price=83420.0)
    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}

    payload = build_runtime_status(tracks, shared_state, engine_scores)

    assert "btc_price" in payload
    assert "engine_scores" in payload
    assert payload["engine_scores"] == {"A": 0.62, "B": 0.68, "C": 0.71}
    assert "tracks" in payload
    assert len(payload["tracks"]) == 3

    track_names = {t["name"] for t in payload["tracks"]}
    assert track_names == {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}

    for t in payload["tracks"]:
        assert "css_color" in t
        assert "status" in t
        assert "win_stats" in t
        assert "pnl_history" in t
        assert t["pnl_history"] == [0.0]
        assert "all_trades" in t


def test_build_runtime_status_open_position_is_non_null(mock_client):
    """open_position payload must be populated when the track's current window has an open position."""
    from bot import build_model_tracks, build_runtime_status
    from state.position import Position, PositionStatus
    import time as _time

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    shared_state = BotState(btc_price=83420.0)
    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}

    # Set up an open position on one track's current_window
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    end_ts = int(_time.time()) + 120
    window = WindowState(
        start_ts=end_ts - 300,
        end_ts=end_ts,
        market_id="cond-test",
        up_token_id="up-test",
        down_token_id="dn-test",
        open_price=83000.0,
    )
    pos = Position(
        order_id="order-open-1",
        window_end_ts=end_ts,
        direction="UP",
        size_usdc=5.0,
        entry_price=0.45,
        entry_ts=_time.time(),
    )
    window.add_position(pos)
    conviction.state.current_window = window

    payload = build_runtime_status(tracks, shared_state, engine_scores)

    conviction_payload = next(t for t in payload["tracks"] if t["name"] == "CONVICTION_ONLY")
    assert conviction_payload["open_position"] is not None
    assert conviction_payload["open_position"]["direction"] == "UP"
    assert conviction_payload["open_position"]["entry_price"] == pytest.approx(0.45)


def test_build_runtime_status_includes_trade_entry_regime(mock_client):
    from bot import build_model_tracks, build_runtime_status

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    conviction.state.record_trade(
        "UP",
        5.0,
        0.58,
        0.07,
        "A",
        "order-1",
        True,
        100,
        entry_regime="LATE_CONVICTION",
    )

    payload = build_runtime_status(tracks, BotState(btc_price=83420.0), {"A": 0.62})
    conviction_payload = next(t for t in payload["tracks"] if t["name"] == "CONVICTION_ONLY")

    assert conviction_payload["all_trades"][0]["regime"] == "LATE_CONVICTION"


def test_build_runtime_status_exposes_freshness(mock_client):
    from bot import build_model_tracks, build_runtime_status

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    shared_state = BotState(
        btc_price=83420.0,
        btc_price_updated_ts=time.time() - 9,
        price_source="BINANCE",
    )
    shared_state.update_price(83410.0, updated_ts=time.time() - 2, source="KRAKEN")
    shared_state.update_price(83420.0, updated_ts=time.time() - 2, source="COINBASE")
    shared_state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.61,
        down_ask=0.39,
        up_bid=0.60,
        down_bid=0.38,
        updated_ts=time.time() - 2,
    )

    payload = build_runtime_status(tracks, shared_state, {"A": 0.62})

    assert payload["freshness"]["btc_price_stale"] is False
    assert payload["freshness"]["btc_price_source"] == "COINBASE"
    assert payload["freshness"]["btc_healthy_source_count"] == 2
    assert payload["freshness"]["btc_price_quorum_met"] is True
    assert payload["freshness"]["polymarket_book_stale"] is False


def test_build_runtime_status_marks_book_idle_after_window_close(mock_client):
    from bot import build_model_tracks, build_runtime_status

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    shared_state = BotState(
        btc_price=83420.0,
        btc_price_updated_ts=time.time() - 1,
        price_source="MEDIAN[COINBASE,GEMINI,KRAKEN]",
    )
    shared_state.current_window = WindowState(
        start_ts=int(time.time()) - 300,
        end_ts=int(time.time()) - 1,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=83000.0,
    )
    shared_state.update_polymarket_book(
        window_end_ts=shared_state.current_window.end_ts,
        up_ask=0.61,
        down_ask=0.39,
        up_bid=0.60,
        down_bid=0.38,
        updated_ts=time.time() - 30,
    )

    payload = build_runtime_status(tracks, shared_state, {"A": 0.62})

    assert payload["freshness"]["polymarket_book_idle"] is True
    assert payload["freshness"]["polymarket_book_stale"] is False
    assert payload["freshness"]["polymarket_book_age_seconds"] is None


def test_evaluate_track_enters_matching_regime(mock_client):
    """CONVICTION_ONLY track must enter on LATE_CONVICTION signal when guard allows."""
    from bot import build_model_tracks, _evaluate_track
    from unittest.mock import MagicMock, patch
    import asyncio

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "LATE_CONVICTION"
    signal.confidence = 0.72
    signal.theo_prob = 0.72
    signal.edge = 0.05
    signal.market_price = 0.42

    selection = MagicMock()
    selection.signal = signal
    selection.up_price = 0.42
    selection.down_price = 0.58
    selection.liquidity_score = 0.8
    selection.seconds_remaining = 45
    selection.net_expected_profit_usdc = None

    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}
    window = MagicMock()
    window.seconds_remaining.return_value = 45
    window.has_open_positions.return_value = False

    conviction.guard.can_enter = MagicMock(return_value=(True, ""))

    from unittest.mock import AsyncMock
    with patch("bot.submit_trade", new_callable=AsyncMock) as mock_submit:
        mock_submit.return_value = None
        asyncio.run(
            _evaluate_track(conviction, selection, engine_scores, window=window, asset="BTC")
        )
    mock_submit.assert_called_once()
    assert conviction.stats.last_signal_theo == pytest.approx(0.72)


def test_evaluate_track_conviction_reentry_needs_stronger_confirmation(mock_client):
    from bot import build_model_tracks, _evaluate_track
    from unittest.mock import AsyncMock, MagicMock, patch
    import asyncio

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    conviction.state.record_trade("UP", 5.0, 0.84, 0.07, "A", "order-1", True, 300)

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "LATE_CONVICTION"
    signal.confidence = 0.93
    signal.edge = 0.07
    signal.market_price = 0.84
    signal.btc_confirmed = True

    selection = MagicMock()
    selection.signal = signal
    selection.up_price = 0.84
    selection.down_price = 0.16
    selection.liquidity_score = 0.8
    selection.seconds_remaining = 45
    selection.net_expected_profit_usdc = None

    engine_scores = {"A": 0.96, "B": 0.82, "C": 0.88}
    window = MagicMock()
    window.end_ts = 300
    window.seconds_remaining.return_value = 45
    window.has_open_positions.return_value = False

    conviction.guard.can_enter = MagicMock(return_value=(True, ""))

    with patch("bot.submit_trade", new_callable=AsyncMock) as mock_submit:
        asyncio.run(
            _evaluate_track(conviction, selection, engine_scores, window=window, asset="BTC")
        )

    mock_submit.assert_not_called()
    assert conviction.stats.last_no_trade_reason == "conviction re-entry needs stronger confirmation"


def test_select_snipe_signals_returns_independent_track_candidates(mock_client, monkeypatch, tmp_path):
    import bot
    from bot import build_model_tracks, select_snipe_signals

    monkeypatch.setattr(bot, "SHADOW_MULTI_TRACK_EXECUTION", True)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    for track in tracks:
        track.ledger = Ledger(str(tmp_path / f"{track.name.lower()}.jsonl"))

    shared_state = BotState(
        btc_price=82100.0,
        btc_price_updated_ts=time.time(),
    )
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    def fake_eval(*args, model=None, **kwargs):
        del args, kwargs
        if model == "A":
            signal = Signal(
                direction=Direction.UP,
                confidence=0.71,
                theo_prob=0.71,
                market_price=0.59,
                edge=0.04,
                ev=0.18,
                model_used="A",
                entry_regime="LATE_CONVICTION",
                strongest_side="UP",
                market_conviction=0.8,
                btc_confirmed=True,
            )
            return SignalEvaluation(
                signal=signal,
                strongest_side="UP",
                regime_candidate="LATE_CONVICTION",
                reason_code="READY",
                reason_detail="conviction ready",
                market_conviction=0.8,
                btc_confirmed=True,
            )
        if model == "B":
            signal = Signal(
                direction=Direction.DOWN,
                confidence=0.68,
                theo_prob=0.32,
                market_price=0.41,
                edge=0.05,
                ev=0.21,
                model_used="B",
                entry_regime="LATE_CONVICTION",
                strongest_side="DOWN",
                market_conviction=0.6,
                btc_confirmed=True,
            )
            return SignalEvaluation(
                signal=signal,
                strongest_side="DOWN",
                regime_candidate="LATE_CONVICTION",
                reason_code="READY",
                reason_detail="conviction ready",
                market_conviction=0.6,
                btc_confirmed=True,
            )
        signal = Signal(
            direction=Direction.UP,
            confidence=0.66,
            theo_prob=0.66,
            market_price=0.57,
            edge=0.03,
            ev=0.16,
            model_used="C",
            entry_regime="LATE_CONVICTION",
            strongest_side="UP",
            market_conviction=0.7,
            btc_confirmed=True,
        )
        return SignalEvaluation(
            signal=signal,
            strongest_side="UP",
            regime_candidate="LATE_CONVICTION",
            reason_code="READY",
            reason_detail="conviction ready",
            market_conviction=0.7,
            btc_confirmed=True,
        )

    monkeypatch.setattr(bot, "evaluate_with_diagnostics", fake_eval)
    monkeypatch.setattr(
        bot,
        "assess_book_quality",
        lambda *args, **kwargs: BookAssessment(0.9, 0.04, "BOOK_OK", "book ready", True),
    )
    monkeypatch.setattr(
        bot,
        "evaluate_trade_economics",
        lambda **kwargs: (5.0, 0.3, 0.2, 0.05, 0.0, 0.1),
    )
    monkeypatch.setattr(
        bot,
        "should_fire_snipe_signal",
        lambda candidate_signal, **kwargs: candidate_signal is not None,
    )

    selections = asyncio.run(
        select_snipe_signals(
            window=window,
            state=shared_state,
            orderer=tracks[0].orderer,
            tracks=tracks,
            asset="BTC",
            force_trade=False,
            live_mode=False,
        )
    )

    assert selections["CONVICTION_ONLY"].signal is not None
    assert selections["CONVICTION_ONLY"].signal.model_used == "A"
    assert selections["CONVICTION_ONLY"].signal.entry_regime == "LATE_CONVICTION"
    assert selections["MISPRICING_ONLY"].signal is not None
    assert selections["MISPRICING_ONLY"].signal.model_used == "B"
    assert selections["MISPRICING_ONLY"].signal.direction == Direction.DOWN
    assert selections["BOTH"].signal is not None
    assert selections["BOTH"].signal.model_used == "C"
    assert selections["BOTH"].signal.direction == Direction.UP


def test_select_snipe_signals_logs_skip_reason_for_rejected_track(mock_client, monkeypatch, tmp_path):
    import bot
    from bot import build_model_tracks, select_snipe_signals

    monkeypatch.setattr(bot, "SHADOW_MULTI_TRACK_EXECUTION", True)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    for track in tracks:
        track.ledger = Ledger(str(tmp_path / f"{track.name.lower()}_candidates.jsonl"))

    shared_state = BotState(
        btc_price=82100.0,
        btc_price_updated_ts=time.time(),
    )
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    def fake_eval(*args, model=None, **kwargs):
        del args, kwargs
        if model == "B":
            return SignalEvaluation(
                signal=None,
                strongest_side="DOWN",
                regime_candidate="LATE_CONVICTION",
                reason_code="NET_PROFIT_TOO_LOW",
                reason_detail="cost floor blocked trade",
                market_conviction=0.5,
                btc_confirmed=True,
            )
        signal = Signal(
            direction=Direction.UP,
            confidence=0.70,
            theo_prob=0.70,
            market_price=0.58,
            edge=0.04,
            ev=0.18,
            model_used=model or "A",
            entry_regime="LATE_CONVICTION",
            strongest_side="UP",
            market_conviction=0.7,
            btc_confirmed=True,
        )
        return SignalEvaluation(
            signal=signal,
            strongest_side="UP",
            regime_candidate=signal.entry_regime,
            reason_code="READY",
            reason_detail="ready",
            market_conviction=0.7,
            btc_confirmed=True,
        )

    monkeypatch.setattr(bot, "evaluate_with_diagnostics", fake_eval)
    monkeypatch.setattr(
        bot,
        "assess_book_quality",
        lambda *args, **kwargs: BookAssessment(0.9, 0.04, "BOOK_OK", "book ready", True),
    )
    monkeypatch.setattr(
        bot,
        "evaluate_trade_economics",
        lambda **kwargs: (5.0, 0.3, 0.2, 0.05, 0.0, 0.1),
    )
    monkeypatch.setattr(
        bot,
        "should_fire_snipe_signal",
        lambda candidate_signal, **kwargs: candidate_signal is not None,
    )

    asyncio.run(
        select_snipe_signals(
            window=window,
            state=shared_state,
            orderer=tracks[0].orderer,
            tracks=tracks,
            asset="BTC",
            force_trade=False,
            live_mode=False,
        )
    )

    mispricing_track = next(track for track in tracks if track.name == "MISPRICING_ONLY")
    candidates = mispricing_track.ledger.get_all_candidates()
    assert candidates[-1]["reason_code"] == "NET_PROFIT_TOO_LOW"
    assert candidates[-1]["skip_reason"] == "NET_PROFIT_TOO_LOW"
    assert candidates[-1]["would_trade"] is False


def test_select_snipe_signals_marks_regime_filtered_candidate_as_not_tradeable(mock_client, monkeypatch, tmp_path):
    import bot
    from bot import build_model_tracks, select_snipe_signals

    monkeypatch.setattr(bot, "SHADOW_MULTI_TRACK_EXECUTION", True)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    for track in tracks:
        track.ledger = Ledger(str(tmp_path / f"{track.name.lower()}_filtered.jsonl"))

    shared_state = BotState(
        btc_price=82100.0,
        btc_price_updated_ts=time.time(),
    )
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    def fake_eval(*args, model=None, **kwargs):
        del args, kwargs
        signal = Signal(
            direction=Direction.DOWN,
            confidence=0.72,
            theo_prob=0.28,
            market_price=0.79,
            edge=0.05,
            ev=0.18,
            model_used=model or "B",
            entry_regime="MISPRICING",
            strongest_side="DOWN",
            market_conviction=0.8,
            btc_confirmed=True,
        )
        return SignalEvaluation(
            signal=signal,
            strongest_side="DOWN",
            regime_candidate="MISPRICING",
            reason_code="READY",
            reason_detail="ready",
            market_conviction=0.8,
            btc_confirmed=True,
        )

    monkeypatch.setattr(bot, "evaluate_with_diagnostics", fake_eval)
    monkeypatch.setattr(
        bot,
        "assess_book_quality",
        lambda *args, **kwargs: BookAssessment(0.9, 0.04, "BOOK_OK", "book ready", True),
    )
    monkeypatch.setattr(
        bot,
        "evaluate_trade_economics",
        lambda **kwargs: (5.0, 0.3, 0.2, 0.05, 0.0, 0.1),
    )
    monkeypatch.setattr(
        bot,
        "should_fire_snipe_signal",
        lambda candidate_signal, **kwargs: candidate_signal is not None,
    )

    asyncio.run(
        select_snipe_signals(
            window=window,
            state=shared_state,
            orderer=tracks[0].orderer,
            tracks=tracks,
            asset="BTC",
            force_trade=False,
            live_mode=False,
        )
    )

    mispricing_track = next(track for track in tracks if track.name == "MISPRICING_ONLY")
    candidates = mispricing_track.ledger.get_all_candidates()
    assert candidates[-1]["reason_code"] == "READY"
    assert candidates[-1]["skip_reason"] == "regime=MISPRICING blocked by filter"
    assert candidates[-1]["would_trade"] is False


def test_select_snipe_signals_blocks_stale_btc_price(mock_client, monkeypatch, tmp_path):
    import bot
    from bot import build_model_tracks, select_snipe_signals

    monkeypatch.setattr(bot, "SHADOW_MULTI_TRACK_EXECUTION", True)
    monkeypatch.setattr(bot, "ALLOW_TRADING_WITHOUT_BTC_PRICE", False)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    for track in tracks:
        track.ledger = Ledger(str(tmp_path / f"{track.name.lower()}_stale.jsonl"))

    shared_state = BotState(
        btc_price=82100.0,
        btc_price_updated_ts=time.time() - 30,
        price_source="BINANCE",
    )
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    monkeypatch.setattr(
        bot,
        "assess_book_quality",
        lambda *args, **kwargs: BookAssessment(0.9, 0.04, "BOOK_OK", "book ready", True),
    )

    selections = asyncio.run(
        select_snipe_signals(
            window=window,
            state=shared_state,
            orderer=tracks[0].orderer,
            tracks=tracks,
            asset="BTC",
            force_trade=False,
            live_mode=False,
        )
    )

    assert selections["CONVICTION_ONLY"].signal is None
    conviction_candidates = next(t for t in tracks if t.name == "CONVICTION_ONLY").ledger.get_all_candidates()
    assert conviction_candidates[-1]["reason_code"] == "STALE_BTC_PRICE"
    assert conviction_candidates[-1]["skip_reason"] == "STALE_BTC_PRICE"


def test_select_snipe_signals_marks_stale_polymarket_book(mock_client, monkeypatch, tmp_path):
    import bot
    from bot import build_model_tracks, select_snipe_signals

    monkeypatch.setattr(bot, "SHADOW_MULTI_TRACK_EXECUTION", True)
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    for track in tracks:
        track.ledger = Ledger(str(tmp_path / f"{track.name.lower()}_book_stale.jsonl"))

    shared_state = BotState(
        btc_price=82100.0,
        btc_price_updated_ts=time.time(),
        price_source="BINANCE",
    )
    shared_state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.60,
        down_ask=0.40,
        up_bid=0.58,
        down_bid=0.38,
        updated_ts=time.time() - 30,
    )
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    monkeypatch.setattr(tracks[0].orderer, "get_book_snapshot", lambda token_id: None)

    async def stop_sleep(_seconds):
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(bot.asyncio, "sleep", stop_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        asyncio.run(
            select_snipe_signals(
                window=window,
                state=shared_state,
                orderer=tracks[0].orderer,
                tracks=tracks,
                asset="BTC",
                force_trade=False,
                live_mode=False,
            )
        )

    conviction_track = next(track for track in tracks if track.name == "CONVICTION_ONLY")
    candidates = conviction_track.ledger.get_all_candidates()
    assert candidates[-1]["reason_code"] == "STALE_POLYMARKET_BOOK"
    assert candidates[-1]["skip_reason"] == "STALE_POLYMARKET_BOOK"
    assert conviction_track.stats.last_no_trade_reason == "STALE_POLYMARKET_BOOK"


def test_submit_trade_cancelled_entry_does_not_open_position(mock_client, tmp_path, monkeypatch):
    from bot import build_model_tracks, submit_trade
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.ledger = Ledger(str(tmp_path / "cancelled_entry.jsonl"))
    track.state.current_window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    track.orderer.wait_for_fill = AsyncMock(
        return_value=OrderResult(order_id="live-order-1", status=OrderStatus.CANCELLED)
    )

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.market_price = 0.58
    signal.edge = 0.05
    signal.ev = 0.2
    signal.model_used = "C"
    signal.entry_regime = "MISPRICING"

    result = asyncio.run(
        submit_trade(
            track,
            window=track.state.current_window,
            signal=signal,
            amount_usdc=5.0,
            role="PRIMARY",
            asset="BTC",
        )
    )

    assert result is None
    assert track.state.trade_history == []
    assert track.state.pending_orders == []
    assert track.state.current_window.get_open_positions() == []
    assert len(track.ledger.get_all_submissions()) == 1
    assert len(track.ledger.get_all_cancellations()) == 1


def test_submit_trade_partial_fill_creates_partial_exposure(mock_client, tmp_path, monkeypatch):
    from bot import build_model_tracks, submit_trade
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.ledger = Ledger(str(tmp_path / "partial_entry.jsonl"))
    track.state.current_window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    track.orderer.wait_for_fill = AsyncMock(
        return_value=OrderResult(
            order_id="live-order-2",
            status=OrderStatus.PARTIAL,
            filled_size=4.0,
            avg_price=0.60,
            submitted_price=0.58,
        )
    )

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.market_price = 0.58
    signal.edge = 0.05
    signal.ev = 0.2
    signal.model_used = "C"
    signal.entry_regime = "MISPRICING"

    result = asyncio.run(
        submit_trade(
            track,
            window=track.state.current_window,
            signal=signal,
            amount_usdc=5.0,
            role="PRIMARY",
            asset="BTC",
        )
    )

    assert result is not None
    assert result.amount_usdc == pytest.approx(2.4)
    assert len(track.state.trade_history) == 1
    assert track.state.trade_history[0].amount_usdc == pytest.approx(2.4)
    positions = track.state.current_window.get_open_positions()
    assert len(positions) == 1
    assert positions[0].size_usdc == pytest.approx(2.4)
    fills = track.ledger.get_all_fills()
    assert fills[-1]["status"] == "PARTIAL"


def test_check_position_exits_exit_failure_keeps_position_open(mock_client, tmp_path, monkeypatch):
    from bot import build_model_tracks, check_position_exits
    from unittest.mock import MagicMock

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.ledger = Ledger(str(tmp_path / "failed_exit.jsonl"))
    window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    position = Position(
        order_id="entry-1",
        window_end_ts=window.end_ts,
        direction="UP",
        size_usdc=5.0,
        entry_price=0.45,
        entry_ts=time.time() - 10,
        take_profit_price=0.50,
    )
    window.add_position(position)
    track.orderer.place_bet = MagicMock(side_effect=RuntimeError("sell failed"))
    track.orderer.cancel = MagicMock(return_value=True)

    closed = asyncio.run(
        check_position_exits(
            track,
            asset="BTC",
            window=window,
            up_price=0.51,
            down_price=0.49,
        )
    )

    assert closed == []
    assert position.status == PositionStatus.OPEN
    assert window.get_open_positions() == [position]


def test_track_entry_skip_reason_emits_guard_breach_alert(mock_client, monkeypatch):
    import bot
    from bot import SnipeSelection, build_model_tracks, _track_entry_skip_reason

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    track = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    window = WindowState(
        start_ts=int(time.time()) - 120,
        end_ts=int(time.time()) + 60,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "LATE_CONVICTION"
    signal.market_price = 0.81
    selection = SnipeSelection(
        signal=signal,
        up_price=0.81,
        down_price=0.19,
        volatility=0.0,
        seconds_remaining=90,
        model_context=None,
    )
    monkeypatch.setattr(track.guard, "can_enter", MagicMock(return_value=(False, "Max open orders reached: 3")))

    with patch.object(bot, "emit_alert_throttled") as mock_alert:
        reason = _track_entry_skip_reason(track, selection, window=window)

    assert reason == "Max open orders reached: 3"
    mock_alert.assert_called_once()
    assert mock_alert.call_args.args[:2] == ("WARNING", "Max open orders reached")


def test_submit_trade_auth_failure_emits_critical_alert(mock_client, monkeypatch):
    import bot
    from bot import build_model_tracks, submit_trade

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    bot._ORDER_FAILURE_TS.clear()
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.state.current_window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    track.orderer.place_bet = MagicMock(side_effect=RuntimeError("401 unauthorized"))

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.market_price = 0.58
    signal.edge = 0.05
    signal.ev = 0.2
    signal.model_used = "C"
    signal.entry_regime = "MISPRICING"

    with patch.object(bot, "emit_alert_throttled") as mock_alert:
        result = asyncio.run(
            submit_trade(
                track,
                window=track.state.current_window,
                signal=signal,
                amount_usdc=5.0,
                role="PRIMARY",
                asset="BTC",
            )
        )

    assert result is None
    mock_alert.assert_called_once()
    assert mock_alert.call_args.args[:2] == ("CRITICAL", "Order auth failure")


def test_submit_trade_repeated_failures_emit_error_alert(mock_client, monkeypatch):
    import bot
    from bot import build_model_tracks, submit_trade

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    monkeypatch.setattr(bot, "ALERT_ORDER_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(bot, "ALERT_ORDER_FAILURE_WINDOW_SECONDS", 60.0)
    bot._ORDER_FAILURE_TS.clear()
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.state.current_window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    track.orderer.place_bet = MagicMock(
        side_effect=RuntimeError("Best ask 0.8700 exceeds slippage cap 0.8300")
    )

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.market_price = 0.58
    signal.edge = 0.05
    signal.ev = 0.2
    signal.model_used = "C"
    signal.entry_regime = "MISPRICING"

    with patch.object(bot, "emit_alert_throttled") as mock_alert:
        for _ in range(2):
            result = asyncio.run(
                submit_trade(
                    track,
                    window=track.state.current_window,
                    signal=signal,
                    amount_usdc=5.0,
                    role="PRIMARY",
                    asset="BTC",
                )
            )
            assert result is None

    assert mock_alert.call_count == 1
    assert mock_alert.call_args.args[:2] == ("ERROR", "Repeated order failures")


def test_compute_client_order_id_deterministic_and_distinct():
    from bot import compute_client_order_id

    base = dict(
        track="BOTH",
        window_end_ts=300,
        role="PRIMARY",
        token_id="up-1",
        price=0.58,
        amount_usdc=5.0,
    )
    assert compute_client_order_id(**base) == compute_client_order_id(**base)
    # A different token (side) is a distinct logical order.
    assert compute_client_order_id(**{**base, "token_id": "down-1"}) != compute_client_order_id(**base)
    # A different size is a distinct logical order.
    assert compute_client_order_id(**{**base, "amount_usdc": 4.0}) != compute_client_order_id(**base)


def test_submit_trade_ambiguous_failure_blocks_duplicate_resubmit(mock_client, tmp_path, monkeypatch):
    import bot
    from bot import build_model_tracks, compute_client_order_id, submit_trade
    from market.orderer import AmbiguousOrderError

    monkeypatch.setattr("bot.ACTIVE_MODEL_TRACK", "BOTH")
    bot._ORDER_FAILURE_TS.clear()
    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="LIVE",
        market_source="gamma",
        dry_run=False,
    )
    track = next(t for t in tracks if t.name == "BOTH")
    track.ledger = Ledger(str(tmp_path / "ambiguous.jsonl"))
    window = WindowState(
        start_ts=0,
        end_ts=int(time.time()) + 120,
        market_id="cond-1",
        up_token_id="up-1",
        down_token_id="down-1",
        open_price=82000.0,
    )
    track.state.current_window = window
    place_bet = MagicMock(side_effect=AmbiguousOrderError("read timeout after POST"))
    track.orderer.place_bet = place_bet

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.market_price = 0.58
    signal.edge = 0.05
    signal.ev = 0.2
    signal.model_used = "C"
    signal.entry_regime = "MISPRICING"

    kwargs = dict(window=window, signal=signal, amount_usdc=5.0, role="PRIMARY", asset="BTC")
    r1 = asyncio.run(submit_trade(track, **kwargs))
    r2 = asyncio.run(submit_trade(track, **kwargs))

    # First attempt is ambiguous; the second identical attempt must be refused
    # before reaching the exchange so a possibly-live order is not duplicated.
    assert r1 is None and r2 is None
    assert place_bet.call_count == 1
    cid = compute_client_order_id(
        track="BOTH",
        window_end_ts=window.end_ts,
        role="PRIMARY",
        token_id="up-1",
        price=0.58,
        amount_usdc=5.0,
    )
    assert track.ledger.has_open_order_intent(cid) is True
