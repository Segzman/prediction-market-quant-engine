"""Whole-day BTC price history for tuning when the bot was not trading.

Fetches Binance 1m klines (no key, cached on disk), slices them into
UTC 5-minute windows, and scores model-A momentum direction accuracy per
window offset. Needs no Polymarket data: momentum theo uses price only,
so any day/period becomes tunable — including days with zero bot coverage.

Usage:
    python scripts/day_history.py --days 7
    python scripts/day_history.py --start 2026-10-01 --days 3 --cache-dir data
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MOMENTUM_THRESHOLD
from strategy.probability import model_a_momentum

WINDOW_SECONDS = 300


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """All 1m klines in [start_ms, end_ms). Paginated, 1000 per request."""
    out: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor,
            "endTime": min(end_ms, cursor + 1000 * 60_000 - 1),
            "limit": 1000,
        })
        request = urllib.request.Request(
            f"https://api.binance.com/api/v3/klines?{query}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            rows = json.load(response)
        if not rows:
            break
        out.extend(rows)
        cursor = int(rows[-1][0]) + 60_000
        time.sleep(0.1)
    return out


def load_or_fetch(day: str, symbol: str, cache_dir: Path) -> list[list]:
    path = cache_dir / f"btc_1m_{symbol}_{day}.json"
    if path.exists():
        return json.loads(path.read_text())
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    rows = fetch_klines(symbol, int(start.timestamp() * 1000),
                        int((start + timedelta(days=1)).timestamp() * 1000))
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    print(f"fetched {day}: {len(rows)} klines", file=sys.stderr)
    return rows


def score_day(rows: list[list]) -> dict:
    """Bucket klines into 5-min windows; score momentum direction per minute."""
    windows: dict[int, list] = {}
    for row in rows:
        open_ts = int(row[0]) // 1000
        window_end = (open_ts // WINDOW_SECONDS + 1) * WINDOW_SECONDS
        windows.setdefault(window_end, []).append((open_ts, float(row[4])))
    # NOTE: model_a momentum theo sign always equals the tick move sign, so
    # the "accuracy" below is move persistence: does the tick-t move direction
    # still match the window outcome at close? That is exactly what a momentum
    # entry bets on, but read the header as persistence, not model skill.
    stats = {"windows": 0, "up_windows": 0, "ticks": 0, "hits": {},
             "abs_move_bps": []}
    for window_end in sorted(windows):
        klines = sorted(windows[window_end])
        if len(klines) < 4:
            continue
        open_px = klines[0][1]
        # open of window = open of its first 1m kline; fall back to first close
        first_open_ts = (window_end - WINDOW_SECONDS) * 1000
        open_px = next((float(r[1]) for r in rows if int(r[0]) == first_open_ts), klines[0][1])
        close_px = klines[-1][1]
        resolved_up = close_px >= open_px
        stats["windows"] += 1
        stats["up_windows"] += resolved_up
        stats["abs_move_bps"].append(abs(close_px - open_px) / open_px * 10_000)
        for open_ts, px in klines:
            remaining = window_end - open_ts
            if remaining <= 0 or remaining > WINDOW_SECONDS:
                continue
            theo = model_a_momentum(open_px, px, remaining)
            move = (px - open_px) / open_px
            for mult in (1.0, 2.0, 4.0):
                if abs(move) >= MOMENTUM_THRESHOLD * mult:
                    direction_up = (theo >= 0.5) == (move >= 0)
                    key = f"{mult}x"
                    bucket = stats["hits"].setdefault(key, {"n": 0, "w": 0})
                    bucket["n"] += 1
                    if direction_up == resolved_up:
                        bucket["w"] += 1
            stats["ticks"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--start", default=None,
                        help="YYYY-MM-DD (default: N days back from today, UTC)")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--cache-dir", default="data")
    args = parser.parse_args()

    end = datetime.fromisoformat(args.start).date() if args.start else datetime.now(timezone.utc).date()
    start_date = end - timedelta(days=args.days - 1) if not args.start else end
    if not args.start:
        start_date = end - timedelta(days=args.days - 1)
    cache_dir = Path(args.cache_dir)
    all_windows = 0
    print(f"{'day':<12} {'win':>5} {'up%':>5} {'|move|bp':>8} | " + "  ".join(f"{m:>14}" for m in ("1x persist", "2x persist", "4x persist")))
    day = start_date
    while day <= (end if not args.start else start_date + timedelta(days=args.days - 1)):
        rows = load_or_fetch(day.isoformat(), args.symbol, cache_dir)
        stats = score_day(rows)
        all_windows += stats["windows"]
        moves = sorted(stats["abs_move_bps"])
        med_move = moves[len(moves) // 2] if moves else 0.0
        up_pct = 100 * stats["up_windows"] / stats["windows"] if stats["windows"] else 0
        cells = []
        for mult in ("1.0x", "2.0x", "4.0x"):
            bucket = stats["hits"].get(mult, {"n": 0, "w": 0})
            cells.append(f"n={bucket['n']:<5} {100 * bucket['w'] / bucket['n'] if bucket['n'] else 0:>5.1f}%" if bucket["n"] else "thin")
        print(f"{day.isoformat():<12} {stats['windows']:>5} {up_pct:>4.0f}% {med_move:>8.1f} | " + "  ".join(f"{c:>14}" for c in cells))
        day += timedelta(days=1)
    print(f"{all_windows} windows scored (momentum threshold {MOMENTUM_THRESHOLD}; acc = move persistence)")


if __name__ == "__main__":
    main()
