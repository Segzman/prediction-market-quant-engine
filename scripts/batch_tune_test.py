"""Batch tune/test over historical ledgers. Tune on old windows, test on new.

Usage:
    python scripts/batch_tune_test.py [--glob '*.jsonl'] [--split 2026-10-01]

Loads all candidates, dedups to last-tick-per-window, backfills window
outcomes from Binance 1m closes (cached), splits windows chronologically at
--split, grids (price_min x conv_min) on TUNE and reports the same configs
on TEST. Metric 1 = directional win-rate (hold proxy). Metric 2 = real
exit-aware trade PnL per batch from trade/resolution records.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from replay_conviction import backfill_outcomes, load_records

PRICE_GRID = (0.55, 0.65, 0.72, 0.79)
CONV_GRID = (0.60, 0.69, 0.80)
STAKE = 5.0


def last_tick_per_window(candidates: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for cand in candidates:
        wts = cand.get("window_end_ts")
        if not isinstance(wts, int):
            continue
        key = (wts, cand.get("track"), cand.get("model"))
        if key not in best or (cand.get("ts") or 0) > (best[key].get("ts") or 0):
            best[key] = cand
    return list(best.values())


def strong_side_price(cand: dict) -> float | None:
    px = cand.get("down_price") if cand.get("strongest_side") == "DOWN" else cand.get("up_price")
    return float(px) if isinstance(px, (int, float)) else None


def label_rows(cands: list[dict], resolutions: dict[int, dict]) -> list[dict]:
    rows = []
    for cand in cands:
        outcome = resolutions.get(cand["window_end_ts"])
        if outcome is None or outcome.get("resolved_up") is None:
            continue
        px = strong_side_price(cand)
        if px is None or not cand.get("open_price"):
            continue
        side = cand.get("strongest_side")
        if side not in {"UP", "DOWN"}:
            continue
        rows.append({
            "w": cand["window_end_ts"],
            "px": px,
            "conv": float(cand.get("market_conviction") or 0.0),
            "won": (side == "UP") == bool(outcome["resolved_up"]),
        })
    return rows


def score(rows: list[dict], price_min: float, conv_min: float) -> tuple[int, float, float] | None:
    subset = [r for r in rows if r["px"] >= price_min and r["conv"] >= conv_min]
    if len(subset) < 8:
        return None
    wins = sum(1 for r in subset if r["won"])
    wr = wins / len(subset)
    avg = sum(r["px"] for r in subset) / len(subset)
    hold_ev = (wr * (1 - avg) - (1 - wr) * avg) * STAKE
    return len(subset), wr, hold_ev


def real_trade_pnl(pattern_files: list[str], since: int, until: int) -> dict[str, dict]:
    import glob as globmod
    import json

    trades: dict[str, dict] = {}
    paths = set()
    for pattern in pattern_files:
        paths.update(globmod.glob(pattern))
    for path in sorted(paths):
        try:
            with open(path) as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "trade" and record.get("order_id"):
                trades.setdefault(record["order_id"], {}).update({
                    "model": record.get("model"),
                    "w": record.get("window_end_ts"),
                })
            elif record.get("type") == "resolution" and record.get("order_id"):
                if isinstance(record.get("pnl"), (int, float)) and record.get(
                    "uma_resolution_status"
                ) != "SIMULATED":
                    trades.setdefault(record["order_id"], {})["pnl"] = float(record["pnl"])
    by_model: dict[str, dict] = {}
    for trade in trades.values():
        pnl, wts = trade.get("pnl"), trade.get("w")
        if not isinstance(pnl, float) or not isinstance(wts, int):
            continue
        if not (since <= wts < until):
            continue
        bucket = by_model.setdefault(str(trade.get("model")), {"n": 0, "net": 0.0})
        bucket["n"] += 1
        bucket["net"] += pnl
    return by_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default="*.jsonl")
    parser.add_argument("--split", default="2026-10-01")
    args = parser.parse_args()

    candidates, resolutions = load_records(args.glob)
    filled = backfill_outcomes(candidates, resolutions)
    rows = label_rows(last_tick_per_window(candidates), resolutions)
    rows.sort(key=lambda r: r["w"])
    split_ts = int(datetime.fromisoformat(args.split).replace(tzinfo=UTC).timestamp())
    tune = [r for r in rows if r["w"] < split_ts]
    test = [r for r in rows if r["w"] >= split_ts]
    print(f"windows={len(rows)} tune={len(tune)} test={len(test)} (backfilled={filled})")

    ranked = []
    for price_min in PRICE_GRID:
        for conv_min in CONV_GRID:
            tune_score = score(tune, price_min, conv_min)
            if tune_score is None:
                continue
            ranked.append((tune_score[1], price_min, conv_min, tune_score,
                           score(test, price_min, conv_min)))
    ranked.sort(key=lambda item: -item[0])
    print(
        f"{'config':<22} {'tune n':>6} {'tune win':>8} {'tuneEV':>7} | "
        f"{'test n':>6} {'test win':>8} {'testEV':>7}"
    )
    for _, price_min, conv_min, tune_score, test_score in ranked:
        test_text = (
            f"{test_score[0]:>6} {100 * test_score[1]:>7.0f}% {test_score[2]:>+7.2f}"
            if test_score else "   thin"
        )
        print(
            f"px>={price_min:<5} conv>={conv_min:<4} {tune_score[0]:>6} "
            f"{100 * tune_score[1]:>7.0f}% {tune_score[2]:>+7.2f} | {test_text}"
        )

    print("real exit-aware PnL:")
    for name, since, until in (("TUNE", 0, split_ts), ("TEST", split_ts, 2**40)):
        for model, bucket in sorted(
            real_trade_pnl([args.glob, ".web_runtime/*.jsonl"], since, until).items()
        ):
            avg = bucket["net"] / bucket["n"] if bucket["n"] else 0.0
            print(f"  {name} model {model}: n={bucket['n']} net=${bucket['net']:+.2f} avg=${avg:+.3f}")


if __name__ == "__main__":
    main()
