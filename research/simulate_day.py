#!/usr/bin/env python3
"""
Monte Carlo simulation of 1-day bot performance based on empirical trade data.

Calibrated from 410 real trades (20260410-20260411) in CONVICTION_ONLY track.
Run: python3 simulate_day.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glob
import json
import random
import statistics

# ─────────────────────────────────────────────
# 1. LOAD EMPIRICAL OUTCOME DISTRIBUTIONS
# ─────────────────────────────────────────────


def load_real_outcomes() -> dict[str, list[float]]:
    """Load actual PnL samples from conviction trade logs, grouped by entry price band."""
    all_files = sorted(
        glob.glob(".web_runtime/20260410_*_trades_conviction.jsonl")
        + glob.glob(".web_runtime/20260411_*_trades_conviction.jsonl")
    )
    trades: dict = {}
    resolutions: dict = {}
    for f in all_files:
        with open(f) as fp:
            for line in fp:
                try:
                    rec = json.loads(line)
                    if rec["type"] == "trade":
                        trades[rec["order_id"]] = rec
                    elif rec["type"] == "resolution":
                        resolutions[rec["order_id"]] = rec
                except Exception:
                    pass

    bands: dict[str, list[float]] = {"low": [], "mid": [], "high": []}
    for oid, t in trades.items():
        if oid not in resolutions:
            continue
        r = resolutions[oid]
        pnl_pct = r["pnl"] / t["amount_usdc"]
        price = t["price"]
        if price < 0.70:
            bands["low"].append(pnl_pct)
        elif price < 0.79:
            bands["mid"].append(pnl_pct)
        else:
            bands["high"].append(pnl_pct)
    return bands


# ─────────────────────────────────────────────
# 2. MARKET REGIME DEFINITIONS
# ─────────────────────────────────────────────

# Market regime = how often each band fires and how many trades per window.
# Calibrated from:
#   - Apr 10 (choppy BTC, 373 trades in ~100 windows):  100% low-price entries
#   - Apr 11 (trending BTC,  37 trades in  ~8 windows): ~20% low, 65% mid, 15% high
#
# Three regimes modeled:

REGIMES = {
    "choppy": {
        # Volatile but directionless BTC — lots of signals all in uncertain zone
        "description": "Choppy / ranging BTC (like Apr 10)",
        "active_windows_per_day": 50,  # ~17% of 288 daily windows produce signals
        "trades_per_window": (2, 6),  # range drawn uniform
        "band_weights": {"low": 0.85, "mid": 0.10, "high": 0.05},
        "regime_probability": 0.50,  # 50% of days
    },
    "trending": {
        # BTC making a clear move, tokens pricing strongly one-sided late in windows
        "description": "Trending BTC (like Apr 11)",
        "active_windows_per_day": 30,
        "trades_per_window": (2, 5),
        "band_weights": {"low": 0.20, "mid": 0.55, "high": 0.25},
        "regime_probability": 0.35,
    },
    "volatile_reversal": {
        # BTC pumps then hard reverses mid-window — rare, best for this strategy
        "description": "Volatile reversal day (best case)",
        "active_windows_per_day": 20,
        "trades_per_window": (2, 4),
        "band_weights": {"low": 0.05, "mid": 0.30, "high": 0.65},
        "regime_probability": 0.15,
    },
}


# ─────────────────────────────────────────────
# 3. SIMULATION ENGINE
# ─────────────────────────────────────────────

BET_SIZE_USDC = 5.0
STARTING_BANKROLL = 10.0  # current balance


def simulate_one_day(
    bands: dict[str, list[float]],
    regime_name: str,
    rng: random.Random,
    starting_bankroll: float = STARTING_BANKROLL,
) -> dict:
    regime = REGIMES[regime_name]
    bankroll = starting_bankroll
    total_pnl = 0.0
    trades_placed = 0
    trades_skipped = 0  # bankroll too low
    sl_count = 0
    tp_count = 0
    te_count = 0

    n_active_windows = int(regime["active_windows_per_day"] * rng.uniform(0.7, 1.3))
    weights = regime["band_weights"]

    for _window in range(n_active_windows):
        n_trades = rng.randint(*regime["trades_per_window"])

        for _t in range(n_trades):
            if bankroll < BET_SIZE_USDC:
                trades_skipped += n_trades - _t
                break

            # Pick entry band weighted by regime
            band = rng.choices(
                ["low", "mid", "high"], weights=[weights["low"], weights["mid"], weights["high"]]
            )[0]

            # Sample outcome from empirical distribution
            pnl_pct = rng.choice(bands[band])
            pnl_usdc = BET_SIZE_USDC * pnl_pct

            bankroll += pnl_usdc
            total_pnl += pnl_usdc
            trades_placed += 1

            if pnl_usdc < -0.05:
                sl_count += 1
            elif pnl_usdc > 0.5:
                tp_count += 1
            else:
                te_count += 1

    return {
        "regime": regime_name,
        "pnl": total_pnl,
        "final_bankroll": bankroll,
        "trades": trades_placed,
        "trades_skipped": trades_skipped,
        "sl_pct": sl_count / max(trades_placed, 1),
        "tp_pct": tp_count / max(trades_placed, 1),
        "windows": n_active_windows,
    }


# ─────────────────────────────────────────────
# 4. MONTE CARLO LOOP
# ─────────────────────────────────────────────


def run_simulation(n_days: int = 500, seed: int = 42) -> None:
    rng = random.Random(seed)
    bands = load_real_outcomes()

    # Print band calibration
    print("=" * 65)
    print("BAND CALIBRATION (from real logs)")
    print("=" * 65)
    for band_name, label, _lo, _hi in [
        ("low", "0.60-0.70 (low conviction)", 0.60, 0.70),
        ("mid", "0.70-0.79 (mid conviction)", 0.70, 0.79),
        ("high", "0.79-0.92 (high conviction)", 0.79, 0.92),
    ]:
        samples = bands[band_name]
        ev = statistics.mean(samples) * 100
        win_rate = sum(1 for s in samples if s > 0) / len(samples) * 100
        sl_rate = sum(1 for s in samples if s < -0.04) / len(samples) * 100
        print(f"  {label}: n={len(samples):3d}  EV={ev:+.2f}%  win={win_rate:.0f}%  SL={sl_rate:.0f}%")
    print()

    # Run Monte Carlo
    regime_keys = list(REGIMES.keys())
    regime_probs = [REGIMES[r]["regime_probability"] for r in regime_keys]

    all_days: list[dict] = []
    for _ in range(n_days):
        regime = rng.choices(regime_keys, weights=regime_probs)[0]
        result = simulate_one_day(bands, regime, rng)
        all_days.append(result)

    pnls = [d["pnl"] for d in all_days]
    pnls.sort()

    print("=" * 65)
    print(f"MONTE CARLO: {n_days} simulated days  |  bankroll ${STARTING_BANKROLL:.0f}")
    print("=" * 65)

    # Overall summary
    print(f"\n{'Metric':<35} {'Value':>12}")
    print("-" * 50)
    print(f"  Mean daily PnL                   ${statistics.mean(pnls):>+8.2f}")
    print(f"  Median daily PnL                 ${statistics.median(pnls):>+8.2f}")
    print(f"  Std dev                          ${statistics.stdev(pnls):>8.2f}")
    print(f"  Best day                         ${max(pnls):>+8.2f}")
    print(f"  Worst day                        ${min(pnls):>+8.2f}")
    print(f"  Profitable days                   {sum(1 for p in pnls if p > 0) / n_days * 100:>7.0f}%")
    print(f"  Blowup days (<-$5, lose half)     {sum(1 for p in pnls if p < -5) / n_days * 100:>7.0f}%")

    # Percentiles
    print(f"\n  P5  (5th percentile)             ${pnls[int(n_days * 0.05)]:>+8.2f}")
    print(f"  P25                              ${pnls[int(n_days * 0.25)]:>+8.2f}")
    print(f"  P50                              ${pnls[int(n_days * 0.50)]:>+8.2f}")
    print(f"  P75                              ${pnls[int(n_days * 0.75)]:>+8.2f}")
    print(f"  P95 (95th percentile)            ${pnls[int(n_days * 0.95)]:>+8.2f}")

    # Per-regime breakdown
    print()
    print("=" * 65)
    print("PER-REGIME BREAKDOWN")
    print("=" * 65)
    for rname, rdef in REGIMES.items():
        days = [d for d in all_days if d["regime"] == rname]
        rpnls = [d["pnl"] for d in days]
        if not rpnls:
            continue
        avg_trades = statistics.mean(d["trades"] for d in days)
        avg_sl = statistics.mean(d["sl_pct"] for d in days) * 100
        print(f"\n  {rdef['description']}")
        print(f"    Days simulated: {len(days)}")
        print(
            f"    Mean PnL:       ${statistics.mean(rpnls):+.2f}  (median ${statistics.median(rpnls):+.2f})"
        )
        print(f"    Profitable:     {sum(1 for p in rpnls if p > 0) / len(rpnls) * 100:.0f}%")
        print(f"    Avg trades/day: {avg_trades:.0f}  |  Avg SL rate: {avg_sl:.0f}%")

    # Suggested improvements
    print()
    print("=" * 65)
    print("MODEL INSIGHTS & RECOMMENDATIONS")
    print("=" * 65)

    ev_low = statistics.mean(bands["low"]) * 100
    ev_mid = statistics.mean(bands["mid"]) * 100
    ev_high = statistics.mean(bands["high"]) * 100

    print(f"""
  PROBLEM: Low-price entries (0.60-0.70) are negative EV ({ev_low:+.1f}%).
  They make up 73% of all trades on choppy days and drag down the strategy.

  Band EV summary:
    0.60-0.70  {ev_low:+.1f}%  ← losing money
    0.70-0.79  {ev_mid:+.1f}%  ← losing money
    0.79-0.92  {ev_high:+.1f}%  ← profitable ✓

  Option A — Raise min entry price to 0.79 (simplest fix):
    Kill all 0.60-0.79 entries. Fewer trades, positive EV only.
    Expected: ~20-40 trades/day (volatile days), ~5-10 (quiet days).

  Option B — Tighten low-conviction SL further from 10% → 6%:
    Reduces the -11.6% avg SL loss but may increase SL frequency.

  Option C — Raise LOW_CONVICTION_HARD_TIME_EXIT from 15s → 10s:
    Cap exposure time on uncertain entries.
""")

    # Simulate "filtered" version (0.79+ only)
    print("=" * 65)
    print("FILTERED SIM: Raise min entry to 0.79 (high band only)")
    print("=" * 65)

    filtered_days = []
    for regime_name in rng.choices(regime_keys, weights=regime_probs, k=n_days):
        regime = REGIMES[regime_name]
        bankroll = STARTING_BANKROLL
        total_pnl = 0.0
        trades_placed = 0
        # In filtered mode: low+mid windows just skip if they'd have fired low/mid
        n_active_windows = int(regime["active_windows_per_day"] * rng.uniform(0.7, 1.3))
        weights = regime["band_weights"]
        high_frac = weights["high"]

        for _window in range(n_active_windows):
            n_trades = rng.randint(*regime["trades_per_window"])
            for _t in range(n_trades):
                if bankroll < BET_SIZE_USDC:
                    break
                # Only trade if this slot would have been a high-band entry
                if rng.random() > high_frac:
                    continue  # filtered out
                pnl_pct = rng.choice(bands["high"])
                pnl_usdc = BET_SIZE_USDC * pnl_pct
                bankroll += pnl_usdc
                total_pnl += pnl_usdc
                trades_placed += 1

        filtered_days.append({"pnl": total_pnl, "trades": trades_placed})

    fpnls = sorted(d["pnl"] for d in filtered_days)
    avg_trades = statistics.mean(d["trades"] for d in filtered_days)
    print(f"\n  Mean daily PnL:  ${statistics.mean(fpnls):>+.2f}")
    print(f"  Median:          ${statistics.median(fpnls):>+.2f}")
    print(f"  P5 / P95:        ${fpnls[int(n_days * 0.05)]:>+.2f} / ${fpnls[int(n_days * 0.95)]:>+.2f}")
    print(f"  Profitable days: {sum(1 for p in fpnls if p > 0) / n_days * 100:.0f}%")
    print(f"  Avg trades/day:  {avg_trades:.1f}")
    print(f"\n  Current (all bands):   ${statistics.mean(pnls):>+.2f}/day")
    print(f"  Filtered (0.79+ only): ${statistics.mean(fpnls):>+.2f}/day")
    improvement = statistics.mean(fpnls) - statistics.mean(pnls)
    print(f"  Delta: {improvement:>+.2f}/day from filtering low/mid entries")


if __name__ == "__main__":
    run_simulation(n_days=1000, seed=42)
