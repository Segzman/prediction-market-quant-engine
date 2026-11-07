import pytest

from state.session import BotState


def test_bankroll_tracks_pending_exposure_and_realized_pnl():
    state = BotState(starting_bankroll_usdc=10.0)

    state.record_trade(
        direction="UP",
        amount=2.5,
        price=0.5,
        edge=0.1,
        model="A",
        order_id="primary-1",
        dry_run=True,
        window_end_ts=100,
    )
    state.record_trade(
        direction="DOWN",
        amount=1.0,
        price=0.5,
        edge=-0.02,
        model="A",
        order_id="hedge-1",
        dry_run=True,
        window_end_ts=100,
        role="HEDGE",
    )

    assert state.current_bankroll_usdc() == 10.0
    assert state.pending_exposure_usdc() == 3.5
    assert state.available_bankroll_usdc() == 6.5

    state.record_resolution("primary-1", True)

    assert state.current_bankroll_usdc() == 12.5
    assert state.pending_exposure_usdc() == 1.0
    assert state.available_bankroll_usdc() == 11.5
    assert state.consecutive_wins == 1
    assert state.consecutive_losses == 0


def test_record_fee_reduces_realized_bankroll_on_resolution():
    state = BotState(starting_bankroll_usdc=10.0)
    state.record_trade(
        direction="UP",
        amount=5.0,
        price=0.67,
        edge=0.1,
        model="A",
        order_id="primary-1",
        dry_run=True,
        window_end_ts=100,
    )
    assert state.record_fee("primary-1", 0.07) is True
    state.record_resolution("primary-1", False)
    assert round(state.daily_pnl, 2) == -5.07
    assert round(state.current_bankroll_usdc(), 2) == 4.93


def test_cancellation_releases_reserved_bankroll():
    state = BotState(starting_bankroll_usdc=10.0)

    state.record_trade(
        direction="UP",
        amount=3.0,
        price=0.6,
        edge=0.08,
        model="A",
        order_id="primary-1",
        dry_run=True,
        window_end_ts=100,
    )

    assert state.available_bankroll_usdc() == 7.0
    assert state.unresolved_primary_count() == 1

    assert state.record_cancellation("primary-1") is True
    assert state.available_bankroll_usdc() == 10.0
    assert state.unresolved_primary_count() == 0
    assert state.cancelled_primary_count() == 1


def test_pending_order_reserves_bankroll_until_fill_or_cancel():
    state = BotState(starting_bankroll_usdc=10.0)

    state.record_pending_order(
        direction="UP",
        amount=4.0,
        price=0.58,
        model="A",
        order_id="pending-1",
        dry_run=False,
        window_end_ts=100,
    )

    assert state.pending_exposure_usdc() == 4.0
    assert state.available_bankroll_usdc() == 6.0
    assert state.unresolved_primary_count() == 1

    assert state.record_cancellation("pending-1") is True
    assert state.pending_exposure_usdc() == 0.0
    assert state.available_bankroll_usdc() == 10.0
    assert state.unresolved_primary_count() == 0


def test_record_trade_persists_entry_regime():
    state = BotState(starting_bankroll_usdc=10.0)

    trade = state.record_trade(
        direction="UP",
        amount=2.0,
        price=0.55,
        edge=0.1,
        model="A",
        order_id="t-1",
        dry_run=True,
        window_end_ts=100,
        entry_regime="LATE_CONVICTION",
    )

    assert trade.entry_regime == "LATE_CONVICTION"
    assert state.trade_history[-1].entry_regime == "LATE_CONVICTION"


def test_open_directional_exposure_tracks_trades_and_pending_orders():
    state = BotState(starting_bankroll_usdc=10.0)
    state.record_trade(
        direction="UP",
        amount=2.0,
        price=0.55,
        edge=0.1,
        model="A",
        order_id="t-1",
        dry_run=True,
        window_end_ts=100,
    )
    state.record_pending_order(
        direction="UP",
        amount=1.5,
        price=0.56,
        model="A",
        order_id="p-1",
        dry_run=True,
        window_end_ts=100,
    )

    assert state.open_directional_exposure_usdc("UP") == pytest.approx(3.5)
    assert state.open_directional_exposure_usdc("DOWN") == 0.0


def test_rolling_volatility_uses_recent_price_updates():
    state = BotState(starting_bankroll_usdc=10.0)
    for index, price in enumerate(
        [100.0, 100.3, 100.1, 100.5, 100.2, 100.6, 100.4, 100.8, 100.7, 101.0]
    ):
        state.update_price(price, updated_ts=float(index + 1))

    assert state.rolling_volatility() > 0


def test_tick_trend_features_capture_direction_and_acceleration():
    state = BotState(starting_bankroll_usdc=10.0)
    samples = [100.0, 100.02, 100.05, 100.09, 100.14, 100.21]
    for index, price in enumerate(samples):
        state.update_price(price, updated_ts=float(index + 1))

    features = state.tick_trend_features(lookback_seconds=10)
    assert features["tick_move_pct"] > 0
    assert features["directional_consistency"] >= 0.8
    assert features["acceleration_pct"] > 0


def test_update_price_tracks_source_and_ignores_older_updates(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 12.0)

    state.update_price(100.0, updated_ts=10.0, source="BINANCE")
    state.update_price(99.0, updated_ts=9.0, source="COINBASE")
    state.update_price(100.2, updated_ts=11.0, source="COINBASE")

    assert state.btc_price == pytest.approx(100.2)
    assert state.price_source == "COINBASE"
    assert state.btc_price_updated_ts == 11.0


def test_update_price_aggregates_median_across_healthy_sources(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 12.0)

    state.update_price(100.0, updated_ts=10.0, source="KRAKEN")
    state.update_price(100.2, updated_ts=10.5, source="COINBASE")
    state.update_price(99.9, updated_ts=11.0, source="GEMINI")

    assert state.btc_price == pytest.approx(99.9)
    assert state.healthy_price_source_count() == 3
    assert state.price_feed_quorum_met() is True
    assert state.is_price_stale() is False
    assert state.price_source == "GEMINI"
    assert state.fastest_price_source() == "GEMINI"


def test_price_quorum_fails_with_only_one_healthy_source(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 20.0)

    state.update_price(100.0, updated_ts=10.0, source="KRAKEN")
    state.update_price(101.0, updated_ts=10.0, source="COINBASE")
    state.update_price(102.0, updated_ts=19.0, source="GEMINI")

    assert state.healthy_price_source_count() == 1
    assert state.price_feed_quorum_met() is False
    assert state.is_price_stale() is True


def test_price_quorum_fails_on_large_divergence(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 11.0)

    state.update_price(100.0, updated_ts=10.0, source="KRAKEN")
    state.update_price(101.0, updated_ts=10.0, source="COINBASE")
    state.update_price(110.0, updated_ts=10.0, source="GEMINI")

    assert state.healthy_price_source_count() == 3
    assert state.price_feed_divergence_bps() > 50.0
    assert state.price_feed_quorum_met() is False
    assert state.is_price_stale() is True


def test_state_exposes_price_and_book_freshness(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 20.0)

    state.update_price(100.0, updated_ts=10.0, source="BINANCE")
    state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.55,
        down_ask=0.45,
        up_bid=0.53,
        down_bid=0.43,
        updated_ts=15.0,
    )

    assert state.btc_price_age_seconds() == pytest.approx(10.0)
    assert state.is_price_stale() is True
    assert state.polymarket_book_age_seconds() == pytest.approx(5.0)
    assert state.is_polymarket_book_stale() is False


def test_clear_polymarket_book_resets_book_freshness(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 20.0)

    state.update_polymarket_book(
        window_end_ts=300,
        up_ask=0.55,
        down_ask=0.45,
        up_bid=0.53,
        down_bid=0.43,
        updated_ts=15.0,
    )
    state.clear_polymarket_book()

    assert state.polymarket_book_age_seconds() is None
    assert state.polymarket_window_end_ts is None


def test_polymarket_features_track_conviction_and_reset_per_window():
    state = BotState(starting_bankroll_usdc=10.0)
    for index, price in enumerate([100.0, 100.01, 100.02, 100.03], start=1):
        state.update_price(price, updated_ts=float(index))
    for index, up_ask in enumerate([0.45, 0.40, 0.34], start=1):
        state.update_polymarket_book(
            window_end_ts=300,
            up_ask=up_ask,
            down_ask=1 - up_ask,
            up_bid=max(0.0, up_ask - 0.02),
            down_bid=max(0.0, (1 - up_ask) - 0.02),
            up_ask_count=30,
            down_ask_count=30,
            up_bid_count=20,
            down_bid_count=20,
            up_ask_size=3.0,
            down_ask_size=2.0,
            up_bid_size=6.0,
            down_bid_size=5.0,
            up_total_ask_size=15.0,
            down_total_ask_size=12.0,
            up_total_bid_size=18.0,
            down_total_bid_size=16.0,
            updated_ts=10.0 + index,
        )

    features = state.polymarket_features()
    assert features["strongest_side"] == "DOWN"
    assert features["market_conviction"] > 0.6
    assert features["down_price_momentum"] > 0
    assert features["up_queue_imbalance"] > 0
    assert features["down_queue_imbalance"] > 0
    assert features["strongest_depth_to_fill_usdc"] > 0
    assert 0.0 < features["market_implied_up_probability"] < 1.0

    state.update_polymarket_book(
        window_end_ts=600,
        up_ask=0.52,
        down_ask=0.48,
        up_bid=0.50,
        down_bid=0.46,
        up_ask_count=20,
        down_ask_count=20,
        up_bid_count=20,
        down_bid_count=20,
        up_ask_size=1.0,
        down_ask_size=1.0,
        up_bid_size=1.0,
        down_bid_size=1.0,
        up_total_ask_size=2.0,
        down_total_ask_size=2.0,
        up_total_bid_size=2.0,
        down_total_bid_size=2.0,
        updated_ts=20.0,
    )
    reset_features = state.polymarket_features()
    assert reset_features["market_conviction"] == 0.0


def test_polymarket_features_compute_microstructure_fields():
    state = BotState(starting_bankroll_usdc=10.0)
    for index, up_ask in enumerate([0.58, 0.60], start=1):
        state.update_polymarket_book(
            window_end_ts=300,
            up_ask=up_ask,
            down_ask=1 - up_ask,
            up_bid=up_ask - 0.02,
            down_bid=(1 - up_ask) - 0.02,
            up_ask_count=10,
            down_ask_count=10,
            up_bid_count=10,
            down_bid_count=10,
            up_ask_size=3.0,
            down_ask_size=5.0,
            up_bid_size=9.0,
            down_bid_size=2.0,
            up_total_ask_size=20.0,
            down_total_ask_size=8.0,
            up_total_bid_size=25.0,
            down_total_bid_size=6.0,
            updated_ts=float(index),
        )

    features = state.polymarket_features()

    assert features["up_queue_imbalance"] == pytest.approx((9.0 - 3.0) / 12.0)
    assert features["down_queue_imbalance"] == pytest.approx((2.0 - 5.0) / 7.0)
    assert features["up_microprice_offset"] > 0
    assert features["depth_imbalance"] > 0
    assert features["market_implied_up_probability"] > 0.5


def test_fastest_source_switches_live_when_leader_goes_stale(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 100.0)

    state.update_price(100.0, updated_ts=99.0, source="GEMINI")
    state.update_price(100.1, updated_ts=99.5, source="BYBIT")
    assert state.price_source == "BYBIT"

    monkeypatch.setattr("state.session.time.time", lambda: 110.0)
    state.update_price(101.0, updated_ts=109.0, source="GEMINI")
    assert state.price_source == "GEMINI"
    assert state.fastest_price_source() == "GEMINI"


def test_median_aggregation_mode(monkeypatch):
    monkeypatch.setattr("state.session.PRICE_SOURCE_SELECTION", "median")
    state = BotState(starting_bankroll_usdc=10.0)
    monkeypatch.setattr("state.session.time.time", lambda: 12.0)

    state.update_price(100.0, updated_ts=10.0, source="KRAKEN")
    state.update_price(100.2, updated_ts=10.5, source="COINBASE")
    state.update_price(99.9, updated_ts=11.0, source="GEMINI")

    assert state.btc_price == pytest.approx(100.0)
    assert state.price_source == "MEDIAN[COINBASE,GEMINI,KRAKEN]"


def test_tick_interval_ema_tracked_per_source(monkeypatch):
    state = BotState(starting_bankroll_usdc=10.0)
    state.update_price(100.0, updated_ts=10.0, source="BYBIT")
    state.update_price(100.1, updated_ts=12.0, source="BYBIT")
    assert state.price_tick_ema["BYBIT"] == pytest.approx(2.0)
    state.update_price(100.2, updated_ts=14.0, source="BYBIT")
    assert state.price_tick_ema["BYBIT"] == pytest.approx(2.0)
