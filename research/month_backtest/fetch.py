#!/usr/bin/env python3
"""Fetch the local cache for the 30-day Polymarket BTC 5-minute backtest.

Three incremental steps, each skipped when its cache file already exists:

  (a) BTC spot: data.binance.vision daily BTCUSDT 1-second klines. If that
      archive is unreachable, falls back to the Binance REST 1-minute klines
      already used by scripts/day_history.py (coarser; recorded in the cache).
  (b) Markets: gamma-api events of the BTC up-or-down 5m series (series id from
      config.ASSET_SERIES_IDS), closed=true, one day per request, paginated.
      Window = [slug suffix, slug suffix + 300]; the slug suffix is the window
      start epoch. Outcome = settled outcomePrices (Up 1 / Down 0).
  (c) Contract prices: clob prices-history for the Up and Down tokens over each
      window (fidelity=1, the finest the endpoint offers).

Everything lives under research/month_backtest/cache/ (gitignored). Requests are
rate limited and retried with exponential backoff. Re-running fetches only what
is missing, so a daily rerun is cheap.

Usage:
    python research/month_backtest/fetch.py [--days 30] [--end-day YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import ASSET_SERIES_IDS, WINDOW_DURATION_SECONDS  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / "cache"
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
BINANCE_VISION = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s"
SERIES_ID = ASSET_SERIES_IDS["BTC"]
EVENTS_PAGE = 100
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
USER_AGENT = {"User-Agent": "Mozilla/5.0"}


class RateLimiter:
    """Spaces request starts at least 1/rate seconds apart across threads."""

    def __init__(self, rate_per_second: float) -> None:
        self._interval = 1.0 / rate_per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next_at)
            self._next_at = at + self._interval
        if at > now:
            time.sleep(at - now)


def get_json(url: str, limiter: RateLimiter, attempts: int = 5) -> object:
    for attempt in range(attempts):
        limiter.wait()
        try:
            request = urllib.request.Request(url, headers=USER_AGENT)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
        time.sleep(min(60.0, 2.0**attempt))
    raise RuntimeError("unreachable")


def get_bytes(url: str, limiter: RateLimiter, attempts: int = 3) -> bytes:
    for attempt in range(attempts):
        limiter.wait()
        try:
            request = urllib.request.Request(url, headers=USER_AGENT)
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
        time.sleep(min(30.0, 2.0**attempt))
    raise RuntimeError("unreachable")


def day_range(end_day: date, days: int) -> list[str]:
    """`days` complete UTC days ending on end_day (inclusive), oldest first."""
    return [(end_day - timedelta(days=back)).isoformat() for back in range(days - 1, -1, -1)]


def last_complete_day(today: date | None = None) -> date:
    today = today or datetime.now(UTC).date()
    return today - timedelta(days=1)


def parse_binance_1s(text: str) -> list[tuple[int, float]]:
    """(epoch_second, close) rows from a Binance kline CSV.

    Recent archives use microsecond open times; older ones milliseconds.
    """
    rows: list[tuple[int, float]] = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 5 or not parts[0].strip().isdigit():
            continue
        open_time = int(parts[0])
        if open_time > 10**14:
            seconds = open_time // 1_000_000
        elif open_time > 10**11:
            seconds = open_time // 1000
        else:
            seconds = open_time
        rows.append((seconds, float(parts[4])))
    return rows


def window_start_from_slug(slug: str) -> int | None:
    """Window start epoch from a btc-updown-5m-<start> slug; None if malformed."""
    suffix = slug.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        return None
    start = int(suffix)
    return start if start % WINDOW_DURATION_SECONDS == 0 else None


def _json_list(value: object) -> list:
    if isinstance(value, str):
        return json.loads(value)
    return list(value or [])


def market_record(event: dict, market: dict) -> dict | None:
    """Flatten one gamma market. None when it is not a two-sided window market."""
    slug = market.get("slug") or event.get("slug") or ""
    start_ts = window_start_from_slug(slug)
    if start_ts is None:
        return None
    outcomes = [str(o).lower() for o in _json_list(market.get("outcomes"))]
    tokens = _json_list(market.get("clobTokenIds"))
    if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != {"up", "down"}:
        return None
    token_by_side = {outcome: str(token) for outcome, token in zip(outcomes, tokens, strict=True)}
    prices = [float(p) for p in _json_list(market.get("outcomePrices"))]
    resolved_up: bool | None = None
    if len(prices) == 2:
        up_price, down_price = (
            prices[outcomes.index("up")],
            prices[outcomes.index("down")],
        )
        if up_price >= 0.999 and down_price <= 0.001:
            resolved_up = True
        elif down_price >= 0.999 and up_price <= 0.001:
            resolved_up = False
    metadata = event.get("eventMetadata") or {}
    return {
        "slug": slug,
        "start_ts": start_ts,
        "end_ts": start_ts + WINDOW_DURATION_SECONDS,
        "up_token": token_by_side["up"],
        "down_token": token_by_side["down"],
        "resolved_up": resolved_up,
        "uma_status": market.get("umaResolutionStatus"),
        "price_to_beat": metadata.get("priceToBeat"),
        "final_price": metadata.get("finalPrice"),
    }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_btc_day(day: str, limiter: RateLimiter, cache_dir: Path) -> str:
    """Write cache/btc/<day>.csv with 'epoch_second,price' rows. Returns source tag."""
    path = cache_dir / "btc" / f"{day}.csv"
    if path.exists():
        source = path.read_text(encoding="utf-8").splitlines()[0].split("=", 1)[-1]
        if source != "binance-rest-1m":  # the 1m fallback is retried: the 1s archive appears later
            return source
    path.parent.mkdir(parents=True, exist_ok=True)
    source = "binance-vision-1s"
    try:
        blob = get_bytes(f"{BINANCE_VISION}/BTCUSDT-1s-{day}.zip", limiter)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        rows = parse_binance_1s(text)
    except (urllib.error.URLError, TimeoutError, ConnectionError, zipfile.BadZipFile):
        source = "binance-rest-1m"
        rows = _binance_rest_1m(day, cache_dir)
    lines = [f"#source={source}"] + [f"{sec},{price}" for sec, price in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return source


def _binance_rest_1m(day: str, cache_dir: Path) -> list[tuple[int, float]]:
    from scripts.day_history import load_or_fetch  # reuse the cached REST fallback

    klines = load_or_fetch(day, "BTCUSDT", cache_dir / "klines_1m")
    return [(int(row[0]) // 1000, float(row[4])) for row in klines]


def fetch_markets_day(day: str, limiter: RateLimiter, cache_dir: Path) -> list[dict]:
    path = cache_dir / "markets" / f"{day}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    events: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "series_id": SERIES_ID,
                "closed": "true",
                "end_date_min": _iso(start.timestamp() + 1),
                "end_date_max": _iso((start + timedelta(days=1)).timestamp()),
                "order": "endDate",
                "ascending": "true",
                "limit": EVENTS_PAGE,
                "offset": offset,
            }
        )
        page = get_json(f"{GAMMA_HOST}/events?{query}", limiter)
        if not isinstance(page, list):
            raise RuntimeError(f"unexpected events payload for {day}")
        events.extend(page)
        if len(page) < EVENTS_PAGE:
            break
        offset += EVENTS_PAGE
    markets: dict[str, dict] = {}
    for event in events:
        for market in event.get("markets") or []:
            record = market_record(event, market)
            if record is not None:
                markets.setdefault(record["slug"], record)
    ordered = sorted(markets.values(), key=lambda m: m["start_ts"])
    if all(m["resolved_up"] is not None for m in ordered):  # unsettled windows are retried later
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ordered, separators=(",", ":")), encoding="utf-8")
    return ordered


def fetch_history(market: dict, limiter: RateLimiter, cache_dir: Path) -> bool:
    """Cache Up/Down price history for one window. True if cached or fetched."""
    path = cache_dir / "prices" / f"{market['slug']}.json"
    if path.exists():
        return True
    body: dict[str, list] = {}
    for side in ("up", "down"):
        query = urllib.parse.urlencode(
            {
                "market": market[f"{side}_token"],
                "startTs": market["start_ts"] - 60,
                "endTs": market["end_ts"] + 60,
                "fidelity": 1,
            }
        )
        payload = get_json(f"{CLOB_HOST}/prices-history?{query}", limiter)
        history = payload.get("history", []) if isinstance(payload, dict) else []
        body[side] = [[int(h["t"]), float(h["p"])] for h in history]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end-day", default=None, help="last complete UTC day (default: yesterday)")
    parser.add_argument("--rps", type=float, default=8.0, help="max requests per second")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    end = date.fromisoformat(args.end_day) if args.end_day else last_complete_day()
    days = day_range(end, args.days)
    limiter = RateLimiter(args.rps)
    print(f"period {days[0]}..{days[-1]} ({len(days)} days), cache={cache_dir}")

    sources: dict[str, int] = {}
    for day in days:
        source = fetch_btc_day(day, limiter, cache_dir)
        sources[source] = sources.get(source, 0) + 1
    print(f"btc: {sources}")

    markets: list[dict] = []
    failed_days: list[str] = []
    for day in days:
        try:
            markets.extend(fetch_markets_day(day, limiter, cache_dir))
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failed_days.append(f"{day}:{type(exc).__name__}")
    unique = {m["slug"]: m for m in markets}
    print(f"markets: {len(unique)} windows, failed days: {failed_days or 'none'}")

    errors = 0

    def _one(market: dict) -> bool:
        try:
            return fetch_history(market, limiter, cache_dir)
        except Exception:  # noqa: BLE001 - reruns retry missing files
            return False

    todo = [m for m in unique.values() if m["resolved_up"] is not None]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for ok in pool.map(_one, todo):
            errors += 0 if ok else 1
    print(f"price histories: {len(todo) - errors}/{len(todo)} cached, errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
