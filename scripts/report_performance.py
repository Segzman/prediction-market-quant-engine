from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

CRYPTO_TAKER_FEE_RATE = 0.072
CRYPTO_TAKER_FEE_EXPONENT = 1.0
CRYPTO_ASSETS = {"BTC", "ETH", "SOL", "XRP", "POL"}
FEE_DOC_URL = "https://docs.polymarket.com/trading/fees"

# Gate 1 promotion defaults (see LIVE_READINESS_PLAN.md). Conservative by design:
# a small isolated canary should not be promoted on a thin or noisy sample.
Z95 = 1.959963984540054
GATE1_MIN_RESOLVED_TRADES = 200
GATE1_MAX_DRAWDOWN_USDC = 25.0


def load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_fee_context() -> dict[str, float | str]:
    return {
        "market_class": "crypto",
        "assumption": "Shadow/demo buys are scored with Polymarket crypto taker fees when fills do not include a fee.",
        "formula": "fee = C × p × feeRate × (p × (1 - p))^exponent",
        "fee_rate": CRYPTO_TAKER_FEE_RATE,
        "exponent": CRYPTO_TAKER_FEE_EXPONENT,
        "source_url": FEE_DOC_URL,
    }


def estimate_crypto_taker_fee_usdc(*, price: float, usdc_notional: float) -> float:
    if price <= 0 or price >= 1 or usdc_notional <= 0:
        return 0.0
    shares = usdc_notional / price
    return shares * price * CRYPTO_TAKER_FEE_RATE * (
        price * (1 - price)
    ) ** CRYPTO_TAKER_FEE_EXPONENT


def _sum_fill_notional_usdc(trade_fills: list[dict]) -> float:
    total = 0.0
    for fill in trade_fills:
        filled_size = float(fill.get("filled_size", 0.0) or 0.0)
        avg_price = float(fill.get("avg_price", 0.0) or 0.0)
        if filled_size > 0 and avg_price > 0:
            total += filled_size * avg_price
    return total


def resolve_trade_fee_usdc(trade: dict, trade_fills: list[dict]) -> tuple[float, str]:
    explicit_fee_total = 0.0
    explicit_positive = False
    for fill in trade_fills:
        details = fill.get("details") or {}
        if "estimated_fee_usdc" in details:
            fee_value = float(details.get("estimated_fee_usdc", 0.0) or 0.0)
            explicit_fee_total += fee_value
            if fee_value > 0 or float(details.get("fee_rate_bps", 0.0) or 0.0) > 0:
                explicit_positive = True
    if explicit_positive:
        return explicit_fee_total, "fill"

    asset = str(trade.get("asset", "")).upper()
    if asset not in CRYPTO_ASSETS:
        return 0.0, "none"

    price = float(trade.get("price", 0.0) or 0.0)
    usdc_notional = _sum_fill_notional_usdc(trade_fills) or float(
        trade.get("amount_usdc", 0.0) or 0.0
    )
    return (
        estimate_crypto_taker_fee_usdc(price=price, usdc_notional=usdc_notional),
        "estimated_crypto_taker",
    )


def _edge_bucket(edge: float) -> str:
    abs_edge = abs(edge)
    if abs_edge < 0.02:
        return "<0.02"
    if abs_edge < 0.05:
        return "0.02-0.05"
    return ">=0.05"


def _candidate_effective_tradeable(candidate: dict) -> bool:
    if not candidate.get("would_trade"):
        return False
    track = str(candidate.get("track") or "")
    regime = str(candidate.get("entry_regime") or candidate.get("regime_candidate") or "")
    if track in {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}:
        return regime == "LATE_CONVICTION"
    return True


def _candidate_skip_reason(candidate: dict) -> str:
    skip_reason = str(candidate.get("skip_reason") or "")
    if skip_reason:
        return skip_reason
    if candidate.get("would_trade") and not _candidate_effective_tradeable(candidate):
        regime = str(candidate.get("entry_regime") or candidate.get("regime_candidate") or "")
        return f"regime={regime} blocked by filter"
    return ""


def build_probability_path_report(records: list[dict]) -> dict[str, dict[str, float]]:
    window_outcomes: dict[int, bool] = {}
    for record in records:
        if (
            record.get("type") in {"resolution", "window_outcome"}
            and record.get("window_end_ts") is not None
            and record.get("resolved_up") is not None
        ):
            window_outcomes[int(record["window_end_ts"])] = bool(record["resolved_up"])

    metrics: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "wins": 0, "brier_score": 0.0, "log_loss": 0.0}
    )
    for candidate in records:
        if candidate.get("type") != "candidate":
            continue
        window_end_ts = candidate.get("window_end_ts")
        if window_end_ts is None or int(window_end_ts) not in window_outcomes:
            continue
        resolved_up = window_outcomes[int(window_end_ts)]
        probabilities = dict(candidate.get("probabilities") or {})
        if candidate.get("direction_probability") is not None and candidate.get("direction") in {"UP", "DOWN"}:
            selected_up_probability = float(candidate["direction_probability"])
            if candidate.get("direction") == "DOWN":
                selected_up_probability = 1.0 - selected_up_probability
            probabilities["SELECTED"] = selected_up_probability

        for model, up_probability_raw in probabilities.items():
            up_probability = min(1 - 1e-6, max(1e-6, float(up_probability_raw)))
            direction = "UP" if up_probability >= 0.5 else "DOWN"
            direction_probability = up_probability if direction == "UP" else (1.0 - up_probability)
            won = (direction == "UP" and resolved_up) or (direction == "DOWN" and not resolved_up)
            bucket = metrics[str(model)]
            bucket["count"] += 1
            if won:
                bucket["wins"] += 1
            bucket["brier_score"] += (direction_probability - (1.0 if won else 0.0)) ** 2
            bucket["log_loss"] += -math.log(
                direction_probability if won else (1.0 - direction_probability)
            )

    for bucket in metrics.values():
        if bucket["count"]:
            bucket["win_rate"] = bucket["wins"] / bucket["count"]
            bucket["brier_score"] /= bucket["count"]
            bucket["log_loss"] /= bucket["count"]
        else:
            bucket["win_rate"] = 0.0
    return dict(metrics)


def build_report(records: list[dict]) -> dict[str, dict[str, float]]:
    trades = {
        record.get("order_id"): record
        for record in records
        if record.get("type") == "trade" and record.get("order_id")
    }
    resolutions = {
        record.get("order_id"): record
        for record in records
        if record.get("type") == "resolution" and record.get("order_id")
    }
    fills = defaultdict(list)
    window_outcomes: dict[int, bool] = {}
    candidates = [
        record
        for record in records
        if record.get("type") == "candidate" and record.get("model")
    ]
    for record in records:
        if record.get("type") == "fill" and record.get("order_id"):
            fills[record["order_id"]].append(record)
        if (
            record.get("type") in {"resolution", "window_outcome"}
            and record.get("window_end_ts") is not None
            and record.get("resolved_up") is not None
        ):
            window_outcomes[int(record["window_end_ts"])] = bool(record["resolved_up"])

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "trades": 0,
            "resolved": 0,
            "wins": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "estimated_fees": 0.0,
            "net_pnl": 0.0,
            "avg_fill_slippage_bps": 0.0,
            "avg_submit_slippage_bps": 0.0,
            "avg_entry_price": 0.0,
            "fee_estimate_trades": 0,
            "fee_fill_trades": 0,
            "candidate_count": 0,
            "candidate_trades": 0,
            "candidate_labeled": 0,
            "acceptance_rate": 0.0,
            "brier_score": 0.0,
            "log_loss": 0.0,
            "avg_expected_net_profit_usdc": 0.0,
            "avg_realized_pnl_usdc": 0.0,
            "realized_vs_expected_gap_usdc": 0.0,
            "edge_bucket_acceptance": {},
            "calibration_error": 0.0,
            "pooled_brier_score": 0.0,
            "pooled_log_loss": 0.0,
            "pooled_calibration_error": 0.0,
            "avg_market_implied_up_probability": 0.0,
            "avg_pooled_up_probability": 0.0,
            "avg_estimated_cost_usdc": 0.0,
            "avg_liquidity_score": 0.0,
            "avg_book_spread": 0.0,
            "reason_breakdown": {},
            "skip_breakdown": {},
        }
    )
    fill_slippage_counts: dict[str, int] = defaultdict(int)
    submit_slippage_counts: dict[str, int] = defaultdict(int)
    price_counts: dict[str, int] = defaultdict(int)
    expected_value_counts: dict[str, int] = defaultdict(int)
    realized_value_counts: dict[str, int] = defaultdict(int)
    edge_bucket_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    edge_bucket_accepts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    reason_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    skip_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    market_probability_counts: dict[str, int] = defaultdict(int)
    pooled_probability_counts: dict[str, int] = defaultdict(int)
    cost_counts: dict[str, int] = defaultdict(int)
    liquidity_counts: dict[str, int] = defaultdict(int)
    spread_counts: dict[str, int] = defaultdict(int)

    for order_id, trade in trades.items():
        if trade.get("role", "PRIMARY") == "HEDGE":
            continue
        model = str(trade.get("model", "UNKNOWN"))
        bucket = by_model[model]
        bucket["trades"] += 1
        entry_price = float(trade.get("price", 0.0) or 0.0)
        if entry_price > 0:
            bucket["avg_entry_price"] += entry_price
            price_counts[model] += 1

        gross_pnl = float(resolutions.get(order_id, {}).get("pnl", 0.0) or 0.0)
        bucket["gross_pnl"] += gross_pnl
        if order_id in resolutions:
            bucket["resolved"] += 1
            if gross_pnl > 0:
                bucket["wins"] += 1

        trade_fills = fills.get(order_id, [])
        fee_total, fee_source = resolve_trade_fee_usdc(trade, trade_fills)
        if fee_source == "fill":
            bucket["fee_fill_trades"] += 1
        elif fee_source == "estimated_crypto_taker":
            bucket["fee_estimate_trades"] += 1
        for fill in trade_fills:
            details = fill.get("details") or {}
            if "fill_vs_signal_bps" in details:
                bucket["avg_fill_slippage_bps"] += float(details["fill_vs_signal_bps"])
                fill_slippage_counts[model] += 1
            if "submitted_vs_signal_bps" in details:
                bucket["avg_submit_slippage_bps"] += float(details["submitted_vs_signal_bps"])
                submit_slippage_counts[model] += 1
        bucket["estimated_fees"] += fee_total
        bucket["net_pnl"] += gross_pnl - fee_total

    for model, bucket in by_model.items():
        fill_count = fill_slippage_counts.get(model, 0)
        if fill_count:
            bucket["avg_fill_slippage_bps"] /= fill_count
        submit_count = submit_slippage_counts.get(model, 0)
        if submit_count:
            bucket["avg_submit_slippage_bps"] /= submit_count
        price_count = price_counts.get(model, 0)
        if price_count:
            bucket["avg_entry_price"] /= price_count
        bucket["win_rate"] = bucket["wins"] / bucket["resolved"] if bucket["resolved"] else 0.0

    for candidate in candidates:
        model = str(candidate.get("model", "UNKNOWN"))
        bucket = by_model[model]
        bucket["candidate_count"] += 1
        effective_tradeable = _candidate_effective_tradeable(candidate)
        if effective_tradeable:
            bucket["candidate_trades"] += 1
        reason_code = str(candidate.get("reason_code") or "")
        if reason_code:
            reason_counts[model][reason_code] += 1
        skip_reason = _candidate_skip_reason(candidate)
        if skip_reason:
            skip_counts[model][skip_reason] += 1

        edge = float(candidate.get("edge", 0.0) or 0.0)
        edge_bucket = _edge_bucket(edge)
        edge_bucket_counts[model][edge_bucket] += 1
        if effective_tradeable:
            edge_bucket_accepts[model][edge_bucket] += 1

        expected_net = candidate.get("net_expected_profit_usdc")
        if expected_net is not None:
            bucket["avg_expected_net_profit_usdc"] += float(expected_net or 0.0)
            expected_value_counts[model] += 1
        estimated_cost = candidate.get("estimated_cost_usdc")
        if estimated_cost is not None:
            bucket["avg_estimated_cost_usdc"] += float(estimated_cost or 0.0)
            cost_counts[model] += 1
        liquidity_score = candidate.get("liquidity_score")
        if liquidity_score is not None:
            bucket["avg_liquidity_score"] += float(liquidity_score or 0.0)
            liquidity_counts[model] += 1
        book_spread = candidate.get("book_spread")
        if book_spread is not None:
            bucket["avg_book_spread"] += float(book_spread or 0.0)
            spread_counts[model] += 1

        calibration_details = (candidate.get("details") or {}).get("calibration") or {}
        market_probability = calibration_details.get("market_implied_up_probability")
        if market_probability is not None:
            bucket["avg_market_implied_up_probability"] += float(market_probability or 0.0)
            market_probability_counts[model] += 1
        pooled_probability = (candidate.get("probabilities") or {}).get("P")
        if pooled_probability is not None:
            bucket["avg_pooled_up_probability"] += float(pooled_probability or 0.0)
            pooled_probability_counts[model] += 1

        window_end_ts = candidate.get("window_end_ts")
        direction = str(candidate.get("direction") or "")
        direction_probability = float(candidate.get("direction_probability", 0.0) or 0.0)
        if window_end_ts is None or direction not in {"UP", "DOWN"}:
            continue
        if int(window_end_ts) not in window_outcomes:
            continue

        resolved_up = window_outcomes[int(window_end_ts)]
        won_candidate = (direction == "UP" and resolved_up) or (
            direction == "DOWN" and not resolved_up
        )
        clipped_probability = min(1 - 1e-6, max(1e-6, direction_probability))
        bucket["candidate_labeled"] += 1
        bucket["brier_score"] += (clipped_probability - (1.0 if won_candidate else 0.0)) ** 2
        bucket["calibration_error"] += abs(clipped_probability - (1.0 if won_candidate else 0.0))
        bucket["log_loss"] += -math.log(
            clipped_probability if won_candidate else (1.0 - clipped_probability)
        )
        if pooled_probability is not None:
            pooled_direction_probability = float(pooled_probability)
            if direction == "DOWN":
                pooled_direction_probability = 1.0 - pooled_direction_probability
            pooled_direction_probability = min(1 - 1e-6, max(1e-6, pooled_direction_probability))
            bucket["pooled_brier_score"] += (
                pooled_direction_probability - (1.0 if won_candidate else 0.0)
            ) ** 2
            bucket["pooled_calibration_error"] += abs(
                pooled_direction_probability - (1.0 if won_candidate else 0.0)
            )
            bucket["pooled_log_loss"] += -math.log(
                pooled_direction_probability
                if won_candidate
                else (1.0 - pooled_direction_probability)
            )

        if effective_tradeable:
            planned_bet = float(candidate.get("planned_bet_usdc", 0.0) or 0.0)
            market_price = float(candidate.get("market_price", 0.0) or 0.0)
            realized_pnl = 0.0
            if planned_bet > 0 and 0 < market_price < 1:
                realized_pnl = (
                    planned_bet * (1 / market_price - 1)
                    if won_candidate
                    else -planned_bet
                )
            bucket["avg_realized_pnl_usdc"] += realized_pnl
            realized_value_counts[model] += 1

    for model, bucket in by_model.items():
        if bucket["candidate_count"]:
            bucket["acceptance_rate"] = bucket["candidate_trades"] / bucket["candidate_count"]
        if bucket["candidate_labeled"]:
            bucket["brier_score"] /= bucket["candidate_labeled"]
            bucket["log_loss"] /= bucket["candidate_labeled"]
            bucket["calibration_error"] /= bucket["candidate_labeled"]
            bucket["pooled_brier_score"] /= bucket["candidate_labeled"]
            bucket["pooled_log_loss"] /= bucket["candidate_labeled"]
            bucket["pooled_calibration_error"] /= bucket["candidate_labeled"]
        expected_count = expected_value_counts.get(model, 0)
        if expected_count:
            bucket["avg_expected_net_profit_usdc"] /= expected_count
        realized_count = realized_value_counts.get(model, 0)
        if realized_count:
            bucket["avg_realized_pnl_usdc"] /= realized_count
        bucket["realized_vs_expected_gap_usdc"] = (
            bucket["avg_realized_pnl_usdc"] - bucket["avg_expected_net_profit_usdc"]
        )
        bucket["edge_bucket_acceptance"] = {
            edge_bucket: (
                edge_bucket_accepts[model][edge_bucket] / count if count else 0.0
            )
            for edge_bucket, count in sorted(edge_bucket_counts[model].items())
        }
        if market_probability_counts.get(model, 0):
            bucket["avg_market_implied_up_probability"] /= market_probability_counts[model]
        if pooled_probability_counts.get(model, 0):
            bucket["avg_pooled_up_probability"] /= pooled_probability_counts[model]
        if cost_counts.get(model, 0):
            bucket["avg_estimated_cost_usdc"] /= cost_counts[model]
        if liquidity_counts.get(model, 0):
            bucket["avg_liquidity_score"] /= liquidity_counts[model]
        if spread_counts.get(model, 0):
            bucket["avg_book_spread"] /= spread_counts[model]
        bucket["reason_breakdown"] = dict(sorted(reason_counts[model].items()))
        bucket["skip_breakdown"] = dict(sorted(skip_counts[model].items()))

    return dict(by_model)


def _resolved_primary_net_pnls(records: list[dict]) -> list[tuple[int, float, float, float]]:
    """Per resolved primary trade: (window_end_ts, ts, gross_pnl, net_pnl), time-ordered."""
    trades = {
        record.get("order_id"): record
        for record in records
        if record.get("type") == "trade"
        and record.get("order_id")
        and record.get("role", "PRIMARY") != "HEDGE"
    }
    resolutions = {
        record.get("order_id"): record
        for record in records
        if record.get("type") == "resolution" and record.get("order_id")
    }
    fills = defaultdict(list)
    for record in records:
        if record.get("type") == "fill" and record.get("order_id"):
            fills[record["order_id"]].append(record)

    series: list[tuple[int, float, float, float]] = []
    for order_id, trade in trades.items():
        resolution = resolutions.get(order_id)
        if resolution is None:
            continue
        gross = float(resolution.get("pnl", 0.0) or 0.0)
        fee, _ = resolve_trade_fee_usdc(trade, fills.get(order_id, []))
        wts = int(trade.get("window_end_ts") or resolution.get("window_end_ts") or 0)
        ts = float(resolution.get("ts") or trade.get("ts") or 0.0)
        series.append((wts, ts, gross, gross - fee))
    series.sort(key=lambda item: (item[0], item[1]))
    return series


def _wilson_interval(wins: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _max_drawdown(nets_in_order: list[float]) -> float:
    peak = 0.0
    cumulative = 0.0
    max_dd = 0.0
    for net in nets_in_order:
        cumulative += net
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def build_gate1_summary(
    records: list[dict],
    *,
    min_resolved_trades: int = GATE1_MIN_RESOLVED_TRADES,
    max_drawdown_usdc: float = GATE1_MAX_DRAWDOWN_USDC,
    require_positive_ci: bool = True,
) -> dict:
    """Decision-grade summary for the Gate 1 (shadow-live promotion) go/no-go.

    Computes net-of-fees expectancy with a 95% confidence interval, win rate
    (Wilson interval), max drawdown, fill rate, and trade frequency, then renders
    a PASS / FAIL / INSUFFICIENT_SAMPLE verdict against the promotion thresholds.
    """
    series = _resolved_primary_net_pnls(records)
    n = len(series)
    grosses = [item[2] for item in series]
    nets = [item[3] for item in series]

    net_total = sum(nets)
    mean = net_total / n if n else 0.0
    stdev = statistics.stdev(nets) if n >= 2 else 0.0
    standard_error = stdev / math.sqrt(n) if n else 0.0
    ci_low = mean - Z95 * standard_error
    ci_high = mean + Z95 * standard_error

    wins = sum(1 for gross in grosses if gross > 0)
    win_rate = wins / n if n else 0.0
    wr_low, wr_high = _wilson_interval(wins, n)
    max_dd = _max_drawdown(nets)

    submitted = {
        record.get("order_id")
        for record in records
        if record.get("type") == "submission"
        and record.get("order_id")
        and record.get("role", "PRIMARY") != "HEDGE"
    }
    filled = {
        record.get("order_id")
        for record in records
        if record.get("type") == "fill" and record.get("order_id")
    }
    fill_rate = (len(submitted & filled) / len(submitted)) if submitted else None

    timestamps = [item[1] for item in series if item[1] > 0]
    span_days = ((max(timestamps) - min(timestamps)) / 86400.0) if len(timestamps) >= 2 else 0.0
    trades_per_day = (n / span_days) if span_days > 0 else None

    reasons: list[str] = []
    if n < min_resolved_trades:
        verdict = "INSUFFICIENT_SAMPLE"
        reasons.append(f"Resolved sample {n} < required minimum {min_resolved_trades}")
    else:
        verdict = "PASS"
        if net_total <= 0:
            verdict = "FAIL"
            reasons.append(f"Net PnL ${net_total:.2f} is not positive")
        if require_positive_ci and ci_low <= 0:
            verdict = "FAIL"
            reasons.append(
                "95% confidence interval on per-trade net PnL lower bound "
                f"${ci_low:.4f} <= 0 (edge not statistically distinguishable from break-even)"
            )
        if max_dd > max_drawdown_usdc:
            verdict = "FAIL"
            reasons.append(
                f"Max drawdown ${max_dd:.2f} exceeds budget ${max_drawdown_usdc:.2f}"
            )
        if verdict == "PASS":
            reasons.append("All Gate 1 criteria met")

    return {
        "resolved_trades": n,
        "net_pnl_total": net_total,
        "mean_net_pnl_per_trade": mean,
        "net_pnl_stdev": stdev,
        "net_pnl_ci95_low": ci_low,
        "net_pnl_ci95_high": ci_high,
        "win_rate": win_rate,
        "win_rate_ci95_low": wr_low,
        "win_rate_ci95_high": wr_high,
        "max_drawdown_usdc": max_dd,
        "fill_rate": fill_rate,
        "trades_per_day": trades_per_day,
        "span_days": span_days,
        "min_resolved_trades": min_resolved_trades,
        "max_drawdown_budget_usdc": max_drawdown_usdc,
        "verdict": verdict,
        "reasons": reasons,
    }


def render_gate1_summary(summary: dict) -> str:
    lines = [
        f"## Gate 1 verdict: {summary['verdict']}",
        "",
        f"- Resolved trades: {summary['resolved_trades']} (min required {summary['min_resolved_trades']})",
        f"- Net PnL: ${summary['net_pnl_total']:.2f}",
        (
            f"- Mean net PnL/trade: ${summary['mean_net_pnl_per_trade']:.4f} "
            f"(95% CI ${summary['net_pnl_ci95_low']:.4f} .. ${summary['net_pnl_ci95_high']:.4f})"
        ),
        (
            f"- Win rate: {summary['win_rate']:.1%} "
            f"(95% CI {summary['win_rate_ci95_low']:.1%} .. {summary['win_rate_ci95_high']:.1%})"
        ),
        (
            f"- Max drawdown: ${summary['max_drawdown_usdc']:.2f} "
            f"(budget ${summary['max_drawdown_budget_usdc']:.2f})"
        ),
    ]
    fill_rate = summary["fill_rate"]
    lines.append(f"- Fill rate: {fill_rate:.1%}" if fill_rate is not None else "- Fill rate: n/a")
    trades_per_day = summary["trades_per_day"]
    lines.append(
        f"- Trade frequency: {trades_per_day:.1f}/day over {summary['span_days']:.2f} days"
        if trades_per_day is not None
        else "- Trade frequency: n/a (insufficient time span)"
    )
    lines.append("")
    lines.extend(f"- {reason}" for reason in summary["reasons"])
    return "\n".join(lines)


def render_report(report: dict[str, dict[str, float]]) -> str:
    lines = [
        "| Model | Trades | Candidates | Accept Rate | Resolved | Win Rate | Brier | Pooled Brier | Cal Gap | Gross PnL | Est Fees | Net PnL | Avg Fill Slip (bps) | Avg Submit Slip (bps) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in sorted(report):
        row = report[model]
        lines.append(
            f"| {model} | {int(row.get('trades', 0))} | {int(row.get('candidate_count', 0))} | {row.get('acceptance_rate', 0.0):.1%} | "
            f"{int(row.get('resolved', 0))} | {row.get('win_rate', 0.0):.1%} | {row.get('brier_score', 0.0):.3f} | {row.get('pooled_brier_score', 0.0):.3f} | {row.get('calibration_error', 0.0):.3f} | "
            f"${row.get('gross_pnl', 0.0):.2f} | ${row.get('estimated_fees', 0.0):.2f} | ${row.get('net_pnl', 0.0):.2f} | "
            f"{row.get('avg_fill_slippage_bps', 0.0):.2f} | {row.get('avg_submit_slippage_bps', 0.0):.2f} |"
        )
    context = build_fee_context()
    lines.extend(
        [
            "",
            (
                "Fee context: crypto taker estimate used when live fill fees are absent. "
                f"feeRate={context['fee_rate']}, exponent={context['exponent']}."
            ),
            "Candidate metrics: acceptance rate, Brier score, pooled Brier score, and calibration gap are computed from logged candidate evaluations when a resolved window label is available.",
            f"Source: {context['source_url']}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="trades.jsonl")
    parser.add_argument(
        "--gate1", action="store_true", help="Also print the Gate 1 go/no-go summary"
    )
    parser.add_argument("--min-resolved", type=int, default=GATE1_MIN_RESOLVED_TRADES)
    parser.add_argument("--max-drawdown", type=float, default=GATE1_MAX_DRAWDOWN_USDC)
    args = parser.parse_args()
    records = load_jsonl(Path(args.ledger))
    print(render_report(build_report(records)))
    if args.gate1:
        summary = build_gate1_summary(
            records,
            min_resolved_trades=args.min_resolved,
            max_drawdown_usdc=args.max_drawdown,
        )
        print()
        print(render_gate1_summary(summary))


if __name__ == "__main__":
    main()
