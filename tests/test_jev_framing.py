"""Tests for research/jev_framing: metrics, cache/budget, and feature reconstruction.
No network: the API caller is a fake."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

FRAMING_DIR = Path(__file__).resolve().parents[1] / "research" / "jev_framing"
if str(FRAMING_DIR) not in sys.path:
    sys.path.insert(0, str(FRAMING_DIR))

import dataset  # noqa: E402
import run  # noqa: E402


def test_metrics_hand_computed():
    rows = [
        {"p": 0.9, "agree": True, "det": True, "lat_ms": 100.0, "src": "api"},
        {"p": 0.2, "agree": False, "det": False, "lat_ms": 300.0, "src": "api"},
        {"p": 0.7, "agree": False, "det": True, "lat_ms": 200.0, "src": "cache"},
        {"p": None, "agree": True, "det": True, "lat_ms": None, "src": "error"},
    ]
    m = run.metrics(rows)
    assert m["n_states"] == 4 and m["n_scored"] == 3
    assert m["brier"] == round((0.01 + 0.04 + 0.49) / 3, 4)
    assert m["log_loss"] == round((-math.log(0.9) - math.log(0.8) - math.log(0.3)) / 3, 4)
    assert m["acc_at_0.5"] == round(2 / 3, 4)
    assert m["acc_at_0.60"] == round(2 / 3, 4)
    assert m["confirm_rate_at_0.60"] == round(2 / 3, 4)
    assert m["precision_at_0.60"] == 0.5
    assert m["det_agreement"] == 1.0  # all three scored states match det
    assert m["error_rate"] == round(1 / 3, 4)  # 1 error out of 3 attempted calls
    assert m["api_calls"] == 3 and m["cache_hits"] == 1
    assert m["latency_p50_ms"] == 200.0
    bins = {b["bin"]: b["n"] for b in m["calibration"]}
    assert bins == {"0.0-0.2": 0, "0.2-0.4": 1, "0.4-0.6": 0, "0.6-0.8": 1, "0.8-1.0": 1}


def test_metrics_empty_has_no_scores():
    m = run.metrics([{"p": None, "agree": True, "det": True, "lat_ms": None, "src": "budget"}])
    assert m["n_scored"] == 0 and "brier" not in m and m["budget_skipped"] == 1


def test_to_agreement_modes():
    assert run.to_agreement(0.8, "UP", "agreement") == 0.8
    assert run.to_agreement(0.8, "DOWN", "p_up") == pytest.approx(0.2)
    assert run.to_agreement(0.8, "UP", "p_up") == 0.8
    assert run.to_agreement(0.8, "UP", "negated") == pytest.approx(0.2)
    assert run.to_agreement(1.7, "UP", "agreement") == 1.0


def test_extract_probability_fields_match_production():
    assert run.extract_probability({"noul": True}) == 1.0
    assert run.extract_probability({"probability": 1.4}) == 1.0
    assert run.extract_probability({"value": -0.2}) == 0.0
    assert run.extract_probability({}) is None


def test_percentile_nearest_rank():
    assert run.percentile([1, 2, 3, 4], 50) == 2
    assert run.percentile([1, 2, 3, 4], 95) == 4
    assert run.percentile([], 50) is None


def test_sample_evenly_is_deterministic_and_spans_range():
    items = list(range(100))
    s = run.sample_evenly(items, 5)
    assert s == [0, 25, 50, 74, 99]
    assert run.sample_evenly(items, None) == items


def test_cache_key_depends_on_payload_and_repeat():
    framing = {"state_fields": ["tick_move_pct"], "instructions": "x {strongest_side}"}
    st = {"tick_move_pct": 0.1, "strongest_side": "UP"}
    k0 = run.cache_key(run.request_payload(framing, st), 0)
    assert k0 == run.cache_key(run.request_payload(framing, st), 0)
    assert k0 != run.cache_key(run.request_payload(framing, st), 1)
    other = dict(st, strongest_side="DOWN")
    assert k0 != run.cache_key(run.request_payload(framing, other), 0)


def _runner(tmp_path, limit, caller):
    cache = run.Cache(tmp_path / "responses.jsonl")
    budget = run.Budget(tmp_path / "budget.json", limit)
    return run.Runner(cache, budget, key_getter=lambda: "k", caller=caller), cache, budget


FRAMING = {
    "state_fields": ["tick_move_pct"],
    "instructions": "q {strongest_side}",
    "answer_mode": "agreement",
}


def test_cache_hit_is_free_and_persists(tmp_path):
    calls = []

    def caller(payload, key):
        calls.append(payload)
        return 0.7, 12.0

    runner, _, budget = _runner(tmp_path, 10, caller)
    st = {"id": 1, "tick_move_pct": 0.1, "strongest_side": "UP"}
    first = runner.score_state(FRAMING, st)
    second = runner.score_state(FRAMING, st)
    assert first["src"] == "api" and second["src"] == "cache"
    assert len(calls) == 1 and budget.used == 1
    # a new process sees the cache on disk
    runner2, _, budget2 = _runner(tmp_path, 10, caller)
    assert runner2.score_state(FRAMING, st)["src"] == "cache"
    assert budget2.used == 1 and len(calls) == 1


def test_budget_stops_calls_and_persists(tmp_path):
    calls = []

    def caller(payload, key):
        calls.append(1)
        return 0.5, 1.0

    runner, _, budget = _runner(tmp_path, 2, caller)
    srcs = [
        runner.score_state(FRAMING, {"id": i, "tick_move_pct": float(i), "strongest_side": "UP"})["src"]
        for i in range(4)
    ]
    assert srcs == ["api", "api", "budget", "budget"]
    assert len(calls) == 2
    assert run.Budget(tmp_path / "budget.json", 2).used == 2


def test_errors_are_not_cached_but_count_against_budget(tmp_path):
    def caller(payload, key):
        raise TimeoutError("slow")

    runner, cache, budget = _runner(tmp_path, 5, caller)
    out = runner.score_state(FRAMING, {"id": 0, "tick_move_pct": 0.0, "strongest_side": "UP"})
    assert out["src"] == "error" and out["p"] is None
    assert cache.data == {} and budget.used == 1


def test_missing_key_raises_before_any_call(tmp_path):
    calls = []

    def caller(payload, key):
        calls.append(1)
        return 0.5, 1.0

    cache = run.Cache(tmp_path / "r.jsonl")
    budget = run.Budget(tmp_path / "b.json", 5)

    def no_key():
        raise SystemExit("no key")

    runner = run.Runner(cache, budget, key_getter=no_key, caller=caller)
    with pytest.raises(SystemExit):
        runner.score_state(FRAMING, {"id": 0, "tick_move_pct": 0.0, "strongest_side": "UP"})
    assert calls == []


def test_bootstrap_zero_difference():
    point, lo, hi = run.bootstrap_brier_diff([0.1, 0.4, 0.2], [0.1, 0.4, 0.2], [1, 1, 2], reps=200)
    assert point == 0.0 and lo == 0.0 and hi == 0.0


def test_reconstruct_features_consistency_and_lookback():
    prior = [
        {"ts": 1000.0, "btc_price": 101.0, "up_price": 0.50, "down_price": 0.50},
        {"ts": 1010.0, "btc_price": 102.0, "up_price": 0.52, "down_price": 0.48},
        {"ts": 1020.0, "btc_price": 99.0, "up_price": 0.45, "down_price": 0.55},
    ]
    rec = {"ts": 1040.0, "btc_price": 103.0, "open_price": 100.0, "up_price": 0.60, "down_price": 0.40}
    f = dataset.reconstruct_features(prior, rec, prior)
    assert f is not None
    # 3 of 4 prices (incl. now) are above open: 101,102,99 -> moves +1,+2,-1,+3
    assert f["directional_consistency"] == 0.75
    assert f["tick_move_pct"] == 3.0
    # lookback at 1040-30=1010 -> btc 102 -> tick move 2.0; acceleration 3-2
    assert f["acceleration_pct"] == 1.0
    assert f["up_price_momentum"] == round((0.60 - 0.52) / 0.52, 6)
    # no point 30 s before now: skipped
    late = [{"ts": 1035.0, "btc_price": 101.0, "up_price": 0.5, "down_price": 0.5}]
    assert dataset.reconstruct_features(late, rec, late) is None
