from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from strategy.probability import build_calibration_regime_key


def load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_calibration(records: list[dict], *, min_samples: int) -> dict:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"samples": 0, "up_count": 0}
    )
    regimes: dict[str, dict[str, float]] = defaultdict(
        lambda: {"samples": 0, "up_count": 0}
    )
    model_metrics: dict[str, dict] = defaultdict(
        lambda: {
            "default": {"samples": 0, "brier_sum": 0.0, "log_loss_sum": 0.0},
            "regimes": defaultdict(lambda: {"samples": 0, "brier_sum": 0.0, "log_loss_sum": 0.0}),
        }
    )
    window_outcomes: dict[int, bool] = {}

    for record in records:
        if (
            record.get("type") == "resolution"
            and record.get("window_end_ts") is not None
            and record.get("resolved_up") is not None
        ):
            window_outcomes[int(record["window_end_ts"])] = bool(record["resolved_up"])

    for record in records:
        if record.get("type") != "resolution":
            continue
        bucket_key = record.get("model_c_bucket")
        if not bucket_key:
            move_pct_bin = record.get("move_pct_bin")
            time_bin = record.get("time_bin")
            if not move_pct_bin or not time_bin:
                continue
            bucket_key = f"{move_pct_bin}|{time_bin}"
        bucket = buckets[bucket_key]
        bucket["samples"] += 1
        if record.get("resolved_up"):
            bucket["up_count"] += 1

        time_bin = record.get("time_bin")
        if time_bin:
            regime_key = build_calibration_regime_key(
                seconds_remaining=15 if time_bin == "0-30" else 45 if time_bin == "30-60" else 75,
                context=type(
                    "CalibrationContext",
                    (),
                    {
                        "strongest_side": record.get("strongest_side", "NONE"),
                        "market_conviction": float(record.get("market_conviction", 0.0) or 0.0),
                    },
                )(),
                market_probability=float(
                    record.get("market_implied_up_probability", record.get("up_market_price", 0.5))
                    or 0.5
                ),
            )
            regime = regimes[regime_key]
            regime["samples"] += 1
            if record.get("resolved_up"):
                regime["up_count"] += 1

    for record in records:
        if record.get("type") != "candidate":
            continue
        window_end_ts = record.get("window_end_ts")
        direction = str(record.get("direction") or "")
        if window_end_ts is None or direction not in {"UP", "DOWN"}:
            continue
        outcome = window_outcomes.get(int(window_end_ts))
        if outcome is None:
            continue
        won = (direction == "UP" and outcome) or (direction == "DOWN" and not outcome)
        regime_key = (
            ((record.get("details") or {}).get("calibration") or {}).get("calibration_regime")
            or build_calibration_regime_key(
                seconds_remaining=int(record.get("seconds_remaining", 45) or 45),
                context=type(
                    "CalibrationContext",
                    (),
                    {
                        "strongest_side": record.get("strongest_side", "NONE"),
                        "market_conviction": float(record.get("market_conviction", 0.0) or 0.0),
                    },
                )(),
                market_probability=float(
                    (((record.get("details") or {}).get("calibration") or {}).get(
                        "market_implied_up_probability",
                        0.5,
                    )
                    or 0.5)
                ),
            )
        )
        probabilities = dict(record.get("probabilities") or {})
        if record.get("selected_probability") is not None:
            probabilities.setdefault(str(record.get("model", "UNKNOWN")), record.get("selected_probability"))
        for model, probability in probabilities.items():
            clipped_probability = min(1 - 1e-6, max(1e-6, float(probability)))
            default_metric = model_metrics[str(model)]["default"]
            default_metric["samples"] += 1
            default_metric["brier_sum"] += (clipped_probability - (1.0 if won else 0.0)) ** 2
            default_metric["log_loss_sum"] += -math.log(
                clipped_probability if won else (1.0 - clipped_probability)
            )
            regime_metric = model_metrics[str(model)]["regimes"][regime_key]
            regime_metric["samples"] += 1
            regime_metric["brier_sum"] += (clipped_probability - (1.0 if won else 0.0)) ** 2
            regime_metric["log_loss_sum"] += -math.log(
                clipped_probability if won else (1.0 - clipped_probability)
            )

    normalized: dict[str, dict[str, float]] = {}
    for bucket_key, stats in buckets.items():
        if stats["samples"] < min_samples:
            continue
        normalized[bucket_key] = {
            "samples": stats["samples"],
            "up_count": stats["up_count"],
            "up_probability": stats["up_count"] / stats["samples"],
            "beta_alpha": stats["up_count"] + 1,
            "beta_beta": (stats["samples"] - stats["up_count"]) + 1,
        }

    normalized_regimes: dict[str, dict[str, float]] = {}
    for regime_key, stats in regimes.items():
        if stats["samples"] < min_samples:
            continue
        normalized_regimes[regime_key] = {
            "samples": stats["samples"],
            "up_count": stats["up_count"],
            "up_probability": stats["up_count"] / stats["samples"],
            "beta_alpha": stats["up_count"] + 1,
            "beta_beta": (stats["samples"] - stats["up_count"]) + 1,
        }

    normalized_model_metrics: dict[str, dict] = {}
    for model, stats in model_metrics.items():
        default_stats = stats["default"]
        normalized_model_metrics[model] = {
            "default": {
                "samples": default_stats["samples"],
                "brier_score": (
                    default_stats["brier_sum"] / default_stats["samples"]
                    if default_stats["samples"]
                    else 0.0
                ),
                "log_loss": (
                    default_stats["log_loss_sum"] / default_stats["samples"]
                    if default_stats["samples"]
                    else 0.0
                ),
            },
            "regimes": {},
        }
        for regime_key, regime_stats in stats["regimes"].items():
            if regime_stats["samples"] < min_samples:
                continue
            normalized_model_metrics[model]["regimes"][regime_key] = {
                "samples": regime_stats["samples"],
                "brier_score": regime_stats["brier_sum"] / regime_stats["samples"],
                "log_loss": regime_stats["log_loss_sum"] / regime_stats["samples"],
            }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_bucket_samples": min_samples,
        "bucket_count": len(normalized),
        "regime_count": len(normalized_regimes),
        "buckets": normalized,
        "regimes": normalized_regimes,
        "model_metrics": normalized_model_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="trades.jsonl")
    parser.add_argument("--output", default="model_c_calibration.json")
    parser.add_argument("--min-samples", type=int, default=10)
    args = parser.parse_args()

    ledger_path = Path(args.ledger)
    output_path = Path(args.output)
    records = load_jsonl(ledger_path)
    calibration = build_calibration(records, min_samples=args.min_samples)
    output_path.write_text(json.dumps(calibration, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"Wrote calibration with {calibration['bucket_count']} buckets to {output_path}"
    )


if __name__ == "__main__":
    main()
