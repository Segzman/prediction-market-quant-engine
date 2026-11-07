from state.types import Direction
from state.window import WindowState
from strategy.probability import ModelContext
from strategy.signal import (
    build_hedge_signal,
    compute_sl_confirmation_seconds,
    evaluate,
    evaluate_interval,
    evaluate_with_diagnostics,
)


def test_no_signal_when_open_price_zero(sample_window, mock_client):
    sample_window.open_price = 0
    result = evaluate(sample_window, 82000, 60, 0.55, 0.45)
    assert result is None
    assert sample_window.open_price == 82000


def test_no_signal_below_momentum_threshold(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 82000.82, 60, 0.52, 0.48)
    assert result is None


def test_no_signal_when_edge_insufficient(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 82164, 60, up_market_price=0.74, down_market_price=0.26)
    assert result is None


def test_signal_returned_when_sufficient_edge(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 82164, 60, up_market_price=0.55, down_market_price=0.45)
    assert result is not None
    assert result.direction == Direction.UP
    assert result.edge >= 0.04


def test_no_signal_when_contract_price_outside_entry_band(sample_window):
    sample_window.open_price = 82000
    # 0.65 is above the ENTRY_MAX_CONTRACT_PRICE (0.62) so should produce no signal
    result = evaluate(sample_window, 82164, 60, up_market_price=0.65, down_market_price=0.35)
    assert result is None


def test_signal_direction_down_on_negative_move(sample_window):
    sample_window.open_price = 82000
    result = evaluate(sample_window, 81836, 60, up_market_price=0.40, down_market_price=0.55)
    if result:
        assert result.direction == Direction.DOWN


def test_evaluate_interval_forces_trade_when_requested(sample_window):
    sample_window.open_price = 82000
    # Both sides in band (0.55-0.62). Tiny up move → DOWN has slightly better edge.
    result = evaluate_interval(
        sample_window,
        82000.10,
        60,
        up_market_price=0.58,
        down_market_price=0.57,
        model="A",
        force_trade=True,
    )
    assert result is not None
    assert result.direction == Direction.DOWN


def test_evaluate_interval_force_trade_returns_none_when_both_sides_outside_band(sample_window):
    sample_window.open_price = 82000
    result = evaluate_interval(
        sample_window,
        82000.10,
        60,
        up_market_price=0.03,
        down_market_price=0.97,
        model="A",
        force_trade=True,
    )
    assert result is None


def test_build_hedge_signal_uses_opposite_direction(sample_window):
    sample_window.open_price = 82000
    # Both sides in band. Price moved UP → primary is UP, hedge is DOWN.
    primary = evaluate_interval(
        sample_window,
        82164,
        60,
        up_market_price=0.57,
        down_market_price=0.58,
        model="A",
        force_trade=True,
    )
    hedge = build_hedge_signal(primary, up_market_price=0.57, down_market_price=0.58)
    assert hedge.direction != primary.direction
    assert hedge.market_price == 0.58


def test_build_hedge_signal_returns_none_outside_entry_band(sample_window):
    sample_window.open_price = 82000
    # UP price 0.65 is above ENTRY_MAX_CONTRACT_PRICE (0.62), so hedge (UP) should be blocked
    primary = evaluate_interval(
        sample_window,
        81836,
        60,
        up_market_price=0.65,
        down_market_price=0.35,
        model="A",
        force_trade=False,
    )
    hedge = build_hedge_signal(primary, up_market_price=0.65, down_market_price=0.35)
    assert hedge is None


def test_evaluate_with_diagnostics_enters_late_conviction_regime(sample_window):
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        strongest_depth_to_fill_usdc=12.0,
        weakest_depth_to_fill_usdc=6.0,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=context,
    )
    assert evaluation.signal is not None
    assert evaluation.signal.entry_regime == "LATE_CONVICTION"
    assert evaluation.signal.direction == Direction.DOWN


def test_evaluate_with_diagnostics_rejects_overpriced_late_conviction(sample_window):
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.10,
        down_market_price=0.95,
        model="B",
        model_context=context,
    )
    assert evaluation.signal is None
    assert evaluation.reason_code == "CONVICTION_TOO_EXPENSIVE"


def test_evaluate_with_diagnostics_rejects_late_conviction_when_depth_to_fill_is_weak(sample_window):
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        down_queue_imbalance=0.4,
        down_microprice_offset=0.01,
        strongest_queue_imbalance=0.4,
        strongest_microprice_offset=0.01,
        strongest_depth_to_fill_usdc=1.0,
        weakest_depth_to_fill_usdc=8.0,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=context,
    )
    assert evaluation.signal is None
    assert evaluation.reason_code == "LATE_EDGE_TOO_SMALL"


def test_evaluate_with_diagnostics_rejects_one_sided_high_price_late_conviction(sample_window):
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        strongest_depth_to_fill_usdc=12.0,
        weakest_depth_to_fill_usdc=6.0,
        one_sided_ratio=0.4,
        btc_poly_agreement=1.0,
        market_implied_up_probability=0.18,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=context,
    )
    assert evaluation.signal is None
    assert evaluation.reason_code == "LATE_EDGE_TOO_SMALL"


def test_evaluate_with_diagnostics_model_a_requires_more_edge_for_expensive_late_conviction(sample_window):
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.86,
        down_price_momentum=0.10,
        down_price_acceleration=0.02,
        strongest_queue_imbalance=0.35,
        strongest_microprice_offset=0.01,
        strongest_depth_to_fill_usdc=14.0,
        weakest_depth_to_fill_usdc=8.0,
        btc_poly_agreement=1.0,
        market_implied_up_probability=0.08,
    )
    evaluation_a = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.11,
        down_market_price=0.88,
        model="A",
        model_context=context,
    )
    evaluation_b = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        18,
        up_market_price=0.11,
        down_market_price=0.88,
        model="B",
        model_context=context,
    )
    assert evaluation_a.signal is None
    assert evaluation_a.reason_code == "LATE_EDGE_TOO_SMALL"
    assert evaluation_b.signal is None
    assert evaluation_b.reason_code == "LATE_EDGE_TOO_SMALL"
    assert "threshold 0.090" in evaluation_a.reason_detail
    assert "threshold 0.070" in evaluation_b.reason_detail


def test_build_hedge_signal_skips_late_conviction_primary():
    primary = evaluate_with_diagnostics(
        WindowState(
            start_ts=0,
            end_ts=300,
            market_id="m1",
            up_token_id="u1",
            down_token_id="d1",
            open_price=82000,
            market_url="https://polymarket.com/event/test",
        ),
        82000.20,
        18,
        up_market_price=0.30,
        down_market_price=0.70,
        model="B",
        model_context=ModelContext(
            strongest_side="DOWN",
            market_conviction=0.84,
            down_price_momentum=0.08,
            down_price_acceleration=0.01,
            btc_poly_agreement=1.0,
        ),
    ).signal
    hedge = build_hedge_signal(primary, up_market_price=0.30, down_market_price=0.70)
    assert hedge is None


def test_build_hedge_signal_blocks_late_conviction_hedge_outside_entry_band():
    # LATE_CONVICTION DOWN at 0.82 → hedge UP would be at 0.35, below ENTRY_MIN (0.55).
    # Even with allow_late_conviction=True, the hedge is blocked by the entry band.
    # We don't buy 35% tokens — too uncertain regardless of hedge intent.
    primary = evaluate_with_diagnostics(
        WindowState(
            start_ts=0,
            end_ts=300,
            market_id="m1",
            up_token_id="u1",
            down_token_id="d1",
            open_price=82000,
            market_url="https://polymarket.com/event/test",
        ),
        82000.20,
        18,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=ModelContext(
            strongest_side="DOWN",
            market_conviction=0.84,
            down_price_momentum=0.08,
            down_price_acceleration=0.01,
            btc_poly_agreement=1.0,
        ),
    ).signal
    hedge = build_hedge_signal(
        primary,
        up_market_price=0.35,
        down_market_price=0.82,
        allow_late_conviction=True,
    )
    assert hedge is None


# ── compute_sl_confirmation_seconds ──────────────────────────────────────────

def _strong_up_context(**overrides) -> ModelContext:
    """ModelContext representing strong UP momentum."""
    defaults = dict(
        directional_consistency=0.80,
        up_price_momentum=0.04,
        down_price_momentum=0.0,
        up_price_acceleration=0.002,
        down_price_acceleration=0.0,
        market_conviction=0.85,
        btc_poly_agreement=1.0,
        strongest_side="UP",
    )
    defaults.update(overrides)
    return ModelContext(**defaults)


def test_no_suppression_when_disabled(monkeypatch):
    import strategy.signal as sig
    monkeypatch.setattr(sig, "MOMENTUM_SL_SUPPRESS_ENABLED", False)
    ctx = _strong_up_context()
    result = compute_sl_confirmation_seconds("UP", ctx, 2.0)
    assert result == 2.0


def test_no_suppression_without_context():
    result = compute_sl_confirmation_seconds("UP", None, 2.0)
    assert result == 2.0


def test_no_suppression_when_acceleration_negative():
    # Negative acceleration = exhaustion/climax — never suppress
    ctx = _strong_up_context(up_price_acceleration=-0.001)
    result = compute_sl_confirmation_seconds("UP", ctx, 2.0)
    assert result == 2.0


def test_no_suppression_when_score_below_threshold():
    # Low directional consistency + no momentum + no BTC agreement → score < 0.65
    ctx = ModelContext(
        directional_consistency=0.5,
        up_price_momentum=0.0,
        market_conviction=0.4,
        btc_poly_agreement=0.0,
        up_price_acceleration=0.001,
    )
    result = compute_sl_confirmation_seconds("UP", ctx, 2.0)
    assert result == 2.0


def test_suppression_extends_window_when_strong_momentum():
    # Strong momentum across all signals → should extend beyond 2s
    ctx = _strong_up_context()
    result = compute_sl_confirmation_seconds("UP", ctx, 2.0)
    assert result > 2.0
    assert result <= 10.0


def test_suppression_capped_at_max_window():
    # Maximum possible score (all weights at 1.0) → must not exceed 10s cap
    ctx = _strong_up_context(
        directional_consistency=1.0,
        up_price_momentum=0.05,
        market_conviction=1.0,
        btc_poly_agreement=1.0,
        up_price_acceleration=0.01,
    )
    result = compute_sl_confirmation_seconds("UP", ctx, 2.0)
    assert result <= 10.0


def test_suppression_uses_correct_direction_signals():
    # DOWN trade should use down_price_momentum, not up_price_momentum
    ctx_down = ModelContext(
        directional_consistency=0.80,
        down_price_momentum=0.04,
        up_price_momentum=0.0,
        down_price_acceleration=0.002,
        up_price_acceleration=0.0,
        market_conviction=0.85,
        btc_poly_agreement=1.0,
        strongest_side="DOWN",
    )
    ctx_up_only = ModelContext(
        directional_consistency=0.80,
        up_price_momentum=0.04,
        down_price_momentum=0.0,
        up_price_acceleration=0.002,
        down_price_acceleration=0.0,
        market_conviction=0.85,
        btc_poly_agreement=1.0,
        strongest_side="UP",
    )
    result_down = compute_sl_confirmation_seconds("DOWN", ctx_down, 2.0)
    result_up_wrong = compute_sl_confirmation_seconds("DOWN", ctx_up_only, 2.0)
    # DOWN context should suppress for a DOWN trade; UP-only context should not
    assert result_down > 2.0
    assert result_up_wrong == 2.0


def test_suppression_btc_disagreement_reduces_score():
    # BTC disagrees (btc_poly_agreement <= 0) → lower score → less extension
    ctx_agree = _strong_up_context(btc_poly_agreement=1.0)
    ctx_disagree = _strong_up_context(btc_poly_agreement=-1.0)
    result_agree = compute_sl_confirmation_seconds("UP", ctx_agree, 2.0)
    result_disagree = compute_sl_confirmation_seconds("UP", ctx_disagree, 2.0)
    assert result_agree >= result_disagree


def test_day_vol_gate_blocks_when_btc_disagrees(sample_window, monkeypatch):
    import strategy.signal as signal_mod

    monkeypatch.setattr(signal_mod, "DAY_VOL_GATE_ENABLED", True)
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        strongest_depth_to_fill_usdc=12.0,
        weakest_depth_to_fill_usdc=6.0,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        90,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=context,
        day_vol_v24_bps=2.0,
    )
    assert evaluation.signal is None
    assert evaluation.reason_code == "DAY_VOL_MOVE_TOO_SMALL"


def test_day_vol_gate_allows_late_aligned_move(sample_window, monkeypatch):
    import strategy.signal as signal_mod

    monkeypatch.setattr(signal_mod, "DAY_VOL_GATE_ENABLED", True)
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="UP",
        market_conviction=0.84,
        up_price_momentum=0.08,
        up_price_acceleration=0.01,
        strongest_depth_to_fill_usdc=12.0,
        weakest_depth_to_fill_usdc=6.0,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82080.00,
        90,
        up_market_price=0.82,
        down_market_price=0.35,
        model="B",
        model_context=context,
        day_vol_v24_bps=2.0,
    )
    assert evaluation.signal is not None
    assert evaluation.signal.entry_regime == "LATE_CONVICTION"


def test_day_vol_gate_noop_when_disabled(sample_window, monkeypatch):
    import strategy.signal as signal_mod

    monkeypatch.setattr(signal_mod, "DAY_VOL_GATE_ENABLED", False)
    sample_window.open_price = 82000
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.84,
        down_price_momentum=0.08,
        down_price_acceleration=0.01,
        strongest_depth_to_fill_usdc=12.0,
        weakest_depth_to_fill_usdc=6.0,
        btc_poly_agreement=1.0,
    )
    evaluation = evaluate_with_diagnostics(
        sample_window,
        82000.40,
        90,
        up_market_price=0.35,
        down_market_price=0.82,
        model="B",
        model_context=context,
        day_vol_v24_bps=2.0,
    )
    assert evaluation.signal is not None
