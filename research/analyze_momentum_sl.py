"""
Momentum-aware SL suppression simulation.

For each SL exit, use the entry-time ModelContext to compute whether
compute_sl_confirmation_seconds() would have extended the confirmation window,
then classify as:
  - HARD_CRASH: exit price >> SL floor (polling lag caused deep loss, suppression irrelevant)
  - BORDERLINE:  exit price close to SL floor (confirmation window triggered the exit)

For borderline SLs, simulate three scenarios:
  A. Baseline:   2s confirmation window (current behaviour)
  B. Suppressed: extended window when momentum score >= 0.65
     - Optimistic: suppressed SLs all recover to break-even
     - Pessimistic: suppressed SLs continue falling another SL_PCT (doubled loss)
     - Neutral:    50/50 split

Limitation: logs record momentum at entry, not at SL trigger time. The simulation
treats entry momentum as a proxy for momentum at SL trigger, which slightly
over-estimates suppression applicability.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glob
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Config (mirror config.py defaults) ──────────────────────────────────────
EXIT_STOP_LOSS_PCT = 0.04          # SL at entry_price * (1 - 0.04)
SL_INSTANT_BREACH_PCT = 0.015      # > this below SL → hard crash, never suppress
SL_BASE_CONFIRMATION_SECONDS = 2.0
MOMENTUM_SL_SUPPRESS_THRESHOLD = 0.65
MOMENTUM_SL_MAX_WINDOW_SECONDS = 10.0
BORDERLINE_BAND = 0.02             # exit within 2% of SL floor = borderline


# ── Inline suppression scorer (mirrors strategy/signal.py) ──────────────────
def _suppression_score(direction: str, snap: dict) -> float:
    """Compute suppression score [0–1] from entry feature_snapshot."""
    consistency = max(0.0, min(1.0, snap.get("directional_consistency", 0.5)))
    if direction == "UP":
        mom = max(0.0, snap.get("up_price_momentum", 0.0))
        accel = snap.get("up_price_acceleration", 0.0)
    else:
        mom = max(0.0, snap.get("down_price_momentum", 0.0))
        accel = snap.get("down_price_acceleration", 0.0)

    if accel < 0:
        return 0.0  # exhaustion guard

    norm_mom = min(mom / 0.05, 1.0)
    conviction = max(0.0, min(1.0, snap.get("market_conviction", 0.0)))
    btc = 1.0 if snap.get("btc_poly_agreement", 0.0) > 0 else 0.0

    return 0.35 * consistency + 0.25 * norm_mom + 0.25 * conviction + 0.15 * btc


def _extended_window(direction: str, snap: dict) -> float:
    score = _suppression_score(direction, snap)
    if score < MOMENTUM_SL_SUPPRESS_THRESHOLD:
        return SL_BASE_CONFIRMATION_SECONDS
    t = (score - MOMENTUM_SL_SUPPRESS_THRESHOLD) / max(
        1.0 - MOMENTUM_SL_SUPPRESS_THRESHOLD, 1e-9
    )
    return min(
        SL_BASE_CONFIRMATION_SECONDS + t * (MOMENTUM_SL_MAX_WINDOW_SECONDS - SL_BASE_CONFIRMATION_SECONDS),
        MOMENTUM_SL_MAX_WINDOW_SECONDS,
    )


# ── Data loading ─────────────────────────────────────────────────────────────
@dataclass
class Trade:
    order_id: str
    direction: str
    entry_price: float
    entry_ts: float
    snap: dict


@dataclass
class Resolution:
    order_id: str
    final_price: float
    pnl: float
    status: str   # EARLY_EXIT_STOP_LOSS, WINDOW_RESOLVED, etc.


def load_trades_and_resolutions(paths: list[str]) -> tuple[dict, dict]:
    trades: dict[str, Trade] = {}
    resolutions: dict[str, Resolution] = {}

    for fp in paths:
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rtype = r.get("type")
                oid = r.get("order_id", "")

                if rtype == "trade" and oid:
                    snap = r.get("feature_snapshot", {})
                    trades[oid] = Trade(
                        order_id=oid,
                        direction=r.get("direction", "UP"),
                        entry_price=float(r.get("price", 0.0)),
                        entry_ts=float(r.get("ts", 0.0)),
                        snap=snap,
                    )

                elif rtype == "resolution" and oid:
                    resolutions[oid] = Resolution(
                        order_id=oid,
                        final_price=float(r.get("final_price", 0.0)),
                        pnl=float(r.get("pnl", 0.0)),
                        status=str(r.get("uma_resolution_status", "")),
                    )

    return trades, resolutions


# ── Main simulation ──────────────────────────────────────────────────────────
def run(paths: list[str]) -> None:
    trades, resolutions = load_trades_and_resolutions(paths)

    print(f"Loaded {len(trades)} trades, {len(resolutions)} resolutions from {len(paths)} files")

    sl_exits: list[tuple[Trade, Resolution]] = []
    for oid, res in resolutions.items():
        if "STOP_LOSS" in res.status and oid in trades:
            sl_exits.append((trades[oid], res))

    if not sl_exits:
        print("\nNo SL exits found in data. Run the bot in dry-run mode to accumulate SL exits.")
        print("The analyze_sl.py script uses .log files which have richer exit data.")
        _explain_feature_from_existing_trades(trades)
        return

    print(f"\nTotal SL exits: {len(sl_exits)}")

    # Classify and score each SL exit
    hard_crash = []
    borderline_suppressed = []
    borderline_unsuppressed = []

    for trade, res in sl_exits:
        ep = trade.entry_price
        if ep <= 0:
            continue
        sl_floor = ep * (1 - EXIT_STOP_LOSS_PCT)
        instant_floor = sl_floor * (1 - SL_INSTANT_BREACH_PCT)
        fp = res.final_price

        score = _suppression_score(trade.direction, trade.snap)
        window = _extended_window(trade.direction, trade.snap)
        would_suppress = window > SL_BASE_CONFIRMATION_SECONDS

        if fp < instant_floor:
            hard_crash.append((trade, res, score))
        elif abs(fp - sl_floor) / max(sl_floor, 1e-9) <= BORDERLINE_BAND:
            if would_suppress:
                borderline_suppressed.append((trade, res, score, window))
            else:
                borderline_unsuppressed.append((trade, res, score))
        else:
            hard_crash.append((trade, res, score))

    print("\nSL classification:")
    print(
        f"  Hard crash (deep SL, polling lag):  {len(hard_crash):4d}  "
        f"({100*len(hard_crash)/len(sl_exits):.1f}%)"
    )
    print(
        f"  Borderline — momentum suppressed:   {len(borderline_suppressed):4d}  "
        f"({100*len(borderline_suppressed)/len(sl_exits):.1f}%)"
    )
    print(
        f"  Borderline — momentum not met:      {len(borderline_unsuppressed):4d}  "
        f"({100*len(borderline_unsuppressed)/len(sl_exits):.1f}%)"
    )

    # Baseline PnL
    all_pnl = sum(res.pnl for _, res, *_ in sl_exits)
    hard_pnl = sum(res.pnl for _, res, _ in hard_crash)
    bl_sup_pnl = sum(res.pnl for _, res, _, __ in borderline_suppressed)
    bl_unsup_pnl = sum(res.pnl for _, res, _ in borderline_unsuppressed)

    print("\nBaseline PnL breakdown:")
    print(f"  Hard crash losses:               ${hard_pnl:+.2f}")
    print(f"  Borderline suppressed losses:    ${bl_sup_pnl:+.2f}")
    print(f"  Borderline unsuppressed losses:  ${bl_unsup_pnl:+.2f}")
    print(f"  Total SL PnL:                    ${all_pnl:+.2f}")

    if borderline_suppressed:
        # Simulate three scenarios for borderline-suppressed SLs
        avg_sl_loss = bl_sup_pnl / len(borderline_suppressed)
        # Optimistic: suppression worked, position recovered to break-even
        opt_delta = -bl_sup_pnl  # recover all the loss
        # Pessimistic: suppression delayed but price fell further (doubled loss)
        pes_delta = bl_sup_pnl   # same loss again
        # Neutral: 50/50
        neu_delta = 0.0

        print(f"\nSimulated impact of momentum suppression on {len(borderline_suppressed)} borderline SLs:")
        print(f"  Avg per-trade loss (baseline):    ${avg_sl_loss:.2f}")
        print(f"  Optimistic (all recover):         ${all_pnl + opt_delta:+.2f}  (delta ${opt_delta:+.2f})")
        print(f"  Neutral (50/50 recover):          ${all_pnl + neu_delta:+.2f}  (delta ${neu_delta:+.2f})")
        print(f"  Pessimistic (all keep falling):   ${all_pnl + pes_delta:+.2f}  (delta ${pes_delta:+.2f})")

        print("\nMomentum scores for suppressed borderline SLs:")
        scores = sorted([score for _, _, score, __ in borderline_suppressed])
        print(f"  Min: {min(scores):.3f}  Median: {scores[len(scores)//2]:.3f}  Max: {max(scores):.3f}")
        print(f"  Extension windows: {sorted(set(round(w,1) for _,_,_,w in borderline_suppressed))}")

    # Show score distribution for ALL SL exits
    all_scores = [_suppression_score(t.direction, t.snap) for t, _, *_ in sl_exits]
    buckets = {"<0.4": 0, "0.4–0.55": 0, "0.55–0.65": 0, "0.65–0.80": 0, "≥0.80": 0}
    for s in all_scores:
        if s < 0.4:
            buckets["<0.4"] += 1
        elif s < 0.55:
            buckets["0.4–0.55"] += 1
        elif s < 0.65:
            buckets["0.55–0.65"] += 1
        elif s < 0.80:
            buckets["0.65–0.80"] += 1
        else:
            buckets["≥0.80"] += 1
    print(f"\nMomentum score distribution at SL exits (all {len(sl_exits)}):")
    for k, v in buckets.items():
        bar = "█" * (v * 40 // max(len(sl_exits), 1))
        print(f"  {k:>12}: {v:4d}  {bar}")

    print(f"\nNote: Scores ≥ 0.65 ({buckets['0.65–0.80'] + buckets['≥0.80']} exits) are "
          "suppression candidates. Among those, only BORDERLINE SLs are affected.\n"
          "Hard crashes (price >> SL) are never suppressed regardless of momentum score.")


def _explain_feature_from_existing_trades(trades: dict) -> None:
    """Analyse the momentum context at entry for all trades as a preview."""
    if not trades:
        return
    print(f"\nPreviewing suppression scores at entry across all {len(trades)} trades:")
    scores = []
    for t in trades.values():
        scores.append(_suppression_score(t.direction, t.snap))
    scores.sort()
    high = sum(1 for s in scores if s >= MOMENTUM_SL_SUPPRESS_THRESHOLD)
    print(f"  Entries with suppression-eligible momentum (score ≥ {MOMENTUM_SL_SUPPRESS_THRESHOLD}): "
          f"{high}/{len(scores)} ({100*high/max(len(scores),1):.1f}%)")
    if scores:
        print(f"  Score range: {scores[0]:.3f} – {scores[-1]:.3f}, "
              f"median {scores[len(scores)//2]:.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    else:
        paths = sorted(glob.glob(".web_runtime/*_trades.jsonl")) + ["trades.jsonl"]
        paths = [p for p in paths if Path(p).exists()]

    if not paths:
        print("No trade files found. Pass paths as arguments or run from the codex_plan directory.")
        sys.exit(1)

    run(paths)
