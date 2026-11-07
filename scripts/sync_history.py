"""Fill missed BTC 1m history so the tuning cache is always up to date.

Fetches every day in [since, yesterday] missing from the cache dir.
Idempotent: cached days are skipped, so a daily scheduler keeps history
current with zero redundant downloads.

Usage:
    python scripts/sync_history.py [--days 60] [--symbol BTCUSDT] [--cache-dir data]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from day_history import load_or_fetch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--cache-dir", default="data")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    today = date.today()
    fetched, skipped = 0, 0
    for back in range(1, args.days + 1):
        day = (today - timedelta(days=back)).isoformat()
        if (cache_dir / f"btc_1m_{args.symbol}_{day}.json").exists():
            skipped += 1
            continue
        try:
            load_or_fetch(day, args.symbol, cache_dir)
            fetched += 1
        except Exception as exc:  # noqa: BLE001 - one bad day must not stop the sync
            print(f"skip {day}: {exc}", file=sys.stderr)
    print(f"synced: fetched={fetched} cached={skipped}")


if __name__ == "__main__":
    main()
