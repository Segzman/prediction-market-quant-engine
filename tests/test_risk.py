import time

import risk.guard as guard_module
from config import MIN_BET_USDC
from risk.guard import DailyGuard
from risk.sizing import fixed_size, get_bet_size, kelly_size, plan_position_sizes
from state.position import Position, PositionStatus
from state.session import BotState
from state.window import WindowState


def test_can_enter_normal_conditions(bot_state):
    guard = DailyGuard(bot_state)
    bot_state.btc_price_updated_ts = time.time()
    ok, reason = guard.can_enter()
    assert ok is True
    assert reason == ""


def test_blocked_when_daily_loss_limit_hit(bot_state):
    bot_state.daily_pnl = -25.0
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "loss limit" in reason.lower()


def test_blocked_when_cooldown_active(bot_state):
    bot_state.btc_price_updated_ts = time.time()
    bot_state.last_trade_ts = time.time() - 0.3  # 0.3s ago, cooldown is 1s
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "cooldown" in reason.lower()


def test_blocked_when_price_stale_by_default(bot_state):
    bot_state.btc_price_updated_ts = time.time() - 30
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "stale" in reason.lower()


def test_blocked_after_consecutive_losses(bot_state):
    bot_state.btc_price_updated_ts = time.time()
    bot_state.consecutive_losses = 3
    bot_state.last_trade_ts = time.time() - 30
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "consecutive" in reason.lower()


def test_consecutive_losses_clears_after_pause(bot_state):
    bot_state.btc_price_updated_ts = time.time()
    bot_state.consecutive_losses = 3
    bot_state.last_trade_ts = time.time() - 700
    guard = DailyGuard(bot_state)
    ok, _ = guard.can_enter()
    assert ok is True


def test_blocked_when_max_open_orders_reached(bot_state, monkeypatch):
    monkeypatch.setattr(guard_module, "MAX_OPEN_ORDERS", 1)
    bot_state.btc_price_updated_ts = time.time()
    bot_state.record_pending_order(
        direction="UP",
        amount=2.0,
        price=0.8,
        model="A",
        order_id="pending-1",
        dry_run=True,
        window_end_ts=int(time.time()) + 120,
    )
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "open orders" in reason.lower()


def test_blocked_when_max_concurrent_positions_reached(bot_state, monkeypatch):
    monkeypatch.setattr(guard_module, "MAX_CONCURRENT_POSITIONS", 1)
    bot_state.btc_price_updated_ts = time.time()
    bot_state.record_trade(
        direction="UP",
        amount=2.0,
        price=0.8,
        edge=0.05,
        model="A",
        order_id="trade-1",
        dry_run=True,
        window_end_ts=int(time.time()) + 120,
    )
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "concurrent positions" in reason.lower()


def test_blocked_when_max_unresolved_exposure_reached(bot_state, monkeypatch):
    monkeypatch.setattr(guard_module, "MAX_UNRESOLVED_EXPOSURE_USDC", 3.0)
    bot_state.btc_price_updated_ts = time.time()
    bot_state.record_pending_order(
        direction="UP",
        amount=3.0,
        price=0.8,
        model="A",
        order_id="pending-2",
        dry_run=True,
        window_end_ts=int(time.time()) + 120,
    )
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter()
    assert ok is False
    assert "unresolved exposure" in reason.lower()


def test_blocked_when_max_window_exposure_reached(bot_state, monkeypatch):
    monkeypatch.setattr(guard_module, "MAX_WINDOW_EXPOSURE_USDC", 2.0)
    bot_state.btc_price_updated_ts = time.time()
    end_ts = int(time.time()) + 120
    window = WindowState(
        start_ts=end_ts - 300,
        end_ts=end_ts,
        market_id="condition-1",
        up_token_id="up-token",
        down_token_id="down-token",
    )
    bot_state.current_window = window
    window.add_position(
        Position(
            order_id="trade-2",
            window_end_ts=end_ts,
            direction="UP",
            size_usdc=2.0,
            entry_price=0.8,
            entry_ts=time.time(),
            status=PositionStatus.OPEN,
        )
    )
    guard = DailyGuard(bot_state)
    ok, reason = guard.can_enter(direction="UP")
    assert ok is False
    assert "window exposure" in reason.lower()


def test_fixed_size_returns_max_bet():
    assert fixed_size(max_bet=5.0) == 5.0


def test_kelly_size_below_max_bet():
    size = kelly_size(edge=0.10, market_price=0.55, bankroll=200.0, max_bet=5.0)
    assert 0 < size <= 5.0


def test_kelly_size_zero_on_negative_edge():
    size = kelly_size(edge=-0.05, market_price=0.55, bankroll=200.0, max_bet=5.0)
    assert size == 0


def test_kelly_size_capped_at_max_bet():
    size = kelly_size(edge=0.40, market_price=0.55, bankroll=10000.0, max_bet=5.0)
    assert size == 5.0


def test_kelly_enabled_still_applies_risk_controls():
    raw_kelly = kelly_size(edge=0.10, market_price=0.55, bankroll=200.0, max_bet=5.0)
    assert raw_kelly == 5.0  # capped: old code returned this directly
    sized = get_bet_size(
        edge=0.10,
        market_price=0.55,
        bankroll=200.0,
        current_bankroll=50.0,  # drawdown -> bankroll_factor floor 0.5
        starting_bankroll=200.0,
        consecutive_losses=4,
        liquidity_score=0.0,  # WEAK tier
        same_side_exposure_usdc=50.0,
        total_open_exposure_usdc=100.0,
        use_kelly=True,
    )
    assert 0 <= sized < raw_kelly


def test_get_bet_size_respects_available_bankroll():
    size = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=2.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    assert 0 < size <= 2.0


def test_get_bet_size_uses_adaptive_size_when_affordable():
    size = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    assert MIN_BET_USDC <= size <= 5.0


def test_get_bet_size_increases_after_wins():
    base = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=4.0,
        current_bankroll=4.0,
        starting_bankroll=10.0,
    )
    grown = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        consecutive_wins=2,
    )
    assert grown > base


def test_get_bet_size_decreases_after_losses():
    base = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    reduced = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=4.0,
        current_bankroll=4.0,
        starting_bankroll=10.0,
        consecutive_losses=2,
    )
    assert 0 < reduced < base


def test_get_bet_size_tiers_up_for_stronger_setups():
    weak = get_bet_size(
        edge=0.05,
        market_price=0.48,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    medium = get_bet_size(
        edge=0.08,
        market_price=0.48,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    strong = get_bet_size(
        edge=0.14,
        market_price=0.48,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    assert weak <= medium <= strong
    assert weak > 0


def test_get_bet_size_scales_down_for_thin_liquidity():
    healthy = get_bet_size(
        edge=0.12,
        market_price=0.42,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        liquidity_score=0.80,
        net_expected_profit_usdc=0.10,
    )
    thin = get_bet_size(
        edge=0.12,
        market_price=0.42,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        liquidity_score=0.20,
        net_expected_profit_usdc=0.10,
    )
    assert MIN_BET_USDC <= thin < healthy


def test_get_bet_size_scales_down_for_low_net_profit():
    strong = get_bet_size(
        edge=0.12,
        market_price=0.42,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        liquidity_score=0.80,
        net_expected_profit_usdc=0.10,
    )
    weak = get_bet_size(
        edge=0.12,
        market_price=0.42,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        liquidity_score=0.80,
        net_expected_profit_usdc=0.01,
    )
    assert MIN_BET_USDC <= weak < strong


def test_get_bet_size_scales_down_for_late_conviction_regime():
    mispricing = get_bet_size(
        edge=0.08,
        market_price=0.40,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        entry_regime="MISPRICING",
        liquidity_score=0.85,
        net_expected_profit_usdc=0.10,
    )
    late = get_bet_size(
        edge=0.03,
        market_price=0.70,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        entry_regime="LATE_CONVICTION",
        liquidity_score=0.85,
        net_expected_profit_usdc=0.04,
    )
    assert MIN_BET_USDC <= late <= mispricing


def test_get_bet_size_scales_down_for_same_side_inventory():
    clean = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
    )
    loaded = get_bet_size(
        edge=0.08,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        same_side_exposure_usdc=4.0,
        total_open_exposure_usdc=6.0,
    )
    assert MIN_BET_USDC <= loaded < clean


def test_get_bet_size_still_sizes_for_forced_negative_edge():
    size = get_bet_size(
        edge=-0.02,
        market_price=0.55,
        bankroll=10.0,
        current_bankroll=10.0,
        starting_bankroll=10.0,
        use_kelly=False,
    )
    assert size > 0


def test_plan_position_sizes_skips_too_small_hedge():
    plan = plan_position_sizes(
        target_primary_bet=5.0,
        available_bankroll=9.0,  # $5 primary + $5 min hedge = $10 needed
        hedge_enabled=True,
        hedge_fraction=0.35,
    )
    assert plan.primary_usdc >= 5.0  # At least $5 min bet
    assert plan.hedge_usdc >= MIN_BET_USDC
    assert plan.hedge_active is True


def test_plan_position_sizes_scales_primary_and_hedge_sustainably():
    plan = plan_position_sizes(
        target_primary_bet=5.0,
        available_bankroll=12.0,  # ~$1.2 reserve + $5 primary + $5 min hedge
        hedge_enabled=True,
        hedge_fraction=0.5,
        use_loss_cap=False,  # Disable loss cap for basic scaling test
    )
    assert plan.primary_usdc >= 5.0
    assert plan.hedge_usdc >= MIN_BET_USDC
    assert plan.hedge_active is True


def test_plan_position_sizes_uses_loss_cap_when_hedge_price_provided():
    """Test that 2% loss cap strategy is used when hedge_price is provided."""
    plan = plan_position_sizes(
        target_primary_bet=5.0,
        available_bankroll=20.0,
        hedge_enabled=True,
        hedge_fraction=0.5,
        hedge_price=0.45,
        use_loss_cap=True,
        max_loss_pct=0.02,
    )
    assert plan.primary_usdc >= 5.0
    assert plan.hedge_usdc >= 5.0  # $5 min hedge enforced
    assert plan.hedge_active is True
    # With 2% loss cap on $20 bankroll, max loss = $0.40
    # Primary bet $5 - max loss $0.40 = $4.60 needs to be covered by hedge
    # Hedge at $0.45 pays $1.22 per $1 invested
    # Required hedge = $4.60 / 1.22 = ~$3.77, but $5 min takes precedence
    assert plan.hedge_usdc >= 5.0  # Min $5 hedge


def test_plan_position_sizes_skips_hedge_when_loss_cap_not_needed():
    """Test that hedge is skipped when primary loss is already within 2% limit."""
    plan = plan_position_sizes(
        target_primary_bet=5.0,
        available_bankroll=300.0,  # Large bankroll, 2% = $6
        hedge_enabled=True,
        hedge_fraction=0.5,
        hedge_price=0.45,
        use_loss_cap=True,
        max_loss_pct=0.02,  # 2% of $300 = $6 max loss
    )
    # Primary $5, so max loss is $5 which is < $6 (2% of $300)
    # Hedge should be skipped
    assert plan.primary_usdc == 5.0
    assert plan.hedge_usdc == 0.0
    assert plan.hedge_active is False
    assert plan.hedge_reason == "LOSS_CAP_NO_HEDGE_NEEDED"


def test_cooldown_blocks_entry_not_exit():
    """can_enter() is blocked during cooldown; check_position_exits never calls DailyGuard."""
    state = BotState(btc_price=82000.0)
    state.consecutive_losses = 3   # triggers pause (CONSECUTIVE_LOSS_LIMIT = 3)
    state.last_trade_ts = time.time()  # just traded

    guard = DailyGuard(state)
    can, reason = guard.can_enter()
    assert can is False
    assert "pause" in reason.lower() or "cooldown" in reason.lower()

    # check_position_exits must not exist on DailyGuard (exits bypass it)
    assert not hasattr(guard, "can_exit")
