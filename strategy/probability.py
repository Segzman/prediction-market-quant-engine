from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import NormalDist

from config import MODEL_C_MIN_BUCKET_SAMPLES, MOMENTUM_THRESHOLD

# Shared instance: NormalDist is stateless, no need to rebuild per tick.
_NORMAL_DIST = NormalDist()


@dataclass(frozen=True)
class ModelContext:
    tick_move_pct: float = 0.0
    directional_consistency: float = 0.5
    acceleration_pct: float = 0.0
    up_price_momentum: float = 0.0
    down_price_momentum: float = 0.0
    up_price_acceleration: float = 0.0
    down_price_acceleration: float = 0.0
    spread_change: float = 0.0
    one_sided_ratio: float = 0.0
    midpoint_shift: float = 0.0
    strongest_side: str = "NONE"
    market_conviction: float = 0.0
    btc_poly_agreement: float = 0.0
    up_queue_imbalance: float = 0.0
    down_queue_imbalance: float = 0.0
    up_microprice_offset: float = 0.0
    down_microprice_offset: float = 0.0
    strongest_queue_imbalance: float = 0.0
    strongest_microprice_offset: float = 0.0
    depth_imbalance: float = 0.0
    strongest_depth_to_fill_usdc: float = 0.0
    weakest_depth_to_fill_usdc: float = 0.0
    market_implied_up_probability: float = 0.5


def _clamp_probability(value: float) -> float:
    return min(0.95, max(0.05, value))


def _logit(value: float) -> float:
    clipped = min(1 - 1e-6, max(1e-6, value))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponent = math.exp(-value)
        return 1.0 / (1.0 + exponent)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def bucket_market_conviction(value: float) -> str:
    if value < 0.4:
        return "<0.40"
    if value < 0.65:
        return "0.40-0.64"
    if value < 0.8:
        return "0.65-0.79"
    return ">=0.80"


def build_calibration_regime_key(
    *,
    seconds_remaining: int,
    context: ModelContext | None,
    market_probability: float,
) -> str:
    strongest_side = context.strongest_side if context is not None else "NONE"
    market_conviction = context.market_conviction if context is not None else 0.0
    return "|".join(
        [
            bucket_seconds_remaining(seconds_remaining),
            strongest_side,
            bucket_market_conviction(market_conviction),
            bucket_probability(market_probability),
        ]
    )


def _calibration_samples(bucket: dict | None) -> int:
    if not bucket:
        return 0
    return int(bucket.get("samples", 0) or 0)


def _calibration_probability(bucket: dict | None) -> float | None:
    if not bucket:
        return None
    if "up_probability" not in bucket:
        return None
    return float(bucket["up_probability"])


def _blend_toward_target(
    *,
    baseline: float,
    target: float | None,
    samples: int,
    min_samples: int,
    max_weight: float,
) -> float:
    if target is None or samples <= 0 or min_samples <= 0:
        return baseline
    confidence = min(samples / float(min_samples * 2), 1.0)
    weight = max_weight * confidence
    return _clamp_probability((baseline * (1.0 - weight)) + (target * weight))


def _calibration_metric_weight(
    calibration: dict | None,
    *,
    model: str,
    regime_key: str,
) -> float:
    if calibration is None:
        return 1.0
    model_metrics = calibration.get("model_metrics", {})
    model_metric = model_metrics.get(model, {})
    regime_metrics = model_metric.get("regimes", {}).get(regime_key)
    default_metrics = model_metric.get("default", {})
    metrics = regime_metrics or default_metrics
    if not metrics:
        return 1.0
    brier = float(metrics.get("brier_score", 0.22) or 0.22)
    sample_count = int(metrics.get("samples", 0) or 0)
    sample_scale = min(sample_count / 50.0, 1.0) if sample_count > 0 else 0.5
    quality = max(0.55, min(1.35, 1.18 - (brier * 1.5)))
    return max(0.45, min(1.5, quality * (0.7 + 0.3 * sample_scale)))


def model_a_momentum(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
) -> float:
    if open_price == 0:
        return 0.5

    move_pct = (current_price - open_price) / open_price
    time_factor = 1 - (seconds_remaining / 300)
    # Keep ordinary sub-0.2% moves informative without saturating too early.
    confidence = (abs(move_pct) / MOMENTUM_THRESHOLD) * time_factor * 0.085
    if move_pct >= 0:
        return _clamp_probability(0.5 + confidence)
    return _clamp_probability(0.5 - confidence)


def model_b_brownian(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float,
) -> float:
    if seconds_remaining == 0:
        return 1.0 if current_price >= open_price else 0.0
    if open_price == 0 or volatility == 0:
        return model_a_momentum(open_price, current_price, seconds_remaining)

    drift = math.log(current_price / open_price)
    denominator = volatility * math.sqrt(seconds_remaining)
    if denominator == 0:
        return model_a_momentum(open_price, current_price, seconds_remaining)
    probability = _NORMAL_DIST.cdf(drift / denominator)
    return _clamp_probability(probability)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def model_b_composite(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float,
    context: ModelContext | None = None,
) -> float:
    base = model_b_brownian(open_price, current_price, seconds_remaining, volatility)
    if open_price == 0:
        return base

    move_pct = (current_price - open_price) / open_price
    direction_bias = _sign(move_pct)
    if direction_bias == 0:
        return base

    late_factor = max(0.0, 1 - (seconds_remaining / 300))
    window_strength = min(abs(move_pct) / (MOMENTUM_THRESHOLD * 4), 1.5)
    adjustment = direction_bias * (0.06 + 0.04 * late_factor) * window_strength

    if context is not None:
        consistency = max(0.0, min(1.0, (context.directional_consistency - 0.5) * 2))
        tick_bias = _sign(context.tick_move_pct)
        tick_strength = min(abs(context.tick_move_pct) / MOMENTUM_THRESHOLD, 1.5) * consistency
        if tick_bias == direction_bias:
            adjustment += direction_bias * 0.05 * tick_strength
        elif tick_bias != 0:
            adjustment -= direction_bias * 0.05 * tick_strength

        accel_bias = _sign(context.acceleration_pct)
        accel_strength = min(abs(context.acceleration_pct) / MOMENTUM_THRESHOLD, 1.0)
        if accel_bias == direction_bias:
            adjustment += direction_bias * 0.025 * accel_strength
        elif accel_bias != 0:
            adjustment -= direction_bias * 0.025 * accel_strength

        conviction_bias = 0
        if context.strongest_side == "UP":
            conviction_bias = 1
        elif context.strongest_side == "DOWN":
            conviction_bias = -1
        conviction_strength = min(max(context.market_conviction, 0.0), 1.0)
        if conviction_bias == direction_bias:
            adjustment += direction_bias * 0.055 * conviction_strength
        elif conviction_bias != 0:
            adjustment -= direction_bias * 0.045 * conviction_strength

        supportive_poly_move = (
            context.up_price_momentum if direction_bias > 0 else context.down_price_momentum
        )
        opposing_poly_move = (
            context.down_price_momentum if direction_bias > 0 else context.up_price_momentum
        )
        adjustment += direction_bias * min(max(supportive_poly_move, 0.0) * 8.0, 0.05)
        adjustment -= direction_bias * min(max(opposing_poly_move, 0.0) * 8.0, 0.04)

        supportive_poly_accel = (
            context.up_price_acceleration
            if direction_bias > 0
            else context.down_price_acceleration
        )
        adjustment += direction_bias * min(max(supportive_poly_accel, 0.0) * 8.0, 0.025)
        supportive_queue_imbalance = (
            context.up_queue_imbalance if direction_bias > 0 else context.down_queue_imbalance
        )
        opposing_queue_imbalance = (
            context.down_queue_imbalance if direction_bias > 0 else context.up_queue_imbalance
        )
        adjustment += direction_bias * max(supportive_queue_imbalance, 0.0) * 0.035
        adjustment -= direction_bias * max(opposing_queue_imbalance, 0.0) * 0.025
        supportive_microprice_offset = (
            context.up_microprice_offset if direction_bias > 0 else context.down_microprice_offset
        )
        adjustment += direction_bias * max(supportive_microprice_offset * 40.0, 0.0) * 0.03
        if context.strongest_side == "UP":
            depth_support = context.depth_imbalance
        elif context.strongest_side == "DOWN":
            depth_support = -context.depth_imbalance
        else:
            depth_support = 0.0
        if conviction_bias == direction_bias:
            adjustment += direction_bias * max(depth_support, 0.0) * 0.03
        elif conviction_bias != 0:
            adjustment -= direction_bias * max(depth_support, 0.0) * 0.02
        if context.btc_poly_agreement < 0:
            adjustment -= direction_bias * 0.03 * conviction_strength
        if context.one_sided_ratio > 0.35:
            adjustment -= direction_bias * 0.02 * min(context.one_sided_ratio, 1.0)
        if context.spread_change > 0.02:
            adjustment -= direction_bias * 0.01

    return _clamp_probability(base + adjustment)


def model_c_calibrated(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float = 0.0,
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    min_bucket_samples: int = MODEL_C_MIN_BUCKET_SAMPLES,
    context: ModelContext | None = None,
    baseline: float | None = None,
) -> float:
    if open_price == 0:
        return 0.5

    market_probability = (
        context.market_implied_up_probability if context is not None else 0.5
    )
    if baseline is None:
        baseline = model_b_composite(
            open_price,
            current_price,
            seconds_remaining,
            volatility,
            context=context,
        )
    baseline_probability = baseline
    move_pct = (current_price - open_price) / open_price
    move_bin = bucket_move_pct(move_pct)
    time_bin = bucket_seconds_remaining(seconds_remaining)
    meta_bucket_key = build_model_c_bucket_key(
        move_bin=move_bin,
        time_bin=time_bin,
        base_prob_bin=bucket_probability(baseline_probability),
        directional_consistency_bin=bucket_directional_consistency(
            context.directional_consistency if context is not None else 0.5
        ),
        acceleration_bin=bucket_acceleration(
            context.acceleration_pct if context is not None else 0.0
        ),
    )
    legacy_bucket_key = f"{move_bin}|{time_bin}"
    regime_key = build_calibration_regime_key(
        seconds_remaining=seconds_remaining,
        context=context,
        market_probability=market_probability,
    )

    calibration = load_model_c_calibration(calibration_path)
    if calibration is not None:
        regime_bucket = calibration.get("regimes", {}).get(regime_key)
        regime_probability = _calibration_probability(regime_bucket)
        regime_samples = _calibration_samples(regime_bucket)
        bucket = calibration.get("buckets", {}).get(meta_bucket_key)
        if bucket and int(bucket.get("samples", 0)) >= min_bucket_samples:
            probability = _blend_toward_target(
                baseline=baseline_probability,
                target=_calibration_probability(bucket),
                samples=_calibration_samples(bucket),
                min_samples=min_bucket_samples,
                max_weight=0.8,
            )
            probability = _blend_toward_target(
                baseline=probability,
                target=regime_probability,
                samples=regime_samples,
                min_samples=min_bucket_samples,
                max_weight=0.25,
            )
            return _clamp_probability(probability)
        bucket = calibration.get("buckets", {}).get(legacy_bucket_key)
        if bucket and int(bucket.get("samples", 0)) >= min_bucket_samples:
            probability = _blend_toward_target(
                baseline=baseline_probability,
                target=_calibration_probability(bucket),
                samples=_calibration_samples(bucket),
                min_samples=min_bucket_samples,
                max_weight=0.65,
            )
            probability = _blend_toward_target(
                baseline=probability,
                target=regime_probability,
                samples=regime_samples,
                min_samples=min_bucket_samples,
                max_weight=0.25,
            )
            return _clamp_probability(probability)
        if regime_samples >= min_bucket_samples and regime_probability is not None:
            return _blend_toward_target(
                baseline=baseline_probability,
                target=regime_probability,
                samples=regime_samples,
                min_samples=min_bucket_samples,
                max_weight=0.4,
            )

    if not ledger_path:
        return baseline_probability
    path = Path(ledger_path)
    if not path.exists():
        return baseline_probability

    bucket_total = 0
    bucket_up = 0
    legacy_total = 0
    legacy_up = 0
    for record in _read_jsonl(path):
        if record.get("type") != "resolution":
            continue
        if record.get("model_c_bucket") == meta_bucket_key:
            bucket_total += 1
            if record.get("resolved_up"):
                bucket_up += 1
        if record.get("move_pct_bin") == move_bin and record.get("time_bin") == time_bin:
            legacy_total += 1
            if record.get("resolved_up"):
                legacy_up += 1

    if bucket_total < min_bucket_samples:
        bucket_total, bucket_up = legacy_total, legacy_up

    if bucket_total < min_bucket_samples:
        return baseline_probability
    return _clamp_probability(bucket_up / bucket_total)


def pooled_probability(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float = 0.0,
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    context: ModelContext | None = None,
) -> float:
    probability_a = model_a_momentum(open_price, current_price, seconds_remaining)
    probability_b = model_b_composite(
        open_price,
        current_price,
        seconds_remaining,
        volatility,
        context=context,
    )
    probability_c = model_c_calibrated(
        open_price,
        current_price,
        seconds_remaining,
        volatility,
        ledger_path=ledger_path,
        calibration_path=calibration_path,
        context=context,
        baseline=probability_b,
    )
    market_probability = (
        context.market_implied_up_probability
        if context is not None
        else 0.5
    )
    market_probability = _clamp_probability(market_probability)
    regime_key = build_calibration_regime_key(
        seconds_remaining=seconds_remaining,
        context=context,
        market_probability=market_probability,
    )
    calibration = load_model_c_calibration(calibration_path)

    weights = {
        "A": 0.9,
        "B": 1.05,
        "C": 1.2,
        "MKT": 0.55,
    }
    time_bin = bucket_seconds_remaining(seconds_remaining)
    if time_bin == "0-30":
        weights["B"] += 0.1
        weights["C"] += 0.15
        weights["MKT"] += 0.05
    elif time_bin == "60-90":
        weights["A"] += 0.15

    extreme_market = False
    weak_strong_side_book = False
    if context is not None:
        if context.directional_consistency >= 0.8:
            weights["B"] += 0.1
        if context.market_conviction >= 0.75:
            weights["C"] += 0.1
        if context.btc_poly_agreement < 0:
            weights["MKT"] *= 0.8

        weak_strong_side_book = (
            context.strongest_depth_to_fill_usdc > 0
            and context.strongest_depth_to_fill_usdc < 5.0
        ) or context.one_sided_ratio > 0.35
        extreme_market = market_probability >= 0.85 or market_probability <= 0.15
        if extreme_market:
            weights["MKT"] = min(weights["MKT"], 0.35 if weak_strong_side_book else 0.5)
        if weak_strong_side_book:
            weights["MKT"] *= 0.85

    for model_key in ("A", "B", "C"):
        weights[model_key] *= _calibration_metric_weight(
            calibration,
            model=model_key,
            regime_key=regime_key,
        )

    logits = [
        weights["A"] * _logit(probability_a),
        weights["B"] * _logit(probability_b),
        weights["C"] * _logit(probability_c),
        weights["MKT"] * _logit(market_probability),
    ]
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return probability_c
    pooled = _clamp_probability(_sigmoid(sum(logits) / total_weight))
    if extreme_market and weak_strong_side_book:
        pooled = 0.5 + ((pooled - 0.5) * 0.82)
    return _clamp_probability(pooled)


def get_probability(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    volatility: float = 0.0,
    model: str = "A",
    *,
    ledger_path: str | None = None,
    calibration_path: str | None = None,
    context: ModelContext | None = None,
) -> float:
    choice = model.upper()
    if choice == "P":
        return pooled_probability(
            open_price,
            current_price,
            seconds_remaining,
            volatility,
            ledger_path=ledger_path,
            calibration_path=calibration_path,
            context=context,
        )
    if choice == "B":
        return model_b_composite(
            open_price,
            current_price,
            seconds_remaining,
            volatility,
            context=context,
        )
    if choice == "C":
        return model_c_calibrated(
            open_price,
            current_price,
            seconds_remaining,
            volatility,
            ledger_path=ledger_path,
            calibration_path=calibration_path,
            context=context,
        )
    return model_a_momentum(open_price, current_price, seconds_remaining)


def bucket_move_pct(move_pct: float) -> str:
    thresholds = [-0.001, -0.0005, 0.0, 0.0005, 0.001]
    for threshold in thresholds:
        if move_pct <= threshold:
            return f"<={threshold:.4f}"
    return ">0.0010"


def bucket_seconds_remaining(seconds_remaining: int) -> str:
    if seconds_remaining <= 30:
        return "0-30"
    if seconds_remaining <= 60:
        return "30-60"
    return "60-90"


def bucket_probability(probability: float) -> str:
    if probability < 0.4:
        return "<0.40"
    if probability < 0.5:
        return "0.40-0.49"
    if probability < 0.6:
        return "0.50-0.59"
    if probability < 0.7:
        return "0.60-0.69"
    if probability < 0.8:
        return "0.70-0.79"
    return ">=0.80"


def bucket_directional_consistency(value: float) -> str:
    if value < 0.55:
        return "<0.55"
    if value < 0.7:
        return "0.55-0.69"
    if value < 0.85:
        return "0.70-0.84"
    return ">=0.85"


def bucket_acceleration(value: float) -> str:
    if value <= -0.0003:
        return "<=-0.0003"
    if value < 0:
        return "-0.0003-0"
    if value < 0.0003:
        return "0-0.0003"
    return ">=0.0003"


def build_model_c_bucket_key(
    *,
    move_bin: str,
    time_bin: str,
    base_prob_bin: str,
    directional_consistency_bin: str,
    acceleration_bin: str,
) -> str:
    return "|".join(
        [
            move_bin,
            time_bin,
            base_prob_bin,
            directional_consistency_bin,
            acceleration_bin,
        ]
    )


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


@lru_cache(maxsize=8)
def _load_json(path_str: str) -> dict | None:
    path = Path(path_str)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_model_c_calibration(path_str: str | None) -> dict | None:
    if not path_str:
        return None
    return _load_json(path_str)
