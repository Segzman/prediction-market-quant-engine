import json
import os

import pytest

from strategy import probability as probability_mod
from strategy.probability import (
    ModelContext,
    bucket_acceleration,
    bucket_directional_consistency,
    bucket_move_pct,
    bucket_probability,
    bucket_seconds_remaining,
    build_calibration_regime_key,
    build_model_c_bucket_key,
    get_probability,
    model_a_momentum,
    model_b_brownian,
    model_b_composite,
    model_c_calibrated,
)


def test_model_a_returns_half_at_zero_move():
    assert model_a_momentum(82000, 82000, 150) == 0.5


def test_model_a_skews_above_half_on_positive_move():
    prob = model_a_momentum(82000, 82082, 60)
    assert prob > 0.5


def test_model_a_skews_below_half_on_negative_move():
    prob = model_a_momentum(82000, 81918, 60)
    assert prob < 0.5


def test_model_a_more_confident_with_less_time():
    prob_90s = model_a_momentum(82000, 82082, 90)
    prob_30s = model_a_momentum(82000, 82082, 30)
    assert prob_30s > prob_90s


def test_model_a_caps_at_0_95():
    prob = model_a_momentum(82000, 83000, 10)
    assert prob <= 0.95


def test_model_a_floors_at_0_05():
    prob = model_a_momentum(82000, 81000, 10)
    assert prob >= 0.05


def test_model_a_returns_half_when_open_price_zero():
    assert model_a_momentum(0, 82000, 60) == 0.5


def test_model_b_returns_half_at_zero_drift_and_nonzero_vol():
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
    assert model_b_brownian(82000, 82001, 0, 0.0001) == 1.0
    assert model_b_brownian(82000, 81999, 0, 0.0001) == 0.0


def test_model_b_higher_vol_widens_to_half():
    prob_low_vol = model_b_brownian(82000, 82082, 60, volatility=0.00001)
    prob_high_vol = model_b_brownian(82000, 82082, 60, volatility=0.01)
    assert abs(prob_high_vol - 0.5) < abs(prob_low_vol - 0.5)


def test_model_b_composite_boosts_supportive_tick_trend():
    baseline = model_b_brownian(82000, 82020, 20, volatility=0.0003)
    boosted = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            tick_move_pct=0.0005,
            directional_consistency=0.9,
            acceleration_pct=0.0003,
        ),
    )
    assert boosted > baseline


def test_model_b_composite_dampens_contradictory_tick_trend():
    baseline = model_b_brownian(82000, 82020, 20, volatility=0.0003)
    damped = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            tick_move_pct=-0.0005,
            directional_consistency=0.9,
            acceleration_pct=-0.0003,
        ),
    )
    assert damped < baseline


def test_model_b_composite_responds_to_polymarket_conviction():
    baseline = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(),
    )
    conviction_up = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            strongest_side="UP",
            market_conviction=0.82,
            up_price_momentum=0.08,
            up_price_acceleration=0.01,
            btc_poly_agreement=1.0,
        ),
    )
    conviction_down = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            strongest_side="DOWN",
            market_conviction=0.82,
            down_price_momentum=0.08,
            down_price_acceleration=0.01,
            btc_poly_agreement=-1.0,
        ),
    )
    assert conviction_up > baseline
    assert conviction_down < baseline


def test_model_b_composite_responds_to_supportive_size_imbalance():
    baseline = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(),
    )
    supportive = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            strongest_side="UP",
            market_conviction=0.75,
            up_queue_imbalance=0.6,
            up_microprice_offset=0.01,
            depth_imbalance=0.5,
        ),
    )
    opposing = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=ModelContext(
            strongest_side="DOWN",
            market_conviction=0.75,
            down_queue_imbalance=0.6,
            down_microprice_offset=0.01,
            depth_imbalance=-0.5,
        ),
    )
    assert supportive > baseline
    assert opposing < baseline


def test_model_c_uses_calibration_file_when_available(tmp_path):
    context = ModelContext(
        tick_move_pct=0.0005,
        directional_consistency=0.9,
        acceleration_pct=0.0004,
        strongest_side="UP",
        market_conviction=0.82,
        market_implied_up_probability=0.66,
    )
    baseline = model_b_composite(
        100.0,
        100.2,
        27,
        volatility=0.0003,
        context=context,
    )
    bucket_key = build_model_c_bucket_key(
        move_bin=bucket_move_pct((100.2 - 100.0) / 100.0),
        time_bin=bucket_seconds_remaining(27),
        base_prob_bin=bucket_probability(baseline),
        directional_consistency_bin=bucket_directional_consistency(context.directional_consistency),
        acceleration_bin=bucket_acceleration(context.acceleration_pct),
    )
    regime_key = build_calibration_regime_key(
        seconds_remaining=27,
        context=context,
        market_probability=context.market_implied_up_probability,
    )
    calibration_path = tmp_path / "model_c_calibration.json"
    calibration_path.write_text(
        json.dumps(
            {
                "buckets": {bucket_key: {"samples": 20, "up_probability": 0.81}},
                "regimes": {regime_key: {"samples": 20, "up_probability": 0.77}},
            }
        ),
        encoding="utf-8",
    )
    prob = get_probability(
        100.0,
        100.2,
        27,
        volatility=0.0003,
        model="C",
        calibration_path=str(calibration_path),
        context=context,
    )
    assert prob == pytest.approx(0.821)


def test_model_c_uses_regime_calibration_when_bucket_missing(tmp_path):
    context = ModelContext(
        strongest_side="DOWN",
        market_conviction=0.74,
        market_implied_up_probability=0.36,
    )
    calibration_path = tmp_path / "model_c_calibration.json"
    regime_key = build_calibration_regime_key(
        seconds_remaining=18,
        context=context,
        market_probability=context.market_implied_up_probability,
    )
    calibration_path.write_text(
        json.dumps(
            {
                "buckets": {},
                "regimes": {regime_key: {"samples": 25, "up_probability": 0.32}},
            }
        ),
        encoding="utf-8",
    )
    prob = get_probability(
        100.0,
        99.9,
        18,
        volatility=0.0004,
        model="C",
        calibration_path=str(calibration_path),
        context=context,
    )
    assert prob < 0.5


def test_model_c_falls_back_to_composite_b_without_calibration():
    context = ModelContext(
        tick_move_pct=0.0005,
        directional_consistency=0.9,
        acceleration_pct=0.0003,
    )
    prob_c = get_probability(
        82000,
        82020,
        20,
        volatility=0.0003,
        model="C",
        context=context,
    )
    prob_b = model_b_composite(
        82000,
        82020,
        20,
        volatility=0.0003,
        context=context,
    )
    assert prob_c == prob_b


def test_pooled_probability_stays_bounded_and_respects_extreme_market_caution():
    supportive_context = ModelContext(
        tick_move_pct=0.0006,
        directional_consistency=0.9,
        strongest_side="UP",
        market_conviction=0.8,
        strongest_depth_to_fill_usdc=12.0,
        market_implied_up_probability=0.91,
    )
    weak_book_context = ModelContext(
        tick_move_pct=0.0006,
        directional_consistency=0.9,
        strongest_side="UP",
        market_conviction=0.8,
        strongest_depth_to_fill_usdc=2.5,
        one_sided_ratio=0.4,
        market_implied_up_probability=0.91,
    )

    supportive = get_probability(100.0, 100.2, 18, volatility=0.0003, model="P", context=supportive_context)
    weak_book = get_probability(100.0, 100.2, 18, volatility=0.0003, model="P", context=weak_book_context)

    assert 0.05 <= supportive <= 0.95
    assert 0.05 <= weak_book <= 0.95
    assert weak_book < supportive


def _model_c_meta_key() -> str:
    return build_model_c_bucket_key(
        move_bin=bucket_move_pct(0.002),
        time_bin=bucket_seconds_remaining(150),
        base_prob_bin=bucket_probability(0.6),
        directional_consistency_bin=bucket_directional_consistency(0.5),
        acceleration_bin=bucket_acceleration(0.0),
    )


def _model_c_ledger_probability(ledger_path):
    return model_c_calibrated(
        100.0,
        100.2,
        150,
        ledger_path=str(ledger_path),
        baseline=0.6,
        min_bucket_samples=1,
        decision_ts=1250,
    )


def test_model_c_ledger_fallback_excludes_resolutions_after_decision_ts(tmp_path):
    meta_key = _model_c_meta_key()
    ledger = tmp_path / "trades.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1100,
                        "resolved_up": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1200,
                        "resolved_up": False,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1250,
                        "resolved_up": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1500,
                        "resolved_up": True,
                    }
                ),
                json.dumps({"type": "resolution", "model_c_bucket": meta_key, "resolved_up": True}),
                json.dumps({"type": "order_intent", "model_c_bucket": meta_key, "window_end_ts": 1100}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    probability = _model_c_ledger_probability(ledger)

    # Only the 1100 and 1200 resolutions count (1 of 2 up). The boundary
    # record ending exactly at the decision time and the later records,
    # including the one missing window_end_ts, are excluded.
    assert probability == pytest.approx(0.5)


def test_model_c_ledger_cache_rereads_only_after_mtime_change(tmp_path, monkeypatch):
    meta_key = _model_c_meta_key()
    ledger = tmp_path / "trades.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1100,
                        "resolved_up": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1200,
                        "resolved_up": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1150,
                        "resolved_up": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(ledger, (1000, 1000))

    read_calls: list[str] = []
    real_read_jsonl = probability_mod._read_jsonl

    def counting_read_jsonl(path):
        read_calls.append(str(path))
        return real_read_jsonl(path)

    monkeypatch.setattr(probability_mod, "_read_jsonl", counting_read_jsonl)

    assert _model_c_ledger_probability(ledger) == pytest.approx(0.95)
    assert _model_c_ledger_probability(ledger) == pytest.approx(0.95)
    assert len(read_calls) == 1

    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1100,
                        "resolved_up": True,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1200,
                        "resolved_up": False,
                    }
                ),
                json.dumps(
                    {
                        "type": "resolution",
                        "model_c_bucket": meta_key,
                        "window_end_ts": 1150,
                        "resolved_up": False,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(ledger, (2000, 2000))

    assert _model_c_ledger_probability(ledger) == pytest.approx(1 / 3)
    assert len(read_calls) == 2
