"""
analyze_entry_band.py

Parses all .web_runtime/*.log files, matches entry lines to exit lines by order_id,
classifies trades into entry bands, and simulates three portfolios to evaluate a
proposed new entry strategy.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glob
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches lines like:
#   Placed UP | edge=0.100 | theo=0.720 | market=0.620 | bet=$5.00 | order=dry-run-...
#   Placed DOWN (HEDGE) | edge=... — we capture both bare and tagged directions
ENTRY_RE = re.compile(
    r"Placed\s+(UP|DOWN)(?:\s+\([^)]+\))?\s*\|"
    r"\s*edge=([+-]?\d+\.?\d*)\s*\|"
    r"\s*theo=(\d+\.?\d*)\s*\|"
    r"\s*market=(\d+\.?\d*)\s*\|"
    r"\s*bet=\$([0-9.]+)\s*\|"
    r"\s*order=(dry-run-[0-9a-f\-]+)"
)

# Matches lines like:
#   Exit triggered for dry-run-... | Reason: STOP_LOSS | Entry: 0.620 | Exit: 0.450 | PnL: -27.42%
#   Exit triggered for Trade #1 | dry-run-... | Reason: ...
EXIT_RE = re.compile(
    r"Exit triggered for (?:Trade #\d+\s*\|\s*)?"
    r"(dry-run-[0-9a-f\-]+)\s*\|"
    r"\s*Reason:\s*(\w+)\s*\|"
    r"\s*Entry:\s*(\d+\.?\d*)\s*\|"
    r"\s*Exit:\s*(\d+\.?\d*)\s*\|"
    r"\s*PnL:\s*([+-]?\d+\.?\d*)%"
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    order_id: str
    direction: str
    edge: float
    theo: float
    market: float  # entry market price
    bet: float
    reason: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    band: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_logs(log_glob: str):
    entries: dict[str, Trade] = {}  # order_id -> Trade (first seen wins)
    exits: dict[str, dict] = {}  # order_id -> exit info (first seen wins)

    log_files = sorted(glob.glob(log_glob))
    if not log_files:
        raise FileNotFoundError(f"No log files matched: {log_glob}")

    total_files = len(log_files)
    entry_lines_seen = 0
    exit_lines_seen = 0

    for path in log_files:
        try:
            with open(path, errors="replace") as fh:
                for line in fh:
                    # Try entry match
                    m = ENTRY_RE.search(line)
                    if m:
                        entry_lines_seen += 1
                        direction, edge, theo, market, bet, order_id = m.groups()
                        if order_id not in entries:
                            entries[order_id] = Trade(
                                order_id=order_id,
                                direction=direction,
                                edge=float(edge),
                                theo=float(theo),
                                market=float(market),
                                bet=float(bet),
                            )
                        continue

                    # Try exit match
                    m = EXIT_RE.search(line)
                    if m:
                        exit_lines_seen += 1
                        order_id, reason, entry_p, exit_p, pnl = m.groups()
                        if order_id not in exits:
                            exits[order_id] = {
                                "reason": reason,
                                "entry_price": float(entry_p),
                                "exit_price": float(exit_p),
                                "pnl_pct": float(pnl),
                            }
        except Exception as exc:
            print(f"  WARNING: could not read {path}: {exc}")

    print(f"Parsed {total_files} log files.")
    print(f"  Entry lines matched : {entry_lines_seen}")
    print(f"  Exit lines matched  : {exit_lines_seen}")
    print(f"  Unique entry IDs    : {len(entries)}")
    print(f"  Unique exit IDs     : {len(exits)}")

    # Match entries to exits — only keep trades with both
    matched: list[Trade] = []
    for oid, trade in entries.items():
        if oid in exits:
            ex = exits[oid]
            trade.reason = ex["reason"]
            trade.entry_price = ex["entry_price"]
            trade.exit_price = ex["exit_price"]
            trade.pnl_pct = ex["pnl_pct"]
            matched.append(trade)

    print(f"  Matched (entry+exit): {len(matched)}")
    return matched


# ---------------------------------------------------------------------------
# Band classification
# ---------------------------------------------------------------------------


def classify_band(trade: Trade) -> str:
    m = trade.market
    t = trade.theo

    # Overlap zone 0.60–0.62: LATE_CONVICTION if theo > 0.65, else NEW_BAND
    if 0.60 <= m < 0.62:
        return "LATE_CONVICTION" if t > 0.65 else "NEW_BAND"

    if 0.60 <= m <= 0.68:
        return "LATE_CONVICTION"

    if 0.55 <= m < 0.60:
        return "NEW_BAND"

    if 0.10 <= m < 0.55:
        return "OLD_BAND"

    return "OUTSIDE"


# ---------------------------------------------------------------------------
# Portfolio simulation
# ---------------------------------------------------------------------------


@dataclass
class PortfolioStats:
    name: str
    trades: list = field(default_factory=list)

    def run(self, all_trades: list[Trade]):
        self.trades = all_trades

    def compute(self, cap_sl: float | None = None):
        """
        cap_sl: if set, clamp any STOP_LOSS exit PnL to this floor (e.g. -5.0 for -5%).
        Returns a dict of stats.
        """
        t = self.trades
        if not t:
            return self._empty()

        total = len(t)
        bankroll = 50.0

        pnls = []
        sl_pnls = []
        tp_pnls = []
        time_pnls = []

        wins = 0
        sl_count = 0
        tp_count = 0
        time_count = 0
        total_dollar_pnl = 0.0

        for trade in t:
            pnl = trade.pnl_pct
            reason = trade.reason.upper()

            if cap_sl and reason == "STOP_LOSS":
                pnl = max(pnl, cap_sl)

            pnls.append(pnl)
            total_dollar_pnl += pnl / 100.0 * 5.0  # always $5 per trade nominal

            bankroll *= 1.0 + pnl / 100.0

            if pnl > 0:
                wins += 1

            if reason == "STOP_LOSS":
                sl_count += 1
                sl_pnls.append(pnl)
            elif reason == "TAKE_PROFIT":
                tp_count += 1
                tp_pnls.append(pnl)
            else:
                time_count += 1
                time_pnls.append(pnl)

        avg_pnl = sum(pnls) / total
        avg_sl = sum(sl_pnls) / len(sl_pnls) if sl_pnls else float("nan")
        avg_tp = sum(tp_pnls) / len(tp_pnls) if tp_pnls else float("nan")

        return {
            "total": total,
            "win_rate": wins / total * 100,
            "avg_pnl": avg_pnl,
            "total_dollar_pnl": total_dollar_pnl,
            "avg_sl_pnl": avg_sl,
            "avg_tp_pnl": avg_tp,
            "sl_count": sl_count,
            "tp_count": tp_count,
            "time_count": time_count,
            "final_bankroll": bankroll,
        }

    def _empty(self):
        return {
            "total": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "total_dollar_pnl": 0,
            "avg_sl_pnl": float("nan"),
            "avg_tp_pnl": float("nan"),
            "sl_count": 0,
            "tp_count": 0,
            "time_count": 0,
            "final_bankroll": 50.0,
        }


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def fmt(v, fmt_str):
    try:
        return format(v, fmt_str)
    except (TypeError, ValueError):
        return "  n/a"


def print_comparison(stats_list: list[tuple[str, dict]]):
    headers = [
        "Portfolio",
        "Trades",
        "Win%",
        "Avg PnL%",
        "$ PnL",
        "Avg SL%",
        "Avg TP%",
        "SL#",
        "TP#",
        "TIME#",
        "Final $",
    ]
    col_w = [24, 7, 7, 9, 9, 8, 8, 5, 5, 6, 9]

    sep = "+" + "+".join("-" * w for w in col_w) + "+"

    def row(*cells):
        parts = []
        for val, w in zip(cells, col_w, strict=False):
            s = str(val)
            parts.append(f" {s:<{w - 1}}")
        return "|" + "|".join(parts) + "|"

    print()
    print("=" * (sum(col_w) + len(col_w) + 1))
    print("  PORTFOLIO COMPARISON")
    print("=" * (sum(col_w) + len(col_w) + 1))
    print(sep)
    print(row(*headers))
    print(sep)

    for name, s in stats_list:
        print(
            row(
                name,
                s["total"],
                f"{s['win_rate']:.1f}%",
                f"{s['avg_pnl']:+.2f}%",
                f"${s['total_dollar_pnl']:+.2f}",
                fmt(s["avg_sl_pnl"], "+.2f") + "%",
                fmt(s["avg_tp_pnl"], "+.2f") + "%",
                s["sl_count"],
                s["tp_count"],
                s["time_count"],
                f"${s['final_bankroll']:.2f}",
            )
        )
    print(sep)
    print()


def print_band_breakdown(band_trades: dict[str, list[Trade]]):
    print("=" * 70)
    print("  BAND BREAKDOWN  (all matched trades, no portfolio filter)")
    print("=" * 70)
    fmt_hdr = f"  {'Band':<18} {'Count':>6}  {'Win%':>6}  {'Avg PnL%':>9}  {'SL':>4}  {'TP':>4}  {'TIME':>5}"
    print(fmt_hdr)
    print("  " + "-" * 66)

    for band in ["OLD_BAND", "NEW_BAND", "LATE_CONVICTION", "OUTSIDE"]:
        trades = band_trades.get(band, [])
        if not trades:
            print(f"  {band:<18} {'0':>6}  {'—':>6}  {'—':>9}  {'—':>4}  {'—':>4}  {'—':>5}")
            continue
        wins = sum(1 for t in trades if t.pnl_pct > 0)
        sl = sum(1 for t in trades if t.reason.upper() == "STOP_LOSS")
        tp = sum(1 for t in trades if t.reason.upper() == "TAKE_PROFIT")
        te = sum(1 for t in trades if t.reason.upper() not in ("STOP_LOSS", "TAKE_PROFIT"))
        avg_pnl = sum(t.pnl_pct for t in trades) / len(trades)
        wr = wins / len(trades) * 100
        print(f"  {band:<18} {len(trades):>6}  {wr:>5.1f}%  {avg_pnl:>+8.2f}%  {sl:>4}  {tp:>4}  {te:>5}")

    print()
    print("  OLD_BAND trades being DROPPED by new strategy:")
    old = band_trades.get("OLD_BAND", [])
    if old:
        wins = sum(1 for t in old if t.pnl_pct > 0)
        avg = sum(t.pnl_pct for t in old) / len(old)
        print(f"    Count: {len(old)}  |  Win rate: {wins / len(old) * 100:.1f}%  |  Avg PnL: {avg:+.2f}%")
        worst = sorted(old, key=lambda t: t.pnl_pct)[:5]
        print("    5 worst OLD_BAND trades:")
        for t in worst:
            print(f"      market={t.market:.3f}  theo={t.theo:.3f}  pnl={t.pnl_pct:+.2f}%  reason={t.reason}")
    else:
        print("    (none)")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    log_glob = ".web_runtime/*.log"
    print(f"\nParsing logs: {log_glob}")
    print("-" * 50)

    matched = parse_logs(log_glob)

    # Classify
    for trade in matched:
        trade.band = classify_band(trade)

    band_map: dict[str, list[Trade]] = {
        "OLD_BAND": [],
        "NEW_BAND": [],
        "LATE_CONVICTION": [],
        "OUTSIDE": [],
    }
    for trade in matched:
        band_map[trade.band].append(trade)

    print()
    print("Band counts after classification:")
    for band, trades in band_map.items():
        print(f"  {band:<18}: {len(trades)}")
    print()

    # Build portfolios
    port_A_trades = band_map["OLD_BAND"] + band_map["NEW_BAND"] + band_map["LATE_CONVICTION"]
    port_B_trades = band_map["NEW_BAND"] + band_map["LATE_CONVICTION"]
    port_C_trades = port_B_trades  # same trades, different SL cap

    pa = PortfolioStats("A — Old strategy")
    pa.run(port_A_trades)

    pb = PortfolioStats("B — New strategy")
    pb.run(port_B_trades)

    pc = PortfolioStats("C — New + ideal SL")
    pc.run(port_C_trades)

    stats_A = pa.compute()
    stats_B = pb.compute()
    stats_C = pc.compute(cap_sl=-5.0)

    print_comparison(
        [
            ("A — Old strategy", stats_A),
            ("B — New strategy", stats_B),
            ("C — New + ideal SL", stats_C),
        ]
    )

    print_band_breakdown(band_map)

    # Edge improvement summary
    if stats_A["total"] > 0 and stats_B["total"] > 0:
        print("=" * 70)
        print("  DECISION VALIDATION SUMMARY")
        print("=" * 70)
        delta_wr = stats_B["win_rate"] - stats_A["win_rate"]
        delta_pnl = stats_B["avg_pnl"] - stats_A["avg_pnl"]
        delta_br = stats_B["final_bankroll"] - stats_A["final_bankroll"]
        delta_br_c = stats_C["final_bankroll"] - stats_A["final_bankroll"]

        print(f"  B vs A  — Win rate change   : {delta_wr:+.1f}pp")
        print(f"  B vs A  — Avg PnL change    : {delta_pnl:+.2f}pp")
        print(f"  B vs A  — Bankroll delta    : ${delta_br:+.2f}")
        print(f"  C vs A  — Bankroll delta    : ${delta_br_c:+.2f}  (with ideal SL cap at -5%)")
        print()
        verdict = "POSITIVE" if delta_pnl >= 0 and delta_br >= 0 else "NEGATIVE"
        print(f"  Overall signal from dropping OLD_BAND: {verdict}")
        print()


if __name__ == "__main__":
    main()
