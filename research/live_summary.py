"""Summarize the paper run into public status files.

Reads the paper ledgers, runtime_status.json and jev_calls.jsonl from the bot
state directory and writes aggregate numbers only: status/live.json, the
shields.io endpoint files status/badge-*.json, and status/LIVE.md. No order
ids, wallet data, keys or market slugs are written.

Usage:
    python research/live_summary.py STATE_DIR [--out status]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.report_performance import estimate_crypto_taker_fee_usdc  # noqa: E402

BANNER = "> **Paper trading only. No real money is at risk. This page makes no claim of edge.**"
HEARTBEAT_STALE_SECONDS = 120
BOOTSTRAP_MIN_N = 20
BOOTSTRAP_RESAMPLES = 2000
Z95 = 1.959963984540054


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def wilson_interval(wins: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    z2 = Z95 * Z95
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_sum_ci(values: list[float], seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    sums = sorted(sum(rng.choice(values) for _ in range(n)) for _ in range(BOOTSTRAP_RESAMPLES))
    low = sums[int(0.025 * (len(sums) - 1))]
    high = sums[int(0.975 * (len(sums) - 1))]
    return (low, high)


def _load_ledgers(
    state: Path,
) -> tuple[list[tuple[dict, dict]], int, int, list[float]]:
    """Return settled (trade, resolution) pairs, trade count, distinct windows, timestamps."""
    trades: dict[tuple[str, str], dict] = {}
    resolutions: dict[tuple[str, str], dict] = {}
    windows: set[int] = set()
    stamps: list[float] = []
    for path in sorted((state / "ledgers").glob("*.jsonl")):
        for rec in _read_jsonl(path):
            kind = rec.get("type")
            ts = _num(rec.get("ts"))
            if ts is not None:
                stamps.append(ts)
            wend = _num(rec.get("window_end_ts"))
            if kind in {"trade", "candidate", "window_outcome"} and wend is not None:
                windows.add(int(wend))
            order_id = rec.get("order_id")
            if not order_id:
                continue
            key = (path.name, str(order_id))
            if kind == "trade":
                trades[key] = rec
            elif kind == "resolution":
                resolutions[key] = rec
    settled = [
        (trades[key], res)
        for key, res in resolutions.items()
        if key in trades and _num(res.get("pnl")) is not None
    ]
    return settled, len(trades), len(windows), stamps


def _load_jev(path: Path) -> tuple[list[dict], list[float]]:
    records = _read_jsonl(path)
    stamps = [ts for ts in (_num(r.get("ts")) for r in records) if ts is not None]
    return records, stamps


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_summary(state: Path, now: float) -> dict:
    status = _read_json(state / "runtime_status.json")
    if str(status.get("mode") or "") == "LIVE":
        raise RuntimeError("refusing to publish: runtime mode is LIVE")

    settled, trade_count, windows_observed, ledger_stamps = _load_ledgers(state)
    jev_records, jev_stamps = _load_jev(state / "jev_calls.jsonl")

    gross_values: list[float] = []
    net_values: list[float] = []
    briers_model: list[float] = []
    briers_market: list[float] = []
    for trade, res in settled:
        pnl = float(res["pnl"])
        amount = _num(trade.get("amount_usdc")) or 0.0
        price = _num(trade.get("price")) or 0.0
        fee = estimate_crypto_taker_fee_usdc(price=price, usdc_notional=amount)
        gross_values.append(pnl)
        net_values.append(pnl - fee)
        resolved_up = res.get("resolved_up")
        edge = _num(trade.get("edge"))
        if not isinstance(resolved_up, bool) or edge is None or not 0 < price < 1:
            continue
        won = (str(trade.get("direction")) == "UP") == resolved_up
        outcome = 1.0 if won else 0.0
        model_p = min(1.0, max(0.0, price + edge))
        briers_model.append((model_p - outcome) ** 2)
        briers_market.append((price - outcome) ** 2)

    settled_n = len(gross_values)
    wins = sum(1 for p in gross_values if p > 0)
    win_low, win_high = wilson_interval(wins, settled_n)
    gross_pnl = sum(gross_values)
    net_pnl = sum(net_values)
    fees = gross_pnl - net_pnl
    pnl_ci: list[float] | None = None
    if settled_n >= BOOTSTRAP_MIN_N:
        low, high = bootstrap_sum_ci(net_values)
        pnl_ci = [round(low, 4), round(high, 4)]

    latencies: list[float] = []
    for r in jev_records:
        latency = _num(r.get("latency_ms"))
        if r.get("api_call") is True and latency is not None:
            latencies.append(latency)
    jev_calls = sum(1 for r in jev_records if r.get("api_call") is True)

    heartbeat = _num(status.get("generated_ts"))
    heartbeat_age = None if heartbeat is None else max(0.0, now - heartbeat)
    raw_freshness = status.get("freshness")
    freshness: dict = raw_freshness if isinstance(raw_freshness, dict) else {}
    feeds_healthy = (
        freshness.get("btc_price_quorum_met") is True
        and freshness.get("btc_price_stale") is False
        and freshness.get("polymarket_book_stale") is not True
        and heartbeat_age is not None
        and heartbeat_age <= HEARTBEAT_STALE_SECONDS
    )
    stamps = ledger_stamps + jev_stamps
    observed_since = min(stamps) if stamps else None
    observed_hours = 0.0 if observed_since is None else max(0.0, now - observed_since) / 3600

    return {
        "updated_at": _iso(now),
        "banner": "Paper trading only. No real money. No claim of edge.",
        "uptime": {
            "observed_since_utc": _iso(observed_since),
            "observed_hours": round(observed_hours, 2),
            "last_heartbeat_utc": _iso(heartbeat),
            "heartbeat_age_seconds": None if heartbeat_age is None else round(heartbeat_age, 1),
            "bot_up": heartbeat_age is not None and heartbeat_age <= HEARTBEAT_STALE_SECONDS,
        },
        "feeds_healthy": feeds_healthy,
        "windows_observed": windows_observed,
        "paper_trades": trade_count,
        "settled_trades": settled_n,
        "win_rate": {
            "wins": wins,
            "rate": round(wins / settled_n, 4) if settled_n else None,
            "wilson95": [round(win_low, 4), round(win_high, 4)] if settled_n else None,
        },
        "net_pnl_usdc": {
            "net": round(net_pnl, 4),
            "gross": round(gross_pnl, 4),
            "est_fees": round(fees, 4),
            "bootstrap95": pnl_ci,
        },
        "brier": {
            "n": len(briers_model),
            "model": round(statistics.fmean(briers_model), 4) if briers_model else None,
            "market_baseline": round(statistics.fmean(briers_market), 4) if briers_market else None,
        },
        "jev": {
            "records": len(jev_records),
            "api_calls": jev_calls,
            "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else None,
        },
    }


def _pnl_badge(summary: dict) -> dict:
    net = summary["net_pnl_usdc"]["net"]
    sign = "+" if net >= 0 else "-"
    return {
        "schemaVersion": 1,
        "label": "paper PnL",
        "message": f"{sign}{abs(net):.2f} USDC",
        "color": "brightgreen" if net >= 0 else "red",
    }


def _trades_badge(summary: dict) -> dict:
    return {
        "schemaVersion": 1,
        "label": "paper trades",
        "message": f"{summary['paper_trades']} ({summary['settled_trades']} settled)",
        "color": "blue",
    }


def _uptime_badge(summary: dict) -> dict:
    up = summary["uptime"]
    up_now = up["bot_up"]
    return {
        "schemaVersion": 1,
        "label": "paper uptime",
        "message": f"{up['observed_hours']:.0f}h observed" if up_now else "down",
        "color": "brightgreen" if up_now else "red",
    }


def _updated_badge(summary: dict) -> dict:
    return {
        "schemaVersion": 1,
        "label": "live updated",
        "message": summary["updated_at"],
        "color": "blue",
    }


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(summary: dict) -> str:
    up = summary["uptime"]
    win = summary["win_rate"]
    pnl = summary["net_pnl_usdc"]
    brier = summary["brier"]
    jev = summary["jev"]
    wilson = win["wilson95"]
    boot = pnl["bootstrap95"]
    lines = [
        "# Live paper run",
        "",
        BANNER,
        "",
        f"Updated: {summary['updated_at']} (every 15 minutes from the VM)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Observed since | {_fmt(up['observed_since_utc'])} ({_fmt(up['observed_hours'], 1)} h) |",
        f"| Last heartbeat | {_fmt(up['last_heartbeat_utc'])} (age {_fmt(up['heartbeat_age_seconds'], 1)} s) |",
        f"| Feeds healthy | {'yes' if summary['feeds_healthy'] else 'no'} |",
        f"| Windows observed | {summary['windows_observed']} |",
        f"| Paper trades | {summary['paper_trades']} |",
        f"| Settled trades | {summary['settled_trades']} |",
        f"| Win rate | {_fmt(win['rate'])} (Wilson 95%: {_fmt(wilson[0]) + ' to ' + _fmt(wilson[1]) if wilson else 'n/a'}) |",
        f"| Net PnL, paper (USDC) | {_fmt(pnl['net'])} after est. fees (gross {_fmt(pnl['gross'])}) |",
        f"| Net PnL 95% bootstrap (USDC) | {(_fmt(boot[0]) + ' to ' + _fmt(boot[1])) if boot else 'needs 20+ settled trades'} |",
        f"| Brier, model vs market ({brier['n']} settled) | {_fmt(brier['model'])} vs {_fmt(brier['market_baseline'])} |",
        f"| JEV calls / latency p50 | {jev['api_calls']} / {_fmt(jev['latency_ms_p50'], 1)} ms |",
        "",
        "Paper results only. Fees are an estimate. Small samples do not establish an edge.",
        "",
    ]
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_outputs(summary: dict, out: Path) -> None:
    _write_text(out / "live.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    badges = {
        "badge-pnl.json": _pnl_badge(summary),
        "badge-trades.json": _trades_badge(summary),
        "badge-uptime.json": _uptime_badge(summary),
        "badge-updated.json": _updated_badge(summary),
    }
    for name, payload in badges.items():
        _write_text(out / name, json.dumps(payload, indent=2) + "\n")
    _write_text(out / "LIVE.md", render_markdown(summary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("state_dir", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "status")
    args = parser.parse_args(argv)
    try:
        summary = build_summary(args.state_dir, time.time())
    except RuntimeError as exc:
        print(f"live_summary: {exc}", file=sys.stderr)
        return 2
    write_outputs(summary, args.out)
    print(f"live_summary: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
