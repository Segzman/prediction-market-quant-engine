"""Replay conviction configs against recorded candidates + resolutions.

No lookahead: every decision uses only fields stored at evaluation time.
Fills assumed at the quoted ask with the standard fee estimate, so PnL
is a optimistic proxy for ranking configs, not a profit promise.

Usage:
    python scripts/replay_conviction.py --glob '*.jsonl' --grid
    python scripts/replay_conviction.py --edge 0.07 --conv 0.75 --price-max 0.92
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
from dataclasses import dataclass

FEE_RATE = 0.072  # crypto taker estimate, mirrors estimate_trade_fee_usdc


@dataclass
class Score:
    trades: int = 0
    wins: int = 0
    gross: float = 0.0
    fees: float = 0.0

    @property
    def net(self) -> float:
        return self.gross - self.fees

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


def load_records(pattern: str) -> tuple[list[dict], dict[int, dict]]:
    candidates: list[dict] = []
    resolutions: dict[int, dict] = {}
    paths = sorted(set(glob.glob(pattern)) | set(glob.glob(".web_runtime/*.jsonl")))
    for path in paths:
        try:
            handle = open(path)
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "candidate":
                    candidates.append(record)
                elif record.get("type") == "resolution":
                    wts = record.get("window_end_ts")
                    if isinstance(wts, int) and wts not in resolutions:
                        resolutions[wts] = record
    return candidates, resolutions


def backfill_outcomes(
    candidates: list[dict],
    resolutions: dict[int, dict],
    *,
    cache_path: str = "replay_outcomes_cache.json",
    symbol: str = "BTCUSDT",
) -> int:
    """Fill missing window outcomes from Binance 1m closes. Returns # backfilled.

    resolved_up = close_at_window_end >= candidate open_price. Cache kept
    on disk so history is fetched once. Zero network when all cached.
    """
    import urllib.parse
    import urllib.request

    try:
        with open(cache_path) as handle:
            cache = json.load(handle)
    except (OSError, json.JSONDecodeError):
        cache = {}
    opens: dict[int, float] = {}
    for cand in candidates:
        wts = cand.get("window_end_ts")
        op = cand.get("open_price")
        if isinstance(wts, int) and isinstance(op, (int, float)) and op > 0:
            opens[wts] = float(op)
    missing = [
        wts for wts in opens
        if wts not in resolutions or resolutions[wts].get("resolved_up") is None
    ]
    backfilled = 0
    for wts in sorted(missing):
        key = str(wts)
        if key not in cache:
            query = urllib.parse.urlencode(
                {"symbol": symbol, "interval": "1m", "endTime": wts * 1000, "limit": 1}
            )
            request = urllib.request.Request(
                f"https://api.binance.com/api/v3/klines?{query}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    rows = json.load(response)
                cache[key] = float(rows[0][4]) if rows else None
            except Exception:
                cache[key] = None
        close = cache.get(key)
        if close is None:
            continue
        resolutions[wts] = {
            "type": "resolution",
            "window_end_ts": wts,
            "resolved_up": bool(close >= opens[wts]),
            "final_price": close,
            "price_to_beat": opens[wts],
            "backfilled": True,
        }
        backfilled += 1
    try:
        with open(cache_path, "w") as handle:
            json.dump(cache, handle)
    except OSError:
        pass
    return backfilled


def score_config(
    candidates: list[dict],
    resolutions: dict[int, dict],
    *,
    edge_min: float,
    conv_min: float,
    price_max: float,
    price_min: float = 0.0,
    stake: float = 5.0,
    regime: str = "LATE_CONVICTION",
) -> Score:
    result = Score()
    best_per_window: dict[int, dict] = {}
    for cand in candidates:
        if cand.get("regime_candidate") != regime:
            continue
        wts = cand.get("window_end_ts")
        if not isinstance(wts, int):
            continue
        edge = cand.get("edge")
        conv = cand.get("market_conviction")
        price = cand.get("market_price")
        direction = cand.get("direction")
        if edge is None or conv is None or price is None or direction is None:
            continue
        if not (price_min <= price <= price_max):
            continue
        if edge < edge_min or conv < conv_min:
            continue
        outcome = resolutions.get(wts)
        if outcome is None or outcome.get("resolved_up") is None:
            continue
        prior = best_per_window.get(wts)
        if prior is None or edge > prior["edge"]:
            best_per_window[wts] = {
                "edge": edge, "price": price, "direction": direction,
                "resolved_up": bool(outcome["resolved_up"]),
            }
    for take in best_per_window.values():
        price = take["price"]
        result.trades += 1
        won = ((take["direction"] == "UP") and take["resolved_up"]) or (
            (take["direction"] == "DOWN") and not take["resolved_up"]
        )
        if won:
            result.wins += 1
            result.gross += stake * (1 / price - 1)
        else:
            result.gross -= stake
        result.fees += stake * FEE_RATE * price * (1 - price)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default="*.jsonl")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--edge", type=float, default=0.07)
    parser.add_argument("--conv", type=float, default=0.75)
    parser.add_argument("--price-max", type=float, default=0.92)
    parser.add_argument("--price-min", type=float, default=0.0)
    parser.add_argument("--stake", type=float, default=5.0)
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--regime", default="LATE_CONVICTION")
    args = parser.parse_args()

    candidates, resolutions = load_records(args.glob)
    if args.no_backfill:
        print(f"candidates={len(candidates)} resolved_windows={len(resolutions)}")
    else:
        filled = backfill_outcomes(candidates, resolutions)
        labeled = sum(1 for r in resolutions.values() if r.get("resolved_up") is not None)
        print(f"candidates={len(candidates)} labeled_windows={labeled} (backfilled={filled})")

    if args.grid:
        print(f"{'edge':>6} {'conv':>6} {'pmax':>6} {'trades':>7} {'win%':>6} {'net':>9}")
        rows = []
        for edge, conv, pmax in itertools.product(
            [0.05, 0.06, 0.07, 0.08, 0.09],
            [0.70, 0.75, 0.80, 0.85],
            [0.88, 0.90, 0.92, 0.95],
        ):
            score = score_config(
                candidates, resolutions,
                edge_min=edge, conv_min=conv, price_max=pmax, stake=args.stake,
                regime=args.regime,
            )
            rows.append((score.net, edge, conv, pmax, score))
        rows.sort(reverse=True)
        for _net, edge, conv, pmax, score in rows[:12]:
            print(
                f"{edge:>6.2f} {conv:>6.2f} {pmax:>6.2f} {score.trades:>7d} "
                f"{100 * score.win_rate:>5.1f}% {score.net:>+9.2f}"
            )
    else:
        score = score_config(
            candidates, resolutions,
            edge_min=args.edge, conv_min=args.conv,
            price_max=args.price_max, price_min=args.price_min, stake=args.stake,
            regime=args.regime,
        )
        print(
            f"edge>={args.edge} conv>={args.conv} price<={args.price_max}: "
            f"trades={score.trades} win={100 * score.win_rate:.1f}% net={score.net:+.2f}"
        )


if __name__ == "__main__":
    main()
