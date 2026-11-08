from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research import backtest_report as br  # noqa: E402
from research.month_backtest import fetch  # noqa: E402
from research.month_backtest import run as mb  # noqa: E402
from strategy.probability import get_probability  # noqa: E402


# ---------------------------------------------------------------- fetch helpers


def test_parse_binance_1s_handles_micro_milli_and_seconds():
    text = (
        "1792454400000000,1.0,1.0,1.0,101.5,0.1,1792454400999999,0,1,0,0,0\n"
        "1792454401000,1.0,1.0,1.0,102.5,0.1,1792454401999,0,1,0,0,0\n"
        "1792454402,1.0,1.0,1.0,103.5,0.1,1792454402999,0,1,0,0,0\n"
        "open_time,header,row\n"
    )
    assert fetch.parse_binance_1s(text) == [
        (1792454400, 101.5),
        (1792454401, 102.5),
        (1792454402, 103.5),
    ]


def test_window_start_from_slug_requires_five_minute_alignment():
    assert fetch.window_start_from_slug("btc-updown-5m-1793923200") == 1793923200
    assert fetch.window_start_from_slug("btc-updown-5m-1793923201") is None
    assert fetch.window_start_from_slug("btc-updown-5m-abc") is None


def test_market_record_resolved_up_and_unresolved():
    event = {"slug": "btc-updown-5m-1793923200", "eventMetadata": {"priceToBeat": 1.0}}
    market = {
        "slug": "btc-updown-5m-1793923200",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
        "outcomePrices": '["1", "0"]',
        "umaResolutionStatus": "resolved",
    }
    rec = fetch.market_record(event, market)
    assert rec["start_ts"] == 1793923200
    assert rec["end_ts"] == 1793923500
    assert rec["up_token"] == "111"
    assert rec["down_token"] == "222"
    assert rec["resolved_up"] is True

    down = dict(market, outcomePrices='["0.0", "1.0"]')
    assert fetch.market_record(event, down)["resolved_up"] is False

    open_market = dict(market, outcomePrices='["0.5", "0.5"]')
    assert fetch.market_record(event, open_market)["resolved_up"] is None

    not_two_sided = dict(market, outcomes='["Yes", "No"]')
    assert fetch.market_record(event, not_two_sided) is None


def test_day_range_is_oldest_first_and_inclusive():
    assert fetch.day_range(date(2026, 11, 6), 3) == ["2026-11-04", "2026-11-05", "2026-11-06"]


# ------------------------------------------------------------ run: pure helpers


def test_last_known_respects_staleness_and_order():
    points = [(10, 0.4), (40, 0.5), (100, 0.6)]
    stamps = [t for t, _ in points]
    assert mb.last_known(points, stamps, 39, 120) == 0.4
    assert mb.last_known(points, stamps, 40, 120) == 0.5
    assert mb.last_known(points, stamps, 5, 120) is None
    assert mb.last_known(points, stamps, 230, 120) is None  # 130 s after the last quote


def test_fee_matches_report_formula_hand_case():
    # 5 USDC at price 0.5: 10 shares, fee = 10 * 0.5 * 0.072 * 0.25 = 0.09
    assert mb.fee_usdc(0.5, 5.0) == pytest.approx(0.09)
    assert mb.fee_usdc(1.0, 5.0) == 0.0


def test_p_up_conversion_for_jev_answer():
    assert mb._p_up(0.7, "UP") == pytest.approx(0.7)
    assert mb._p_up(0.7, "DOWN") == pytest.approx(0.3)


def test_score_block_hand_case():
    block = mb.score_block([0.8, 0.2], [1, 0])
    assert block["brier"] == pytest.approx(0.04)
    assert block["hits"] == 2 and block["hit_n"] == 2
    assert block["hit_rate"] == 1.0
    assert mb.score_block([], [])["n"] == 0


def test_cluster_mean_ci_constant_and_bounded():
    lo, hi = mb.cluster_mean_ci([2.0, 2.0, 2.0, 2.0], [0, 0, 1, 1], iters=200)
    assert lo == pytest.approx(2.0) and hi == pytest.approx(2.0)
    values = [1.0, -1.0, 3.0, 0.0]
    lo, hi = mb.cluster_mean_ci(values, [0, 0, 1, 1], iters=500)
    assert min(values) <= lo <= hi <= max(values)
    assert mb.cluster_mean_ci([], []) == (None, None)


def _state(**overrides):
    base = dict(
        open_price=100.0,
        price_path=[(t, 100.0 + 0.01 * t) for t in range(0, 31)],
        poly_path=[(t, 0.5 + 0.001 * t, 0.5 - 0.001 * t) for t in range(0, 31, 5)],
        price_now=100.3,
        seconds_remaining=60,
        up_price=0.6,
        down_price=0.4,
    )
    base.update(overrides)
    return mb.build_state_features(**base)


def test_build_state_features_hand_case():
    st = _state()
    assert st["move"] == pytest.approx(0.003)
    assert st["mkt_up"] == pytest.approx(0.6)
    assert st["side"] == "UP"
    ctx = st["context"]
    assert ctx.strongest_side == "UP"
    assert ctx.btc_poly_agreement == 1.0  # BTC up and book leans Up
    assert ctx.market_implied_up_probability == pytest.approx(0.6)
    assert ctx.market_conviction == pytest.approx(0.72 * 0.6)
    assert st["sigma"] > 0.0


def test_build_state_features_thin_history_has_no_volatility_or_momentum():
    st = _state(price_path=[(0, 100.0), (1, 100.1)], poly_path=[])
    assert st["sigma"] == 0.0
    assert st["up_mom"] == 0.0 and st["down_mom"] == 0.0
    assert st["tick_move"] == 0.0 and st["consistency"] == 0.5


def test_side_from_market_and_agreement_neutral_at_half():
    assert mb.side_from_market(0.5) == "NONE"
    st = _state(up_price=0.5, down_price=0.5)
    assert st["context"].btc_poly_agreement == 0.0


def test_ledger_record_uses_bot_bucket_keys():
    st = _state()
    rec = mb.ledger_record(st, window_end_ts=1000.0, resolved_up=True, base_prob=0.6)
    assert rec["type"] == "resolution"
    assert rec["window_end_ts"] == 1000.0 and rec["resolved_up"] is True
    assert rec["time_bin"] == "30-60"
    assert rec["move_pct_bin"] == ">0.0010"
    assert rec["model_c_bucket"].count("|") == 4


def test_model_c_ignores_resolutions_at_or_after_decision(tmp_path):
    """Look-ahead guard: only resolutions with window_end_ts < decision_ts may count."""
    st = _state()
    base = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "B", context=st["context"])
    decision_ts = 940.0
    ledger = tmp_path / "ledger.jsonl"
    lines = []
    for _ in range(12):  # resolved strictly before the decision: all Up
        lines.append(mb.ledger_record(st, 900.0, True, base))
    for _ in range(12):  # resolved at or after the decision: all Down, must be ignored
        lines.append(mb.ledger_record(st, 1000.0, False, base))
    ledger.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    kwargs = {"context": st["context"], "ledger_path": str(ledger), "decision_ts": decision_ts}
    c_prob = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "C", **kwargs)
    assert c_prob == pytest.approx(0.95)  # 12 of 12 prior resolutions were Up, clamped
    late = get_probability(
        st["S0"], st["S"], st["s"], st["sigma"], "C",
        context=st["context"], ledger_path=str(ledger), decision_ts=1100.0,
    )
    assert late == pytest.approx(0.5)  # now the Down resolutions count too: 12/24


# ----------------------------------------------------- run: entry rules and sim


def _states(window: int, resolved_up: bool, quotes_by_s: dict[int, tuple[float, float]], p_up_by_s: dict[int, float]):
    out = []
    for s, (up_px, down_px) in quotes_by_s.items():
        out.append(
            {
                "s": s,
                "window": window,
                "week": 1,
                "resolved_up": resolved_up,
                "up_px": up_px,
                "down_px": down_px,
                "p": {"A": p_up_by_s[s]},
            }
        )
    return out


def test_pick_entry_takes_earliest_qualifying_state_and_settles_win():
    # s=90: model 0.5 -> no edge; s=60: UP at 0.55 with p 0.80 -> edge 0.25, qualifies.
    states = _states(
        0,
        True,
        {90: (0.55, 0.45), 60: (0.55, 0.45), 30: (0.60, 0.40)},
        {90: 0.5, 60: 0.80, 30: 0.80},
    )
    trade = mb.pick_entry(states, "A", stake=5.0)
    assert trade is not None
    assert trade["s"] == 60 and trade["side"] == "UP"
    fill = 0.55 * (1 + mb.EXPECTED_SLIPPAGE_BPS / 10_000)
    assert trade["fill"] == pytest.approx(fill)
    fee = mb.fee_usdc(fill, 5.0)
    assert trade["fee"] == pytest.approx(fee)
    assert trade["won"] is True
    assert trade["pnl"] == pytest.approx(5.0 / fill - 5.0 - fee)


def test_pick_entry_rejects_out_of_band_and_small_edge():
    out_of_band = _states(0, True, {60: (0.70, 0.30)}, {60: 0.95})
    assert mb.pick_entry(out_of_band, "A") is None
    small_edge = _states(0, True, {60: (0.55, 0.45)}, {60: 0.58})
    assert mb.pick_entry(small_edge, "A") is None


def test_pick_entry_down_side_loss_accounting():
    states = _states(0, True, {60: (0.30, 0.70)}, {60: 0.20})  # DOWN quote 0.70 is out of band
    assert mb.pick_entry(states, "A") is None
    states = _states(0, True, {60: (0.45, 0.55)}, {60: 0.20})  # DOWN quote 0.55 in band, p_down 0.80
    trade = mb.pick_entry(states, "A", stake=5.0)
    assert trade is not None and trade["side"] == "DOWN"
    assert trade["won"] is False
    fill = 0.55 * (1 + mb.EXPECTED_SLIPPAGE_BPS / 10_000)
    assert trade["pnl"] == pytest.approx(-5.0 - mb.fee_usdc(fill, 5.0))


def test_simulate_one_trade_per_window_and_drawdown():
    windows = [
        _states(0, True, {60: (0.55, 0.45)}, {60: 0.80}),
        _states(1, False, {60: (0.55, 0.45)}, {60: 0.80}),
        _states(2, True, {60: (0.55, 0.45)}, {60: 0.80}),
    ]
    trades = mb.simulate(windows, "A")
    assert [t["window"] for t in trades] == [0, 1, 2]
    pnls = [t["pnl"] for t in trades]
    assert trades[0]["won"] and not trades[1]["won"] and trades[2]["won"]
    assert br.max_drawdown(pnls) == pytest.approx(-pnls[1] if pnls[1] < 0 else 0.0)
    summary = mb.summarize_trades(trades)
    assert summary["n"] == 3 and summary["wins"] == 2
    assert summary["total_pnl"] == pytest.approx(sum(pnls))
    assert math.isclose(summary["mean_pnl"], sum(pnls) / 3)
    assert mb.summarize_trades([]) == {"n": 0}
