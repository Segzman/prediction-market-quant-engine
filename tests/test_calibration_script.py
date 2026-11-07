from __future__ import annotations

import json

from scripts.build_model_c_calibration import build_calibration


def test_build_calibration_creates_bucket_probabilities():
    records = [
        {
            "type": "resolution",
            "model_c_bucket": ">0.0010|0-30|0.60-0.69|>=0.85|>=0.0003",
            "time_bin": "0-30",
            "strongest_side": "UP",
            "market_conviction": 0.84,
            "market_implied_up_probability": 0.66,
            "window_end_ts": 100,
            "resolved_up": True,
        },
        {
            "type": "resolution",
            "model_c_bucket": ">0.0010|0-30|0.60-0.69|>=0.85|>=0.0003",
            "time_bin": "0-30",
            "strongest_side": "UP",
            "market_conviction": 0.84,
            "market_implied_up_probability": 0.66,
            "window_end_ts": 101,
            "resolved_up": False,
        },
        {
            "type": "resolution",
            "model_c_bucket": ">0.0010|0-30|0.60-0.69|>=0.85|>=0.0003",
            "time_bin": "0-30",
            "strongest_side": "UP",
            "market_conviction": 0.84,
            "market_implied_up_probability": 0.66,
            "window_end_ts": 102,
            "resolved_up": True,
        },
        {
            "type": "candidate",
            "window_end_ts": 100,
            "direction": "UP",
            "model": "C",
            "probabilities": {"A": 0.61, "B": 0.63, "C": 0.67, "P": 0.65},
            "details": {
                "calibration": {
                    "calibration_regime": "0-30|UP|>=0.80|0.60-0.69",
                    "market_implied_up_probability": 0.66,
                }
            },
        },
        {
            "type": "candidate",
            "window_end_ts": 101,
            "direction": "UP",
            "model": "C",
            "probabilities": {"A": 0.61, "B": 0.63, "C": 0.67, "P": 0.65},
            "details": {
                "calibration": {
                    "calibration_regime": "0-30|UP|>=0.80|0.60-0.69",
                    "market_implied_up_probability": 0.66,
                }
            },
        },
    ]
    calibration = build_calibration(records, min_samples=2)
    bucket = calibration["buckets"][">0.0010|0-30|0.60-0.69|>=0.85|>=0.0003"]
    assert bucket["samples"] == 3
    assert bucket["up_probability"] == 2 / 3
    assert calibration["regime_count"] == 1
    assert calibration["model_metrics"]["C"]["default"]["samples"] == 2
