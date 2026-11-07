import time

from state.position import ExitReason, Position, PositionStatus


def test_time_exit_exits_underwater_when_conviction_has_decayed():
    position = Position(
        order_id="pos-1",
        window_end_ts=300,
        direction="UP",
        size_usdc=5.0,
        entry_price=0.60,
        entry_ts=time.time() - 8,
        time_exit_seconds=5,
        entry_market_conviction=0.82,
        conviction_decay_floor=0.60,
    )

    should_exit, reason = position.should_exit(
        current_price=0.58,
        seconds_elapsed=8,
        market_conviction=0.61,
    )

    assert should_exit is True
    assert reason == ExitReason.TIME_EXIT


def _tp_position(**overrides):
    params = dict(
        order_id="pos-tp1",
        window_end_ts=300,
        direction="UP",
        size_usdc=5.0,
        entry_price=0.60,
        entry_ts=time.time() - 3,
        take_profit_price=0.60 * 1.15,
        stop_loss_price=0.60 * 0.90,
        take_profit_1_price=0.60 * 1.08,
        take_profit_1_fraction=0.5,
    )
    params.update(overrides)
    return Position(**params)


def test_tp1_banks_half_and_runner_continues():
    position = _tp_position()
    assert position.take_partial_profit(0.60 * 1.08) == 0.5
    assert position.take_profit_1_taken is True
    assert position.status == PositionStatus.PARTIAL
    # One-shot: second call banks nothing
    assert position.take_partial_profit(0.60 * 1.10) == 0.0


def test_tp1_skipped_below_level():
    position = _tp_position()
    assert position.take_partial_profit(0.60 * 1.05) == 0.0
    assert position.take_profit_1_taken is False


def test_trailing_ratchet_lifts_sl_and_fires():
    position = _tp_position(stop_loss_price=0.54, take_profit_price=0.60 * 1.30)
    # Run-up to 0.68 with 6% trail -> effective SL 0.6392 (no TP at 0.78 yet)
    exited, _ = position.should_exit(0.68, 10, trail_pct=0.06)
    assert exited is False
    assert position.trailing_stop_price(0.06) == 0.68 * 0.94
    # Dip to 0.63 trips the ratcheted SL (original 0.54 would hold).
    # First tick arms the breach timer, second confirms (0s window).
    exited, _ = position.should_exit(0.63, 11, trail_pct=0.06,
                                    sl_confirmation_seconds=0.0)
    assert exited is False
    exited, reason = position.should_exit(0.63, 12, trail_pct=0.06,
                                         sl_confirmation_seconds=0.0)
    assert exited is True
    assert reason == ExitReason.STOP_LOSS


def test_trailing_never_ratchets_down_on_dips():
    position = _tp_position(stop_loss_price=0.54, take_profit_price=0.60 * 1.30)
    position.should_exit(0.68, 10, trail_pct=0.06)
    assert position.trailing_stop_price(0.06) == 0.68 * 0.94
    # Dip leaves the recorded high (and ratchet) untouched
    position.should_exit(0.60, 11, trail_pct=0.06,
                         sl_confirmation_seconds=1000.0,
                         sl_instant_breach_pct=0.50)
    assert position.highest_price == 0.68
    assert position.trailing_stop_price(0.06) == 0.68 * 0.94


def test_close_accounts_fees_in_pnl():
    position = _tp_position()
    pnl = position.close(exit_price=0.66, exit_ts=time.time(), reason=ExitReason.TAKE_PROFIT,
                         fee_usdc=0.05, partial_pct=1.0)
    # shares = 5/0.60, gross = shares*0.66, pnl = gross - 5 - 0.05
    assert pnl == (5.0 / 0.60) * 0.66 - 5.0 - 0.05
    assert position.fee_usdc == 0.05


def test_tp1_partial_net_of_fees_stays_positive():
    position = _tp_position()
    assert position.take_partial_profit(0.60 * 1.08) == 0.5
    part = 5.0 * 0.5
    fee = part * 0.072 * 0.648 * (1 - 0.648)
    pnl = position.close(exit_price=0.648, exit_ts=time.time(), reason=ExitReason.TAKE_PROFIT,
                         fee_usdc=fee, partial_pct=0.5)
    assert pnl > 0  # banked half at +8% must clear its own exit fee
    assert position.status == PositionStatus.PARTIAL
