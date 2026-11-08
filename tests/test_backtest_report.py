from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research import backtest_report as br  # noqa: E402


def test_brier_hand_case():
    # ((0.8-1)^2 + (0.2-1)^2) / 2 = (0.04 + 0.64) / 2
    assert br.brier([0.8, 0.2], [1, 1]) == pytest.approx(0.34)


def test_log_loss_hand_case():
    # (-ln 0.8 - ln 0.2) / 2
    expected = (-math.log(0.8) - math.log(0.2)) / 2
    assert br.log_loss([0.8, 0.2], [1, 1]) == pytest.approx(expected)


def test_reliability_bins_and_ece():
    probs = [0.05, 0.05, 0.95, 0.95]
    outcomes = [0, 1, 1, 1]
    rows, ece = br.reliability(probs, outcomes, bins=10)
    assert rows[0]["n"] == 2 and rows[0]["freq"] == pytest.approx(0.5)
    assert rows[9]["n"] == 2 and rows[9]["freq"] == pytest.approx(1.0)
    # bin 0: |0.05 - 0.5| * 2/4 ; bin 9: |0.95 - 1.0| * 2/4
    assert ece == pytest.approx(0.45 * 0.5 + 0.05 * 0.5)


def test_directional_hits_skip_half():
    hits, n = br.directional_hits([0.7, 0.3, 0.5, 0.6], [1, 1, 1, 0])
    # 0.7 -> UP, right; 0.3 -> DOWN, wrong; 0.5 skipped; 0.6 -> UP, wrong
    assert (hits, n) == (1, 3)


def test_wilson_zero_of_ten():
    lo, hi = br.wilson_interval(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-4)
    # z^2 / (n + z^2) = 3.8415 / 13.8415
    assert hi == pytest.approx(0.2775, abs=1e-3)


def test_wilson_empty_is_none():
    assert br.wilson_interval(0, 0) == (None, None)


def test_max_drawdown_hand_case():
    # cumulative: 1, -1, 2, 1 -> worst fall is peak 1 to trough -1
    assert br.max_drawdown([1, -2, 3, -1]) == pytest.approx(2.0)
    assert br.max_drawdown([1, 2, 3]) == 0.0


def test_trade_pnl_win_and_loss():
    # stake 2 at price 0.5 buys 4 shares; fee 0.1
    assert br.trade_pnl(2.0, 0.5, 0.1, won=True) == pytest.approx(1.9)
    assert br.trade_pnl(2.0, 0.5, 0.1, won=False) == pytest.approx(-2.1)


def test_expected_trade_pnl_hand_case():
    # 2 * (0.6 / 0.5) - 2 - 0.1 = 0.3
    assert br.expected_trade_pnl(2.0, 0.5, 0.6, 0.1) == pytest.approx(0.3)


def test_cluster_bootstrap_constant_and_bounds():
    lo, hi = br.cluster_bootstrap_mean_ci([1.0, 1.0, 1.0], ["a", "b", "c"], iters=200)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)
    vals = [1.0, -1.0, 2.0, 0.0]
    lo, hi = br.cluster_bootstrap_mean_ci(vals, ["a", "a", "b", "c"], iters=500)
    assert lo <= 0.5 <= hi


def test_tick_features_monotone_rising():
    hist = [(0.0, 100.0), (2.0, 101.0), (4.0, 102.0), (6.0, 103.0)]
    move, consistency, accel = br.tick_features(hist)
    assert move == pytest.approx(0.03)
    assert consistency == pytest.approx(1.0)
    # late (103-102)/102 minus early (102-100)/100 = 0.0098039 - 0.02
    assert accel == pytest.approx((1 / 102) - 0.02)


def test_split_is_chronological_and_window_atomic():
    rows = [{"w": w, "src": s} for w in (10, 20, 30, 40) for s in ("both", "conviction")]
    train, test = br.split_chronological(rows, train_fraction=0.5)
    assert {r["w"] for r in train} == {10, 20}
    assert {r["w"] for r in test} == {30, 40}


def test_first_bets_one_per_source_window():
    rows = [
        {"src": "both", "w": 1, "ts": 1.0, "bet": True},
        {"src": "both", "w": 1, "ts": 2.0, "bet": True},
        {"src": "conviction", "w": 1, "ts": 1.5, "bet": True},
        {"src": "both", "w": 2, "ts": 3.0, "bet": False},
    ]
    bets = br.first_bets(rows)
    assert [(b["src"], b["ts"]) for b in bets] == [("both", 1.0), ("conviction", 1.5)]
