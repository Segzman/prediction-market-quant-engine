import math

import pytest

from scripts.report_performance import (
    build_fee_context,
    build_probability_path_report,
    build_report,
    render_report,
)


def test_build_report_computes_net_pnl_and_slippage_by_model():
    records = [
        {
            "type": "candidate",
            "window_end_ts": 100,
            "model": "A",
            "track": "CONVICTION_ONLY",
            "direction": "UP",
            "entry_regime": "LATE_CONVICTION",
            "would_trade": True,
            "planned_bet_usdc": 5.0,
            "market_price": 0.58,
            "direction_probability": 0.71,
            "net_expected_profit_usdc": 0.22,
            "estimated_cost_usdc": 0.03,
            "liquidity_score": 0.81,
            "book_spread": 0.04,
            "edge": 0.04,
            "reason_code": "READY",
            "probabilities": {"P": 0.69},
            "details": {"calibration": {"market_implied_up_probability": 0.58}},
        },
        {
            "type": "trade",
            "order_id": "a-1",
            "model": "A",
            "role": "PRIMARY",
            "window_end_ts": 100,
        },
        {
            "type": "fill",
            "order_id": "a-1",
            "details": {
                "estimated_fee_usdc": 0.12,
                "fill_vs_signal_bps": 18.0,
                "submitted_vs_signal_bps": 10.0,
            },
        },
        {
            "type": "resolution",
            "order_id": "a-1",
            "window_end_ts": 100,
            "resolved_up": True,
            "pnl": 1.5,
        },
        {
            "type": "trade",
            "order_id": "a-hedge",
            "model": "A",
            "role": "HEDGE",
        },
        {
            "type": "fill",
            "order_id": "a-hedge",
            "details": {
                "estimated_fee_usdc": 0.04,
                "fill_vs_signal_bps": 30.0,
                "submitted_vs_signal_bps": 20.0,
            },
        },
        {
            "type": "resolution",
            "order_id": "a-hedge",
            "pnl": -0.2,
        },
        {
            "type": "trade",
            "order_id": "b-1",
            "model": "B",
            "role": "PRIMARY",
            "window_end_ts": 200,
        },
        {
            "type": "fill",
            "order_id": "b-1",
            "details": {
                "estimated_fee_usdc": 0.08,
                "submitted_vs_signal_bps": 6.0,
            },
        },
        {
            "type": "resolution",
            "order_id": "b-1",
            "window_end_ts": 200,
            "resolved_up": True,
            "pnl": -1.0,
        },
        {
            "type": "candidate",
            "window_end_ts": 200,
            "model": "B",
            "track": "MISPRICING_ONLY",
            "direction": "DOWN",
            "would_trade": False,
            "planned_bet_usdc": 5.0,
            "market_price": 0.41,
            "direction_probability": 0.68,
            "net_expected_profit_usdc": 0.14,
            "estimated_cost_usdc": 0.05,
            "liquidity_score": 0.73,
            "book_spread": 0.06,
            "edge": 0.05,
            "reason_code": "NET_PROFIT_TOO_LOW",
            "skip_reason": "NET_PROFIT_TOO_LOW",
            "probabilities": {"P": 0.33},
            "details": {"calibration": {"market_implied_up_probability": 0.41}},
        },
    ]

    report = build_report(records)

    assert report["A"]["trades"] == 1
    assert report["A"]["resolved"] == 1
    assert report["A"]["wins"] == 1
    assert report["A"]["gross_pnl"] == 1.5
    assert report["A"]["estimated_fees"] == 0.12
    assert report["A"]["net_pnl"] == 1.38
    assert report["A"]["avg_fill_slippage_bps"] == 18.0
    assert report["A"]["avg_submit_slippage_bps"] == 10.0
    assert report["A"]["win_rate"] == 1.0
    assert report["A"]["candidate_count"] == 1
    assert report["A"]["candidate_trades"] == 1
    assert report["A"]["acceptance_rate"] == 1.0
    assert report["A"]["brier_score"] == pytest.approx((1 - 0.71) ** 2)
    assert report["A"]["log_loss"] == pytest.approx(-math.log(0.71))
    assert report["A"]["pooled_brier_score"] == pytest.approx((1 - 0.69) ** 2)
    assert report["A"]["avg_market_implied_up_probability"] == pytest.approx(0.58)
    assert report["A"]["avg_estimated_cost_usdc"] == pytest.approx(0.03)

    assert report["B"]["trades"] == 1
    assert report["B"]["resolved"] == 1
    assert report["B"]["wins"] == 0
    assert report["B"]["gross_pnl"] == -1.0
    assert report["B"]["estimated_fees"] == 0.08
    assert report["B"]["net_pnl"] == -1.08
    assert report["B"]["avg_fill_slippage_bps"] == 0.0
    assert report["B"]["avg_submit_slippage_bps"] == 6.0
    assert report["B"]["win_rate"] == 0.0
    assert report["B"]["candidate_count"] == 1
    assert report["B"]["candidate_trades"] == 0
    assert report["B"]["acceptance_rate"] == 0.0
    assert report["B"]["skip_breakdown"]["NET_PROFIT_TOO_LOW"] == 1


def test_render_report_outputs_markdown_table():
    report = {
        "A": {
            "trades": 2,
            "candidate_count": 5,
            "candidate_trades": 2,
            "acceptance_rate": 0.4,
            "resolved": 2,
            "wins": 1,
            "brier_score": 0.18,
            "pooled_brier_score": 0.16,
            "calibration_error": 0.28,
            "log_loss": 0.52,
            "gross_pnl": 0.7,
            "estimated_fees": 0.1,
            "net_pnl": 0.6,
            "avg_fill_slippage_bps": 7.5,
            "avg_submit_slippage_bps": 4.0,
            "win_rate": 0.5,
            "avg_expected_net_profit_usdc": 0.12,
            "avg_realized_pnl_usdc": 0.10,
            "realized_vs_expected_gap_usdc": -0.02,
            "edge_bucket_acceptance": {"0.02-0.05": 0.4},
        }
    }

    rendered = render_report(report)

    assert (
        "| Model | Trades | Candidates | Accept Rate | Resolved | Win Rate | "
        "Brier | Pooled Brier | Cal Gap | Gross PnL |"
    ) in rendered
    assert (
        "| A | 2 | 5 | 40.0% | 2 | 50.0% | 0.180 | 0.160 | 0.280 | "
        "$0.70 | $0.10 | $0.60 | 7.50 | 4.00 |"
    ) in rendered
    assert "Fee context:" in rendered
    assert "Candidate metrics:" in rendered


def test_build_report_estimates_crypto_taker_fee_when_fill_fee_missing():
    records = [
        {
            "type": "trade",
            "order_id": "c-1",
            "model": "C",
            "role": "PRIMARY",
            "asset": "BTC",
            "price": 0.40,
            "amount_usdc": 10.0,
        },
        {
            "type": "fill",
            "order_id": "c-1",
            "filled_size": 25.0,
            "avg_price": 0.40,
            "details": {},
        },
        {
            "type": "resolution",
            "order_id": "c-1",
            "pnl": 5.0,
        },
    ]

    report = build_report(records)

    # docs.polymarket.com/trading/fees: 25 shares * 0.07 * 0.40 * 0.60
    assert report["C"]["estimated_fees"] == pytest.approx(0.42)
    assert report["C"]["net_pnl"] == pytest.approx(4.58)
    assert report["C"]["fee_estimate_trades"] == 1
    assert report["C"]["fee_fill_trades"] == 0
    assert report["C"]["avg_entry_price"] == 0.4


def test_build_fee_context_exposes_crypto_taker_assumption():
    context = build_fee_context()

    assert context["market_class"] == "crypto"
    assert context["fee_rate"] == 0.07


def test_build_probability_path_report_compares_pooled_and_selected_paths():
    records = [
        {
            "type": "candidate",
            "window_end_ts": 100,
            "direction": "UP",
            "direction_probability": 0.72,
            "probabilities": {"A": 0.65, "B": 0.61, "C": 0.70, "P": 0.74},
        },
        {
            "type": "candidate",
            "window_end_ts": 200,
            "direction": "DOWN",
            "direction_probability": 0.67,
            "probabilities": {"A": 0.58, "B": 0.43, "C": 0.40, "P": 0.36},
        },
        {"type": "resolution", "window_end_ts": 100, "resolved_up": True},
        {"type": "resolution", "window_end_ts": 200, "resolved_up": False},
    ]

    comparison = build_probability_path_report(records)

    assert comparison["P"]["count"] == 2
    assert comparison["SELECTED"]["count"] == 2
    assert comparison["P"]["brier_score"] <= comparison["A"]["brier_score"]


def test_build_report_treats_regime_filtered_candidate_as_not_tradeable():
    records = [
        {
            "type": "candidate",
            "window_end_ts": 100,
            "model": "B",
            "track": "MISPRICING_ONLY",
            "direction": "UP",
            "direction_probability": 0.74,
            "entry_regime": "MISPRICING",
            "would_trade": True,
            "reason_code": "READY",
            "skip_reason": "",
            "probabilities": {"P": 0.7},
        },
        {
            "type": "resolution",
            "window_end_ts": 100,
            "resolved_up": True,
        },
    ]

    report = build_report(records)

    assert report["B"]["candidate_count"] == 1
    assert report["B"]["candidate_trades"] == 0
    assert report["B"]["acceptance_rate"] == 0.0
    assert report["B"]["skip_breakdown"]["regime=MISPRICING blocked by filter"] == 1


def _resolved_trade_records(oid, *, won, amount, wts, ts, asset="NONE"):
    # price 0.5 => a win pays +amount, a loss pays -amount (fee-free for non-crypto asset)
    pnl = amount if won else -amount
    return [
        {
            "type": "trade",
            "order_id": oid,
            "model": "A",
            "role": "PRIMARY",
            "window_end_ts": wts,
            "ts": ts,
            "price": 0.5,
            "amount_usdc": amount,
            "asset": asset,
        },
        {
            "type": "resolution",
            "order_id": oid,
            "window_end_ts": wts,
            "resolved_up": won,
            "pnl": pnl,
            "ts": ts,
        },
    ]


def test_gate1_flags_noisy_small_sample_as_not_significant():
    from scripts.report_performance import build_gate1_summary

    records = []
    # nets ordered by window: +3, -1, +3, -1  -> total +4, mean +1, max drawdown 1
    for i, (won, amount) in enumerate([(True, 3.0), (False, 1.0), (True, 3.0), (False, 1.0)]):
        wts = 100 * (i + 1)
        records += _resolved_trade_records(f"a-{i}", won=won, amount=amount, wts=wts, ts=wts)
    # 4 submissions, 3 fills => fill_rate 0.75
    for i in range(4):
        records.append({"type": "submission", "order_id": f"a-{i}", "role": "PRIMARY"})
    for i in range(3):
        records.append({"type": "fill", "order_id": f"a-{i}", "filled_size": 1.0, "avg_price": 0.5})

    summary = build_gate1_summary(records, min_resolved_trades=3, max_drawdown_usdc=10.0)

    assert summary["resolved_trades"] == 4
    assert summary["net_pnl_total"] == pytest.approx(4.0)
    assert summary["mean_net_pnl_per_trade"] == pytest.approx(1.0)
    assert summary["max_drawdown_usdc"] == pytest.approx(1.0)
    assert summary["fill_rate"] == pytest.approx(0.75)
    # Noisy: the 95% CI on per-trade net PnL straddles zero -> edge not proven.
    assert summary["net_pnl_ci95_low"] < 0
    assert summary["verdict"] == "FAIL"
    assert any("confidence interval" in r.lower() for r in summary["reasons"])


def test_gate1_reports_insufficient_sample():
    from scripts.report_performance import build_gate1_summary

    records = _resolved_trade_records("a-0", won=True, amount=1.0, wts=100, ts=100)
    summary = build_gate1_summary(records, min_resolved_trades=30)

    assert summary["verdict"] == "INSUFFICIENT_SAMPLE"


def test_gate1_passes_consistent_positive_edge():
    from scripts.report_performance import build_gate1_summary

    records = []
    for i in range(8):
        wts = 100 * (i + 1)
        records += _resolved_trade_records(f"a-{i}", won=True, amount=1.0, wts=wts, ts=wts)

    summary = build_gate1_summary(records, min_resolved_trades=5, max_drawdown_usdc=5.0)

    assert summary["verdict"] == "PASS"
    assert summary["net_pnl_ci95_low"] > 0
    assert summary["max_drawdown_usdc"] == pytest.approx(0.0)


def test_path_report_uses_window_outcome_records():
    from scripts.report_performance import build_probability_path_report

    records = [
        {
            "type": "candidate",
            "window_end_ts": 100,
            "direction": "UP",
            "direction_probability": 0.8,
            "probabilities": {"A": 0.8},
        },
        {"type": "window_outcome", "window_end_ts": 100, "resolved_up": True},
    ]
    comparison = build_probability_path_report(records)
    assert comparison["A"]["count"] == 1
    assert comparison["A"]["wins"] == 1
