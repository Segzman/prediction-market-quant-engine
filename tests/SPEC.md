# tests/ — Spec

All tests use `pytest`. Async tests use `pytest-asyncio`.
External calls (CLOB API, Binance WS, file I/O) are mocked with `unittest.mock`.
No test should make real network calls or write to real files.
PnL assertions are always net of fees (entry + each exit leg at the
crypto taker estimate); gross-only tuning is forbidden.

## conftest.py

```python
# Shared fixtures

@pytest.fixture
def sample_window() -> WindowState:
    """A WindowState with end_ts = now + 120s, up/down tokens set."""

@pytest.fixture
def bot_state() -> BotState:
    """A fresh BotState with btc_price=82000, no trades."""

@pytest.fixture
def mock_client() -> MagicMock:
    """Mock PolymarketClient with sensible default return values."""

@pytest.fixture
def tmp_ledger(tmp_path) -> Ledger:
    """Ledger writing to a temp file — deleted after each test."""
```

---

## test_probability.py

### Model A Tests

```python
def test_model_a_returns_half_at_zero_move():
    assert model_a_momentum(82000, 82000, 150) == 0.5

def test_model_a_skews_above_half_on_positive_move():
    prob = model_a_momentum(82000, 82082, 60)   # +0.1% move
    assert prob > 0.5

def test_model_a_skews_below_half_on_negative_move():
    prob = model_a_momentum(82000, 81918, 60)   # -0.1% move
    assert prob < 0.5

def test_model_a_more_confident_with_less_time():
    # Same move, less time → higher confidence
    prob_90s = model_a_momentum(82000, 82082, 90)
    prob_30s = model_a_momentum(82000, 82082, 30)
    assert prob_30s > prob_90s

def test_model_a_caps_at_0_95():
    prob = model_a_momentum(82000, 83000, 10)   # huge move, little time
    assert prob <= 0.95

def test_model_a_floors_at_0_05():
    prob = model_a_momentum(82000, 81000, 10)
    assert prob >= 0.05

def test_model_a_returns_half_when_open_price_zero():
    assert model_a_momentum(0, 82000, 60) == 0.5
```

### Model B Tests

```python
def test_model_b_returns_half_at_zero_drift_and_nonzero_vol():
    # No drift → symmetric → 50%
    prob = model_b_brownian(82000, 82000, 60, volatility=0.0001)
    assert abs(prob - 0.5) < 0.01

def test_model_b_above_half_on_positive_drift():
    prob = model_b_brownian(82000, 82082, 60, volatility=0.0001)
    assert prob > 0.5

def test_model_b_falls_back_to_model_a_on_zero_vol():
    prob_b = model_b_brownian(82000, 82082, 60, volatility=0.0)
    prob_a = model_a_momentum(82000, 82082, 60)
    assert prob_b == prob_a

def test_model_b_returns_current_outcome_at_zero_time():
    # At resolution, if current >= open → 1.0
    assert model_b_brownian(82000, 82001, 0, 0.0001) == 1.0
    assert model_b_brownian(82000, 81999, 0, 0.0001) == 0.0

def test_model_b_higher_vol_widens_to_half():
    # Very high vol → outcome uncertain → prob approaches 0.5
    prob_low_vol = model_b_brownian(82000, 82082, 60, volatility=0.00001)
    prob_high_vol = model_b_brownian(82000, 82082, 60, volatility=0.01)
    assert abs(prob_high_vol - 0.5) < abs(prob_low_vol - 0.5)
```

---

## test_edge.py

```python
def test_positive_edge_up_direction():
    # theo=0.70, market UP price=0.60 → edge=0.10
    edge = calculate_edge(0.70, 0.60, Direction.UP)
    assert abs(edge - 0.10) < 0.001

def test_negative_edge_up_direction():
    # theo=0.50, market UP price=0.65 → edge=-0.15
    edge = calculate_edge(0.50, 0.65, Direction.UP)
    assert edge < 0

def test_positive_edge_down_direction():
    # theo=0.30 (P(down)=0.70), market DOWN price=0.60 → edge=0.10
    edge = calculate_edge(0.30, 0.60, Direction.DOWN)
    assert abs(edge - 0.10) < 0.001

def test_is_tradeable_above_threshold():
    assert is_tradeable(0.05, min_edge=0.04) is True

def test_is_not_tradeable_below_threshold():
    assert is_tradeable(0.03, min_edge=0.04) is False

def test_is_not_tradeable_on_zero_edge():
    assert is_tradeable(0.0) is False

def test_expected_value_positive_edge():
    # Betting $5 at 0.60 with true prob 0.70
    ev = expected_value(edge=0.10, bet_size=5.0, market_price=0.60)
    assert ev > 0

def test_expected_value_negative_edge():
    ev = expected_value(edge=-0.10, bet_size=5.0, market_price=0.60)
    assert ev < 0
```

---

## test_signal.py

```python
def test_no_signal_when_open_price_zero(sample_window, mock_client):
    sample_window.open_price = 0
    result = evaluate(sample_window, 82000, 60, 0.55, 0.45)
    assert result is None
    assert sample_window.open_price == 82000   # open price set as side effect

def test_no_signal_below_momentum_threshold(sample_window):
    sample_window.open_price = 82000
    # Move of 0.001% — below 0.05% threshold
    result = evaluate(sample_window, 82000.82, 60, 0.52, 0.48)
    assert result is None

def test_no_signal_when_edge_insufficient(sample_window):
    sample_window.open_price = 82000
    # Large move but market has priced it in
    result = evaluate(sample_window, 82164, 60, up_market_price=0.74, down_market_price=0.26)
    # theo ~0.77, market 0.74, edge=0.03 < 0.04 threshold
    assert result is None

def test_signal_returned_when_sufficient_edge(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 82164, 60, up_market_price=0.60, down_market_price=0.40)
    assert result is not None
    assert result.direction == Direction.UP
    assert result.edge >= 0.04

def test_signal_direction_down_on_negative_move(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 81836, 60, up_market_price=0.40, down_market_price=0.55)
    # DOWN move, market DOWN price = 0.55, we think DOWN prob is higher
    if result:
        assert result.direction == Direction.DOWN
```

---

## test_risk.py

```python
def test_can_trade_normal_conditions(bot_state):
    guard = DailyGuard(bot_state)
    bot_state.btc_price_updated_ts = time.time()
    ok, reason = guard.can_trade()
    assert ok is True

def test_blocked_when_daily_loss_limit_hit(bot_state):
    bot_state.daily_pnl = -25.0   # over the $20 limit
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_trade()
    assert ok is False
    assert "loss limit" in reason.lower()

def test_blocked_when_cooldown_active(bot_state):
    bot_state.last_trade_ts = time.time() - 10   # 10s ago, cooldown is 60s
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_trade()
    assert ok is False
    assert "cooldown" in reason.lower()

def test_blocked_when_price_stale(bot_state):
    bot_state.btc_price_updated_ts = time.time() - 30   # stale
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_trade()
    assert ok is False
    assert "stale" in reason.lower()

def test_blocked_after_consecutive_losses(bot_state):
    bot_state.consecutive_losses = 3
    bot_state.last_trade_ts = time.time() - 30   # within pause window
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_trade()
    assert ok is False
    assert "consecutive" in reason.lower()

def test_consecutive_losses_clears_after_pause(bot_state):
    bot_state.consecutive_losses = 3
    bot_state.last_trade_ts = time.time() - 700   # pause expired
    guard = DailyGuard(bot_state)
    ok, _ = guard.can_trade()
    assert ok is True

# Sizing
def test_fixed_size_returns_max_bet():
    assert fixed_size(max_bet=5.0) == 5.0

def test_kelly_size_below_max_bet():
    size = kelly_size(edge=0.10, market_price=0.55, bankroll=200.0, max_bet=5.0)
    assert 0 < size <= 5.0

def test_kelly_size_zero_on_negative_edge():
    size = kelly_size(edge=-0.05, market_price=0.55, bankroll=200.0, max_bet=5.0)
    assert size == 0

def test_kelly_size_capped_at_max_bet():
    # Huge edge → Kelly wants to bet a lot → should be capped
    size = kelly_size(edge=0.40, market_price=0.55, bankroll=10000.0, max_bet=5.0)
    assert size == 5.0
```

---

## test_window.py

```python
def make_window(seconds_until_end: int) -> WindowState:
    now = int(time.time())
    end_ts = now + seconds_until_end
    return WindowState(start_ts=end_ts-300, end_ts=end_ts, market_id="x",
                       up_token_id="u", down_token_id="d")

def test_phase_waiting_when_far_from_end():
    w = make_window(200)
    assert w.get_phase(int(time.time())) == WindowPhase.WAITING

def test_phase_entry_window_at_60s():
    w = make_window(60)
    assert w.get_phase(int(time.time())) == WindowPhase.ENTRY_WINDOW

def test_phase_too_late_at_10s():
    w = make_window(10)
    assert w.get_phase(int(time.time())) == WindowPhase.TOO_LATE

def test_phase_expired_at_minus_5s():
    w = make_window(-5)
    assert w.get_phase(int(time.time())) == WindowPhase.EXPIRED

def test_is_in_entry_window_true():
    w = make_window(60)
    assert w.is_in_entry_window(int(time.time())) is True

def test_set_open_price_only_sets_once():
    w = make_window(120)
    w.set_open_price(82000)
    w.set_open_price(83000)   # should not overwrite
    assert w.open_price == 82000

def test_seconds_remaining_positive():
    w = make_window(60)
    assert w.seconds_remaining(int(time.time())) > 0

def test_seconds_remaining_floors_at_zero():
    w = make_window(-10)
    assert w.seconds_remaining(int(time.time())) == 0
```

---

## test_orderer.py

```python
def test_dry_run_does_not_call_api(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    mock_client.create_order.assert_not_called()
    assert result.status == OrderStatus.FILLED
    assert result.dry_run is True

def test_dry_run_returns_fake_order_id(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    assert result.order_id is not None
    assert "dry-run" in result.order_id

def test_live_mode_calls_api_with_correct_params(mock_client):
    mock_client.create_order.return_value = {"orderID": "real-id-123"}
    orderer = Orderer(mock_client, dry_run=False)
    result = orderer.place_bet("token_up", Direction.UP, 5.0, 0.58)
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args
    assert call_kwargs[1]["price"] == 0.59   # market_price + 0.01
    assert call_kwargs[1]["token_id"] == "token_up"

def test_cancel_called_on_window_expiry(mock_client):
    mock_client.get_order.return_value = {"status": "OPEN"}
    mock_client.cancel_order.return_value = {"success": True}
    orderer = Orderer(mock_client, dry_run=False)
    result = orderer.cancel("real-id-123")
    mock_client.cancel_order.assert_called_once_with("real-id-123")
    assert result is True

@pytest.mark.asyncio
async def test_wait_for_fill_dry_run_returns_immediately(mock_client):
    orderer = Orderer(mock_client, dry_run=True)
    result = await orderer.wait_for_fill("dry-run-xyz", deadline_ts=int(time.time())+60)
    assert result.status == OrderStatus.FILLED

@pytest.mark.asyncio
async def test_wait_for_fill_cancels_past_deadline(mock_client):
    mock_client.get_order.return_value = {"status": "OPEN"}
    mock_client.cancel_order.return_value = {}
    orderer = Orderer(mock_client, dry_run=False)
    past_deadline = int(time.time()) - 1
    result = await orderer.wait_for_fill("real-id", deadline_ts=past_deadline)
    assert result.status == OrderStatus.CANCELLED
```

---

## test_ledger.py

```python
def test_trade_written_to_file(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600, direction="UP", amount_usdc=5.0,
        price=0.58, edge=0.07, ev=0.42, model="A",
        order_id="abc123", dry_run=True
    )
    trades = tmp_ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["direction"] == "UP"
    assert trades[0]["order_id"] == "abc123"

def test_resolution_written_to_file(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600, direction="UP", amount_usdc=5.0,
        price=0.58, edge=0.07, ev=0.42, model="A", order_id="abc123", dry_run=True
    )
    tmp_ledger.record_resolution("abc123", 1743612600, resolved_up=True,
                                  direction="UP", amount_usdc=5.0, price=0.58)
    resolutions = tmp_ledger.get_all_resolutions()
    assert len(resolutions) == 1
    assert resolutions[0]["resolved_up"] is True
    assert resolutions[0]["pnl"] > 0   # winning trade

def test_resolution_pnl_negative_on_loss(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600, direction="UP", amount_usdc=5.0,
        price=0.58, edge=0.07, ev=0.42, model="A", order_id="abc123", dry_run=True
    )
    tmp_ledger.record_resolution("abc123", 1743612600, resolved_up=False,
                                  direction="UP", amount_usdc=5.0, price=0.58)
    resolutions = tmp_ledger.get_all_resolutions()
    assert resolutions[0]["pnl"] == -5.0

def test_daily_summary_correct(tmp_ledger):
    # Add 3 trades: 2 wins, 1 loss
    for i, (direction, resolved_up, pnl_positive) in enumerate([
        ("UP", True, True), ("DOWN", True, True), ("UP", False, False)
    ]):
        oid = f"order-{i}"
        tmp_ledger.record_trade(1743612600+i*300, direction, 5.0, 0.58, 0.07, 0.42, "A", oid, True)
        tmp_ledger.record_resolution(oid, 1743612600+i*300, resolved_up, direction, 5.0, 0.58)
    
    summary = tmp_ledger.get_daily_summary()
    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert abs(summary["win_rate"] - 0.667) < 0.01

def test_file_created_if_not_exists(tmp_path):
    path = str(tmp_path / "new_trades.jsonl")
    ledger = Ledger(filepath=path)
    assert os.path.exists(path)

def test_multiple_trades_all_returned(tmp_ledger):
    for i in range(5):
        tmp_ledger.record_trade(
            1743612600+i*300, "UP", 5.0, 0.58, 0.07, 0.42, "A", f"order-{i}", True
        )
    assert len(tmp_ledger.get_all_trades()) == 5
```

---

## test_finder.py

```python
def test_finds_active_window(mock_client):
    now = int(time.time())
    mock_client.get_markets.return_value = {"data": [{
        "market_slug": "btc-updown-5m-1234",
        "condition_id": "cond-abc",
        "end_date_iso": datetime.fromtimestamp(now + 120, tz=timezone.utc).isoformat(),
        "tokens": [
            {"outcome": "Up",   "token_id": "up-token"},
            {"outcome": "Down", "token_id": "down-token"},
        ]
    }]}
    finder = MarketFinder(mock_client)
    window = finder.find_active_window()
    assert window is not None
    assert window.up_token_id == "up-token"
    assert window.down_token_id == "down-token"

def test_skips_expired_windows(mock_client):
    now = int(time.time())
    mock_client.get_markets.return_value = {"data": [{
        "market_slug": "btc-updown-5m-1234",
        "condition_id": "cond-abc",
        "end_date_iso": datetime.fromtimestamp(now - 60, tz=timezone.utc).isoformat(),
        "tokens": [
            {"outcome": "Up", "token_id": "up"},
            {"outcome": "Down", "token_id": "down"},
        ]
    }]}
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None

def test_skips_non_btc_markets(mock_client):
    now = int(time.time())
    mock_client.get_markets.return_value = {"data": [{
        "market_slug": "eth-updown-5m-9999",
        "condition_id": "cond-xyz",
        "end_date_iso": datetime.fromtimestamp(now + 120, tz=timezone.utc).isoformat(),
        "tokens": [{"outcome": "Up", "token_id": "u"}, {"outcome": "Down", "token_id": "d"}]
    }]}
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None

def test_uses_cache_within_ttl(mock_client):
    mock_client.get_markets.return_value = {"data": []}
    finder = MarketFinder(mock_client)
    finder.find_active_window()
    finder.find_active_window()
    # Should only call API once — second call uses cache
    assert mock_client.get_markets.call_count == 1

def test_returns_none_when_no_active_window(mock_client):
    mock_client.get_markets.return_value = {"data": []}
    finder = MarketFinder(mock_client)
    assert finder.find_active_window() is None
```
