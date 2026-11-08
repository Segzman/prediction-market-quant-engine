#!/usr/bin/env python3
"""
Stop-Loss Strategy Simulation
Analyzes all trading log files and compares 3 SL strategies.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glob
import os
import re
from collections import defaultdict

LOG_DIR = ".web_runtime"
BET_SIZE = 5.0  # dollars per trade

# ── Regex patterns ─────────────────────────────────────────────────────────────

# "Exit triggered for [Trade #N | ]<order_id> | Reason: X | Entry: Y | Exit: Z | PnL: W%"
EXIT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+INFO\s+bot:\s+"
    r"Exit triggered for\s+(?:Trade #\d+\s*\|\s*)?"
    r"(dry-run-[\w-]+)\s*\|\s*"
    r"Reason:\s*([\w_]+)\s*\|\s*"
    r"Entry:\s*([\d.]+)\s*\|\s*"
    r"Exit:\s*([\d.]+)\s*\|\s*"
    r"PnL:\s*(-?[\d.]+)%"
)

# "Placed UP/DOWN [(...)] | ... | market=X | ... | order=<id>"
PLACED_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+INFO\s+bot:\s+"
    r"Placed\s+(UP|DOWN)(?:\s+\([^)]+\))?\s*\|.*?"
    r"market=([\d.]+)\s*\|.*?"
    r"order=(dry-run-[\w-]+)"
)

# ── Parsing ────────────────────────────────────────────────────────────────────


def parse_logs():
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")))
    if not log_files:
        print(f"ERROR: No .log files found in {LOG_DIR}")
        return [], {}

    exits = {}  # order_id -> trade dict (first occurrence wins)
    placements = {}  # order_id -> placement dict

    total_exit_lines = 0
    total_placed_lines = 0
    malformed = 0

    for lf in log_files:
        try:
            with open(lf, errors="replace") as fh:
                for line in fh:
                    # ---- EXIT lines ----
                    m = EXIT_RE.match(line)
                    if m:
                        total_exit_lines += 1
                        ts_str, order_id, reason, entry_s, exit_s, pnl_s = m.groups()
                        if order_id not in exits:  # deduplicate: keep first
                            try:
                                exits[order_id] = {
                                    "timestamp": ts_str,
                                    "order_id": order_id,
                                    "reason": reason,
                                    "entry": float(entry_s),
                                    "exit": float(exit_s),
                                    "pnl": float(pnl_s),
                                    "log_file": os.path.basename(lf),
                                }
                            except ValueError:
                                malformed += 1
                        continue

                    # ---- PLACED lines ----
                    m2 = PLACED_RE.match(line)
                    if m2:
                        total_placed_lines += 1
                        ts_str, direction, market_s, order_id = m2.groups()
                        if order_id not in placements:
                            try:
                                placements[order_id] = {
                                    "direction": direction,
                                    "market_price": float(market_s),
                                }
                            except ValueError:
                                malformed += 1
        except OSError as e:
            print(f"  WARNING: could not read {lf}: {e}")

    trades = list(exits.values())
    print(f"Log files scanned  : {len(log_files)}")
    print(f"Raw exit lines     : {total_exit_lines}")
    print(f"Unique trades      : {len(trades)}")
    print(f"Raw placed lines   : {total_placed_lines}")
    print(f"Unique placements  : {len(placements)}")
    print(f"Malformed lines    : {malformed}")
    print()
    return trades, placements


# ── Strategy simulation ────────────────────────────────────────────────────────

SL_THRESHOLD = -4.0  # configured SL level (%)
SL_PRICE_MULT = 0.96  # entry * 0.96
INSTANT_MULT = 0.9456  # entry * 0.96 * 0.985  (tiered instant threshold)
DEEP_BREACH_PCT = -5.56  # if actual pnl < this => price dropped >1.5% past SL


def simulate_strategies(trades):
    """Return three lists of (strategy_name, trade_pnl) per strategy."""
    strat_a, strat_b, strat_c = [], [], []

    for t in trades:
        actual_pnl = t["pnl"]
        reason = t["reason"]
        entry = t["entry"]

        # Strategy A: baseline — always actual
        strat_a.append({**t, "sim_pnl": actual_pnl, "sim_exit": t["exit"]})

        if reason == "STOP_LOSS":
            # Strategy B: instant SL at -4%
            sim_exit_b = entry * SL_PRICE_MULT
            sim_pnl_b = (sim_exit_b / entry - 1) * 100  # exactly -4%
            strat_b.append({**t, "sim_pnl": sim_pnl_b, "sim_exit": sim_exit_b})

            # Strategy C: tiered
            if actual_pnl < DEEP_BREACH_PCT:
                # Deep breach: simulate as caught at instant threshold
                sim_exit_c = entry * INSTANT_MULT
                sim_pnl_c = (INSTANT_MULT - 1) * 100  # -5.44%
            else:
                # Borderline (between -4% and -5.56%): use actual
                sim_pnl_c = actual_pnl
                sim_exit_c = t["exit"]
            strat_c.append({**t, "sim_pnl": sim_pnl_c, "sim_exit": sim_exit_c})
        else:
            # Non-SL exits: unchanged across all strategies
            strat_b.append({**t, "sim_pnl": actual_pnl, "sim_exit": t["exit"]})
            strat_c.append({**t, "sim_pnl": actual_pnl, "sim_exit": t["exit"]})

    return strat_a, strat_b, strat_c


# ── Statistics ─────────────────────────────────────────────────────────────────


def bucket_label(pnl_pct):
    """Return magnitude bucket string for SL exits."""
    mag = abs(pnl_pct)
    if mag < 5:
        return "0–5%"
    elif mag < 10:
        return "5–10%"
    elif mag < 15:
        return "10–15%"
    elif mag < 20:
        return "15–20%"
    else:
        return "20%+"


def compute_stats(trades):
    n = len(trades)
    if n == 0:
        return {}

    pnls = [t["sim_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]

    sl_trades = [t for t in trades if t["reason"] == "STOP_LOSS"]
    tp_trades = [t for t in trades if t["reason"] == "TAKE_PROFIT"]
    other_trades = [t for t in trades if t["reason"] not in ("STOP_LOSS", "TAKE_PROFIT")]

    sl_pnls = [t["sim_pnl"] for t in sl_trades]
    tp_pnls = [t["sim_pnl"] for t in tp_trades]

    total_dollar_pnl = sum(p / 100 * BET_SIZE for p in pnls)

    # SL magnitude buckets
    buckets = defaultdict(int)
    for t in sl_trades:
        buckets[bucket_label(t["sim_pnl"])] += 1

    reason_counts = defaultdict(int)
    for t in trades:
        reason_counts[t["reason"]] += 1

    return {
        "n": n,
        "win_rate": len(wins) / n * 100 if n else 0,
        "avg_pnl": sum(pnls) / n if n else 0,
        "total_dollar_pnl": total_dollar_pnl,
        "avg_sl_pnl": sum(sl_pnls) / len(sl_pnls) if sl_pnls else float("nan"),
        "avg_tp_pnl": sum(tp_pnls) / len(tp_pnls) if tp_pnls else float("nan"),
        "sl_count": len(sl_trades),
        "tp_count": len(tp_trades),
        "other_count": len(other_trades),
        "worst": min(pnls),
        "best": max(pnls),
        "buckets": dict(buckets),
        "reason_counts": dict(reason_counts),
    }


# ── Pretty printing ────────────────────────────────────────────────────────────

BUCKET_ORDER = ["0–5%", "5–10%", "10–15%", "15–20%", "20%+"]


def print_report(stats_a, stats_b, stats_c):
    strategies = [
        ("A  Baseline (actual)", stats_a),
        ("B  Instant SL (-4%)", stats_b),
        ("C  Tiered SL", stats_c),
    ]

    sep = "=" * 80
    thin = "-" * 80

    print(sep)
    print("  STOP-LOSS STRATEGY COMPARISON — All Unique Trades")
    print(sep)

    # Wide table
    col_w = 22
    hdr_fmt = f"  {'Metric':<28}" + "".join(f"  {s[0]:<{col_w}}" for s in strategies)
    print(hdr_fmt)
    print(thin)

    def row(label, fn):
        vals = "".join(f"  {fn(s):<{col_w}}" for _, s in strategies)
        print(f"  {label:<28}{vals}")

    row("Total unique trades", lambda s: str(s["n"]))
    row("Win rate", lambda s: f"{s['win_rate']:.1f}%")
    row("Avg PnL per trade", lambda s: f"{s['avg_pnl']:+.2f}%")
    row("Total P&L ($5/trade)", lambda s: f"${s['total_dollar_pnl']:+.2f}")
    row("Best single trade", lambda s: f"{s['best']:+.2f}%")
    row("Worst single trade", lambda s: f"{s['worst']:+.2f}%")
    print(thin)
    row("STOP_LOSS count", lambda s: str(s["sl_count"]))
    row("Avg SL PnL", lambda s: f"{s['avg_sl_pnl']:+.2f}%")
    row("TAKE_PROFIT count", lambda s: str(s["tp_count"]))
    row("Avg TP PnL", lambda s: f"{s['avg_tp_pnl']:+.2f}%")
    row("TIME_EXIT / other", lambda s: str(s["other_count"]))
    print(thin)

    # Reason breakdown
    all_reasons = sorted({r for _, s in strategies for r in s["reason_counts"]})
    for reason in all_reasons:
        row(f"  {reason}", lambda s, r=reason: str(s["reason_counts"].get(r, 0)))
    print(thin)

    # SL magnitude buckets
    print(f"  {'SL Exit Magnitude Buckets':<28}")
    for b in BUCKET_ORDER:
        row(f"    {b}", lambda s, bk=b: str(s["buckets"].get(bk, 0)))

    print(sep)

    # Delta summary
    print()
    print("  DELTA vs BASELINE  (Strategy X minus Strategy A)")
    print(thin)
    for name, s in strategies[1:]:
        d_avg = s["avg_pnl"] - stats_a["avg_pnl"]
        d_dol = s["total_dollar_pnl"] - stats_a["total_dollar_pnl"]
        d_sl = s["avg_sl_pnl"] - stats_a["avg_sl_pnl"]
        d_wr = s["win_rate"] - stats_a["win_rate"]
        print(f"  Strategy {name.strip()}")
        print(f"    Avg PnL change     : {d_avg:+.2f}%  ({d_wr:+.1f}pp win rate)")
        print(f"    Avg SL PnL change  : {d_sl:+.2f}%")
        print(f"    Total $ P&L change : ${d_dol:+.2f}")
        print()

    print(sep)


# ── Deep-dive: individual SL trades ───────────────────────────────────────────


def print_sl_details(trades_a, trades_b, trades_c):
    # Build lookup by order_id
    b_by_id = {t["order_id"]: t for t in trades_b}
    c_by_id = {t["order_id"]: t for t in trades_c}

    sl_trades = [t for t in trades_a if t["reason"] == "STOP_LOSS"]
    sl_trades.sort(key=lambda t: t["pnl"])  # worst first

    print()
    print("=" * 100)
    print("  STOP_LOSS INDIVIDUAL TRADE DETAIL  (sorted worst → best actual PnL)")
    print("=" * 100)
    hdr = f"  {'Order ID':<44} {'Entry':>6} {'Exit':>6} {'Act PnL':>8} {'Strat B':>8} {'Strat C':>8} {'Cat'}"
    print(hdr)
    print("-" * 100)

    for t in sl_trades:
        oid = t["order_id"]
        b = b_by_id.get(oid, {})
        c = c_by_id.get(oid, {})
        act_pnl = t["pnl"]
        b_pnl = b.get("sim_pnl", float("nan"))
        c_pnl = c.get("sim_pnl", float("nan"))

        # Category for strategy C
        if act_pnl < DEEP_BREACH_PCT:
            cat = "DEEP"
        else:
            cat = "BORDER"

        print(
            f"  {oid:<44} {t['entry']:>6.3f} {t['exit']:>6.3f} "
            f"{act_pnl:>+7.2f}% {b_pnl:>+7.2f}% {c_pnl:>+7.2f}%  {cat}"
        )

    print()
    print(f"  DEEP   = actual PnL < {DEEP_BREACH_PCT}%  → price fell >1.5% past SL before exit")
    print(f"  BORDER = actual PnL between {SL_THRESHOLD}% and {DEEP_BREACH_PCT}%")
    print("=" * 100)


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    print()
    print("=" * 80)
    print("  Parsing log files …")
    print("=" * 80)
    trades, placements = parse_logs()

    if not trades:
        print("No trades found. Exiting.")
        return

    strat_a, strat_b, strat_c = simulate_strategies(trades)

    stats_a = compute_stats(strat_a)
    stats_b = compute_stats(strat_b)
    stats_c = compute_stats(strat_c)

    print_report(stats_a, stats_b, stats_c)
    print_sl_details(strat_a, strat_b, strat_c)

    # Sanity check: all three lists should have same length
    assert len(strat_a) == len(strat_b) == len(strat_c), "Strategy list length mismatch!"


if __name__ == "__main__":
    main()
