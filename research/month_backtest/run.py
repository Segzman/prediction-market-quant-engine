#!/usr/bin/env python3
"""Score the engine's probability models on a month of real Polymarket BTC 5m outcomes.

Reads the cache written by research/month_backtest/fetch.py (no network unless
JEV is enabled) and, for every resolved window, rebuilds the model inputs at the
decision times the bot uses (seconds remaining 90, 60, 30, 10). Each state is
scored by the real functions in strategy/probability.py:

  A, B, P      computed from the current state only.
  C            calibrated from resolutions STRICTLY before the decision time
               (the look-ahead fix): a per-run ledger file holds one resolution
               record per prior decision state; get_probability is given
               decision_ts so records with window_end_ts >= decision_ts are
               ignored.
  MKT          normalized Up/Down contract mid at the decision time.
  JEV          optional, directional states only (see --no-jev / JEV_MAX_CALLS).

Outputs docs/month_backtest.md and research/month_backtest/data/summary.json
(small derived summary, committed). Raw caches are never written here.

Environment:
  TYPESAFE_API_KEY  JEV key. Else macOS keychain item service "typesafe-api",
                    account "sekun". Kept in memory only, never printed or stored.
  JEV_MAX_CALLS     cap on JEV network calls per run. Default 0 = unlimited.

Usage:
    python research/month_backtest/run.py [--no-jev] [--end-day YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    EDGE_THRESHOLD,
    ENTRY_MAX_CONTRACT_PRICE,
    ENTRY_MIN_CONTRACT_PRICE,
    EXPECTED_SLIPPAGE_BPS,
    MIN_EXPECTED_PROFIT_USDC,
    POLYMARKET_FEATURE_LOOKBACK_SECONDS,
    VOLATILITY_MIN_SAMPLES,
)
from research import backtest_report as br  # noqa: E402
from research.month_backtest import fetch  # noqa: E402
from strategy import jev  # noqa: E402
from strategy.probability import (  # noqa: E402
    ModelContext,
    bucket_acceleration,
    bucket_directional_consistency,
    bucket_move_pct,
    bucket_probability,
    bucket_seconds_remaining,
    build_model_c_bucket_key,
    get_probability,
)

DOCS_PATH = ROOT / "docs" / "month_backtest.md"
SUMMARY_PATH = Path(__file__).resolve().parent / "data" / "summary.json"
CACHE_DIR = fetch.CACHE_DIR
DECISION_SECONDS = (90, 60, 30, 10)
EXPECTED_WINDOWS = 30 * 288
MODELS = ("A", "B", "C", "P")
PROB_NAMES = MODELS + ("MKT",)
STAKE_USDC = 5.0  # fixed stake; the bot sizes adaptively, the sim does not
CRYPTO_FEE_RATE = 0.072  # scripts/report_performance.py CRYPTO_TAKER_FEE_RATE
MARKET_MAX_AGE_SECONDS = 120.0  # last contract quote older than this = no quote
VOLATILITY_LOOKBACK_SECONDS = 30
TICK_WINDOW_SECONDS = 30
BOOTSTRAP_ITERS = 1000
BOOTSTRAP_SEED = 11
SUMMARY_MAX_BYTES = 500_000


# ----------------------------------------------------------------- pure helpers


def last_known(points: list[tuple[int, float]], stamps: list[int], t: float, max_age: float) -> float | None:
    """Latest quote at or before t, if no older than max_age seconds."""
    i = bisect_right(stamps, t) - 1
    if i < 0 or t - stamps[i] > max_age:
        return None
    return points[i][1]


def rolling_volatility(prices: list[float]) -> float:
    """Std of 1-second log returns, mirroring state.session.BotState.rolling_volatility."""
    returns = [
        math.log(b / a) for a, b in zip(prices, prices[1:], strict=False) if a > 0 and b > 0
    ]
    if len(returns) < VOLATILITY_MIN_SAMPLES - 1:
        return 0.0
    return statistics.pstdev(returns)


def side_from_market(mkt_up: float) -> str:
    if mkt_up > 0.5:
        return "UP"
    if mkt_up < 0.5:
        return "DOWN"
    return "NONE"


def build_state_features(
    *,
    open_price: float,
    price_path: list[tuple[int, float]],
    poly_path: list[tuple[int, float, float]],
    price_now: float,
    seconds_remaining: int,
    up_price: float,
    down_price: float,
) -> dict:
    """Model inputs for one decision state. Pure: no I/O, no clock.

    price_path: (second, price) for recent seconds up to and including now.
    poly_path: (second, up_mid, down_mid) for recent contract quotes.
    """
    move_pct = (price_now - open_price) / open_price
    tick_move, consistency, accel = br.tick_features(price_path) if len(price_path) >= 3 else (0.0, 0.5, 0.0)
    if len(poly_path) >= 2:
        up_mom, down_mom = br.poly_momentum(poly_path)
    else:
        up_mom, down_mom = 0.0, 0.0
    sigma = rolling_volatility([p for _, p in price_path])
    mkt_up = up_price / (up_price + down_price) if up_price + down_price > 0 else 0.5
    side = side_from_market(mkt_up)
    side_sign = {"UP": 1, "DOWN": -1}.get(side, 0)
    move_sign = (move_pct > 0) - (move_pct < 0)
    agreement = 0.0 if side_sign == 0 or move_sign == 0 else (1.0 if side_sign == move_sign else -1.0)
    context = ModelContext(
        tick_move_pct=tick_move,
        directional_consistency=consistency,
        acceleration_pct=accel,
        up_price_momentum=up_mom,
        down_price_momentum=down_mom,
        strongest_side=side,
        # Proxy: the bot's conviction is 0.72 x strongest-side price plus book terms
        # that this study does not have (no depth or queue data).
        market_conviction=min(1.0, 0.72 * max(mkt_up, 1 - mkt_up)),
        btc_poly_agreement=agreement,
        market_implied_up_probability=mkt_up,
    )
    return {
        "s": seconds_remaining,
        "S0": open_price,
        "S": price_now,
        "move": move_pct,
        "sigma": sigma,
        "tick_move": tick_move,
        "consistency": consistency,
        "accel": accel,
        "up_mom": up_mom,
        "down_mom": down_mom,
        "up_px": up_price,
        "down_px": down_price,
        "mkt_up": mkt_up,
        "side": side,
        "context": context,
    }


def ledger_record(state: dict, window_end_ts: float, resolved_up: bool, base_prob: float) -> dict:
    """One Model C resolution record, built the way the bot's ledger keys it."""
    move_bin = bucket_move_pct(state["move"])
    time_bin = bucket_seconds_remaining(state["s"])
    return {
        "type": "resolution",
        "window_end_ts": window_end_ts,
        "resolved_up": resolved_up,
        "model_c_bucket": build_model_c_bucket_key(
            move_bin=move_bin,
            time_bin=time_bin,
            base_prob_bin=bucket_probability(base_prob),
            directional_consistency_bin=bucket_directional_consistency(state["consistency"]),
            acceleration_bin=bucket_acceleration(state["accel"]),
        ),
        "move_pct_bin": move_bin,
        "time_bin": time_bin,
    }


def fee_usdc(price: float, notional: float) -> float:
    """Polymarket crypto taker fee, same formula as scripts/report_performance.py."""
    if price <= 0 or price >= 1 or notional <= 0:
        return 0.0
    shares = notional / price
    return shares * price * CRYPTO_FEE_RATE * (price * (1 - price))


def pick_entry(states: list[dict], model: str, *, stake: float = STAKE_USDC) -> dict | None:
    """First decision state (earliest in the window) where the bot's mispricing rules pass.

    Rules mirror config: quoted side price in [ENTRY_MIN, ENTRY_MAX], edge >= EDGE_THRESHOLD,
    expected profit net of fee and slippage >= MIN_EXPECTED_PROFIT_USDC. The side is the
    one with the larger edge. Fill = quote x (1 + EXPECTED_SLIPPAGE_BPS).
    """
    for state in sorted(states, key=lambda st: -st["s"]):
        p_up = state["p"].get(model)
        if p_up is None:
            continue
        best: dict | None = None
        for side, p_side, quote in (
            ("UP", p_up, state["up_px"]),
            ("DOWN", 1 - p_up, state["down_px"]),
        ):
            edge = p_side - quote
            if not (ENTRY_MIN_CONTRACT_PRICE <= quote <= ENTRY_MAX_CONTRACT_PRICE):
                continue
            if edge < EDGE_THRESHOLD:
                continue
            if best is None or edge > best["edge"]:
                best = {"side": side, "p_side": p_side, "quote": quote, "edge": edge}
        if best is None:
            continue
        fill = best["quote"] * (1 + EXPECTED_SLIPPAGE_BPS / 10_000)
        fee = fee_usdc(fill, stake)
        ev_net = stake * best["p_side"] / fill - stake - fee
        if ev_net < MIN_EXPECTED_PROFIT_USDC:
            continue
        won = (best["side"] == "UP") == bool(state["resolved_up"])
        return {
            "window": state["window"],
            "s": state["s"],
            "side": best["side"],
            "p_side": best["p_side"],
            "quote": best["quote"],
            "fill": fill,
            "edge": best["edge"],
            "fee": fee,
            "ev_net": ev_net,
            "won": won,
            "pnl": br.trade_pnl(stake, fill, fee, won),
            "week": state["week"],
        }
    return None


def simulate(windows: list[list[dict]], model: str) -> list[dict]:
    """Trades for one model, chronological, at most one per window."""
    trades = []
    for states in windows:
        trade = pick_entry(states, model)
        if trade is not None:
            trades.append(trade)
    return trades


def cluster_mean_ci(values: list[float], clusters: list[int], iters: int = BOOTSTRAP_ITERS) -> tuple[float | None, float | None]:
    """95% percentile CI of the mean, resampling whole clusters (windows).

    Same estimator as backtest_report.cluster_bootstrap_mean_ci, aggregated per
    cluster first so ~35k states resample quickly.
    """
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for v, c in zip(values, clusters, strict=False):
        sums[c] = sums.get(c, 0.0) + v
        counts[c] = counts.get(c, 0) + 1
    keys = list(sums)
    if not keys:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    for _ in range(iters):
        total = count = 0
        for _ in keys:
            k = keys[rng.randrange(len(keys))]
            total += sums[k]
            count += counts[k]
        means.append(total / count)
    means.sort()
    return means[int(0.025 * iters)], means[min(iters - 1, int(0.975 * iters))]


def score_block(probs: list[float], outcomes: list[int]) -> dict:
    n = len(probs)
    if n == 0:
        return {"n": 0}
    hits, hit_n = br.directional_hits(probs, outcomes)
    lo, hi = br.wilson_interval(hits, hit_n)
    rel_rows, ece = br.reliability(probs, outcomes, bins=10)
    return {
        "n": n,
        "brier": br.brier(probs, outcomes),
        "log_loss": br.log_loss(probs, outcomes),
        "hits": hits,
        "hit_n": hit_n,
        "hit_rate": hits / hit_n if hit_n else None,
        "hit_ci": [lo, hi],
        "ece": ece,
        "reliability": rel_rows,
    }


def run_jev(states: list[dict], cache_dir: Path, max_calls: int) -> dict:
    """Score the production JEV agreement question on directional states. Never prints the key."""
    stats: dict = {"status": "skipped", "eligible": 0}
    eligible = [st for st in states if st["side"] in ("UP", "DOWN")]
    stats["eligible"] = len(eligible)
    key = _resolve_key()
    if not key:
        stats["status"] = "no_key"
        return stats
    os.environ["TYPESAFE_API_KEY"] = key
    del key

    cache_path = cache_dir / "jev" / "jev_cache.jsonl"
    cache: dict[str, float] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[rec["h"]] = rec["p"]
    for st in eligible:
        if st["jev_hash"] in cache:
            st["p"]["JEV"] = _p_up(cache[st["jev_hash"]], st["side"])
    pending = [st for st in eligible if st["jev_hash"] not in cache]
    stats["cache_hits"] = len(eligible) - len(pending)
    if max_calls > 0 and len(pending) > max_calls:
        step = math.ceil(len(pending) / max_calls)
        pending = pending[::step][:max_calls]
    stats["sampled"] = len(pending) < len(eligible) - stats["cache_hits"]

    def call(st: dict) -> tuple[dict, float | None, float | None, int, str | None]:
        attempts = api_calls = 0
        prob: float | None = None
        latency: float | None = None
        error: str | None = None
        for attempt in range(3):
            meta: dict = {}
            prob = jev.query_btc_agreement(
                strongest_side=st["side"],
                seconds_remaining=st["s"],
                tick_move_pct=st["tick_move"],
                directional_consistency=st["consistency"],
                acceleration_pct=st["accel"],
                up_price_momentum=st["up_mom"],
                down_price_momentum=st["down_mom"],
                meta=meta,
            )
            attempts += 1
            api_calls += 1 if meta.get("api_call") else 0
            error = meta.get("error_class")
            if prob is not None:
                latency = meta.get("latency_ms")
                break
            if meta.get("skip_reason"):
                break
            time.sleep(2.0**attempt)
        return st, prob, latency, api_calls, error

    latencies: list[float] = []
    errors: dict[str, int] = {}
    failures = total_calls = 0
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        ThreadPoolExecutor(max_workers=8) as pool,
        open(cache_path, "a", encoding="utf-8", newline="\n") as out,
    ):
        for st, prob, latency, api_calls, error in pool.map(call, pending):
            total_calls += api_calls
            if error:
                errors[error] = errors.get(error, 0) + 1
            if prob is None:
                failures += 1
                continue
            st["p"]["JEV"] = _p_up(prob, st["side"])
            if latency is not None:
                latencies.append(float(latency))
            out.write(json.dumps({"h": st["jev_hash"], "p": round(prob, 6)}, separators=(",", ":")) + "\n")
    stats.update(
        status="ran",
        api_calls=total_calls,
        failures=failures,
        errors=errors,
        latency_ms_p50=_quantile(latencies, 0.5),
        latency_ms_p95=_quantile(latencies, 0.95),
        latency_ms_max=max(latencies) if latencies else None,
        scored=sum(1 for st in eligible if "JEV" in st["p"]),
    )
    return stats


def _p_up(agreement: float, side: str) -> float:
    """JEV answers P(movement corroborates side). Convert to P(Up)."""
    return agreement if side == "UP" else 1.0 - agreement


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def _resolve_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "typesafe-api", "-a", "sekun", "-w"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


# ------------------------------------------------------------------- loading


def load_btc(days: list[str], cache_dir: Path) -> tuple[list[float | None], int, dict[str, int]]:
    """Per-second price series. series[k] is the latest price known at second k."""
    t0 = int(datetime.fromisoformat(days[0]).replace(tzinfo=UTC).timestamp())
    n = len(days) * 86_400 + 120
    series: list[float | None] = [None] * n
    sources: dict[str, int] = {}
    for day in days:
        path = cache_dir / "btc" / f"{day}.csv"
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        source = lines[0].split("=", 1)[-1]
        sources[source] = sources.get(source, 0) + 1
        for line in lines[1:]:
            sec_s, price_s = line.split(",")
            idx = int(sec_s) - t0
            price = float(price_s)
            if source == "binance-rest-1m":
                # A minute's close is known only at its end; hold it for the next minute.
                for k in range(idx + 60, idx + 120):
                    if 0 <= k < n:
                        series[k] = price
            elif 0 <= idx + 1 < n:
                series[idx + 1] = price  # close of second idx, known at idx + 1
    return series, t0, sources


def btc_at(series: list[float | None], t0: int, t: int, max_age: int = 2) -> float | None:
    idx = t - t0
    for k in range(idx, idx - max_age - 1, -1):
        if 0 <= k < len(series) and series[k] is not None:
            return series[k]
    return None


def load_history(slug: str, cache_dir: Path) -> dict | None:
    path = cache_dir / "prices" / f"{slug}.json"
    if not path.exists():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    up = [(int(t), float(p)) for t, p in body.get("up", [])]
    down = [(int(t), float(p)) for t, p in body.get("down", [])]
    return {
        "up": up,
        "up_ts": [t for t, _ in up],
        "down": down,
        "down_ts": [t for t, _ in down],
    }


def poly_path(hist: dict, t: int) -> list[tuple[int, float, float]]:
    """(second, up, down) carry-forward quotes over the feature lookback, both sides known."""
    lo = t - POLYMARKET_FEATURE_LOOKBACK_SECONDS
    stamps = sorted({ts for ts in hist["up_ts"] + hist["down_ts"] if lo <= ts <= t})
    out = []
    for ts in stamps:
        u = last_known(hist["up"], hist["up_ts"], ts, 1e12)
        d = last_known(hist["down"], hist["down_ts"], ts, 1e12)
        if u is not None and d is not None:
            out.append((ts, u, d))
    return out


def build_windows(markets: list[dict], series, t0: int, cache_dir: Path, days: list[str]) -> tuple[list[dict], dict]:
    """Resolved windows with their decision states. Returns (windows, coverage)."""
    coverage = {
        "windows_expected": EXPECTED_WINDOWS,
        "windows_listed": len(markets),
        "windows_resolved": 0,
        "windows_no_btc_open": 0,
        "windows_no_price_history": 0,
        "states_expected": 0,
        "states_no_btc_price": 0,
        "states_no_contract_quote": 0,
        "states_scored": 0,
    }
    day_index = {d: i for i, d in enumerate(days)}
    windows: list[dict] = []
    seen = set()
    for m in markets:
        if m["slug"] in seen or m["resolved_up"] is None:
            continue
        seen.add(m["slug"])
        coverage["windows_resolved"] += 1
        hist = load_history(m["slug"], cache_dir)
        if hist is None:
            coverage["windows_no_price_history"] += 1
            continue
        open_px = btc_at(series, t0, m["start_ts"])
        if open_px is None:
            coverage["windows_no_btc_open"] += 1
            continue
        day = datetime.fromtimestamp(m["start_ts"], tz=UTC).date().isoformat()
        if day not in day_index:
            continue
        states = []
        for s in DECISION_SECONDS:
            coverage["states_expected"] += 1
            t = m["end_ts"] - s
            price_now = btc_at(series, t0, t)
            if price_now is None:
                coverage["states_no_btc_price"] += 1
                continue
            up_px = last_known(hist["up"], hist["up_ts"], t, MARKET_MAX_AGE_SECONDS)
            down_px = last_known(hist["down"], hist["down_ts"], t, MARKET_MAX_AGE_SECONDS)
            if up_px is None or down_px is None:
                coverage["states_no_contract_quote"] += 1
                continue
            price_path = [
                (k, series[k - t0])
                for k in range(t - TICK_WINDOW_SECONDS, t + 1)
                if 0 <= k - t0 < len(series) and series[k - t0] is not None
            ]
            st = build_state_features(
                open_price=open_px,
                price_path=price_path,
                poly_path=poly_path(hist, t),
                price_now=price_now,
                seconds_remaining=s,
                up_price=up_px,
                down_price=down_px,
            )
            st["p"] = {"MKT": st["mkt_up"]}
            st["t"] = t
            st["window"] = len(windows)
            st["week"] = day_index[day] // 7 + 1
            st["day"] = day
            st["jev_hash"] = jev_state_hash(st)
            states.append(st)
            coverage["states_scored"] += 1
        if not states:
            continue
        windows.append(
            {
                "slug": m["slug"],
                "start_ts": m["start_ts"],
                "end_ts": m["end_ts"],
                "resolved_up": bool(m["resolved_up"]),
                "day": day,
                "week": day_index[day] // 7 + 1,
                "states": states,
            }
        )
    windows.sort(key=lambda w: w["start_ts"])
    for i, w in enumerate(windows):
        w["index"] = i
        for st in w["states"]:
            st["window"] = i
            st["resolved_up"] = w["resolved_up"]
            st["end_ts"] = w["end_ts"]
    coverage["windows_with_states"] = len(windows)
    return windows, coverage


def jev_state_hash(st: dict) -> str:
    import hashlib

    state = {
        "strongest_side": st["side"],
        "seconds_remaining": st["s"],
        "tick_move_pct": st["tick_move"],
        "directional_consistency": st["consistency"],
        "acceleration_pct": st["accel"],
        "up_price_momentum": st["up_mom"],
        "down_price_momentum": st["down_mom"],
    }
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_model_probs(windows: list[dict], ledger_path: Path) -> None:
    """Fill st['p'] with A, B (pass 1), then C and P against the look-ahead-safe ledger."""
    records = []
    for w in windows:
        for st in w["states"]:
            st["p"]["A"] = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "A", context=st["context"])
            st["p"]["B"] = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "B", context=st["context"])
            records.append(
                (w["end_ts"], ledger_record(st, w["end_ts"], w["resolved_up"], st["p"]["B"]))
            )
    records.sort(key=lambda item: item[0])
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8", newline="\n") as handle:
        for _, rec in records:
            handle.write(json.dumps(rec, separators=(",", ":")) + "\n")
    for w in windows:
        for st in w["states"]:
            kwargs = {"ledger_path": str(ledger_path), "context": st["context"], "decision_ts": st["t"]}
            st["p"]["C"] = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "C", **kwargs)
            st["p"]["P"] = get_probability(st["S0"], st["S"], st["s"], st["sigma"], "P", **kwargs)


# ------------------------------------------------------------------- analysis


def all_states(windows: list[dict]) -> list[dict]:
    return [st for w in windows for st in w["states"]]


def paired_brier_diff(states: list[dict], model: str) -> tuple[float | None, float | None, float | None]:
    """Mean of (model Brier - MKT Brier) per state, with window-cluster 95% CI. Negative = model better."""
    diffs, clusters = [], []
    for st in states:
        p_model = st["p"].get(model)
        if p_model is None:
            continue
        y = 1.0 if st["resolved_up"] else 0.0
        diffs.append((p_model - y) ** 2 - (st["mkt_up"] - y) ** 2)
        clusters.append(st["window"])
    if not diffs:
        return None, None, None
    lo, hi = cluster_mean_ci(diffs, clusters)
    return statistics.fmean(diffs), lo, hi


def analyse(windows: list[dict], coverage: dict, jev_stats: dict) -> dict:
    states = all_states(windows)
    result: dict = {"coverage": coverage, "jev": jev_stats}
    result["headline"] = {}
    result["by_time"] = {}
    for name in PROB_NAMES:
        pairs = [(st["p"][name], st) for st in states if st["p"].get(name) is not None]
        result["headline"][name] = score_block([p for p, _ in pairs], [int(bool(st["resolved_up"])) for _, st in pairs])
    for name in PROB_NAMES:
        mean_d, lo, hi = paired_brier_diff(states, name)
        result["headline"][name]["brier_vs_mkt"] = {"mean": mean_d, "ci": [lo, hi]}
    for s in DECISION_SECONDS:
        sub = [st for st in states if st["s"] == s]
        result["by_time"][str(s)] = {
            name: score_block([st["p"][name] for st in sub], [int(bool(st["resolved_up"])) for st in sub])
            for name in PROB_NAMES
        }
    jev_states = [st for st in states if "JEV" in st["p"]]
    result["jev_subset"] = {
        "n_states": len(jev_states),
        **{
            name: score_block([st["p"][name] for st in jev_states], [int(bool(st["resolved_up"])) for st in jev_states])
            for name in ("JEV", "P", "A", "MKT")
        },
    }
    result["sim"] = {}
    window_states = [w["states"] for w in windows]
    for model in MODELS:
        trades = simulate(window_states, model)
        result["sim"][model] = summarize_trades(trades)
    weeks = sorted({w["week"] for w in windows})
    result["weekly"] = []
    for week in weeks:
        ws = [w for w in windows if w["week"] == week]
        sts = [st for w in ws for st in w["states"]]
        row = {
            "week": week,
            "days": sorted({w["day"] for w in ws}),
            "windows": len(ws),
            "brier": {},
            "hit": {},
        }
        for name in PROB_NAMES:
            pairs = [st for st in sts if st["p"].get(name) is not None]
            blk = score_block([st["p"][name] for st in pairs], [int(bool(st["resolved_up"])) for st in pairs])
            row["brier"][name] = blk.get("brier")
            row["hit"][name] = blk.get("hit_rate")
        trades_by_model = {}
        for model in MODELS:
            trades_by_model[model] = [t for t in simulate([w["states"] for w in ws], model)]
            row.setdefault("trades", {})[model] = len(trades_by_model[model])
            row.setdefault("mean_pnl", {})[model] = (
                statistics.fmean(t["pnl"] for t in trades_by_model[model]) if trades_by_model[model] else None
            )
        result["weekly"].append(row)
    first = [w for w in windows if w["week"] <= 3]
    last = [w for w in windows if w["week"] == max(weeks)]
    result["split"] = {
        "first_3_weeks": split_block(first),
        "last_week": split_block(last),
        "note": "days 22-23 of the period belong to neither split",
    }
    result["windows"] = [
        [w["start_ts"], int(w["resolved_up"]), next((round(st["mkt_up"], 3) for st in w["states"] if st["s"] == 60), None)]
        for w in windows
    ]
    return result


def split_block(ws: list[dict]) -> dict:
    sts = [st for w in ws for st in w["states"]]
    out: dict = {"windows": len(ws), "brier": {}, "hit": {}}
    for name in PROB_NAMES:
        pairs = [st for st in sts if st["p"].get(name) is not None]
        blk = score_block([st["p"][name] for st in pairs], [int(bool(st["resolved_up"])) for st in pairs])
        out["brier"][name] = blk.get("brier")
        out["hit"][name] = blk.get("hit_rate")
    out["trades"] = {}
    for model in MODELS:
        trades = simulate([w["states"] for w in ws], model)
        out["trades"][model] = summarize_trades(trades)
    return out


def summarize_trades(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for t in trades if t["won"])
    lo, hi = br.wilson_interval(wins, len(trades))
    c_lo, c_hi = cluster_mean_ci(pnls, [t["window"] for t in trades])
    return {
        "n": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades),
        "win_ci": [lo, hi],
        "mean_pnl": statistics.fmean(pnls),
        "mean_pnl_ci": [c_lo, c_hi],
        "total_pnl": sum(pnls),
        "max_drawdown": br.max_drawdown(pnls),
        "mean_model_ev": statistics.fmean(t["ev_net"] for t in trades),
        "mean_quote": statistics.fmean(t["quote"] for t in trades),
        "mean_fee": statistics.fmean(t["fee"] for t in trades),
    }


# ------------------------------------------------------------------ rendering


def _f(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _ci(pair: list | None, digits: int = 3) -> str:
    if not pair or pair[0] is None:
        return "n/a"
    return f"[{pair[0]:.{digits}f}, {pair[1]:.{digits}f}]"


def render_markdown(result: dict, generated_on: str) -> str:
    cov = result["coverage"]
    head = result["headline"]
    lines: list[str] = []
    add = lines.append
    add("# Month-long backtest: probability models vs real Polymarket settlements")
    add("")
    period = result["period"]
    add(f"_Generated by `research/month_backtest/run.py` on {generated_on} (UTC). Do not edit by hand._")
    add("")
    add(
        f"Period: {period['start_day']} to {period['end_day']} (UTC), {period['days']} complete days. "
        f"BTC price sources by day: {', '.join(f'{k} x{v}' for k, v in sorted(result['coverage'].get('btc_sources', {}).items()))}."
    )
    add("")
    add(
        "Outcomes are the settled Up/Down results from Polymarket's gamma API (`outcomePrices`), not a "
        "price proxy. Decisions are rebuilt at seconds remaining 90, 60, 30 and 10 from Binance BTCUSDT "
        "1-second prices and the contract quotes in the CLOB price history. Model C only uses resolutions "
        "strictly before each decision time."
    )
    add("")
    add("## Coverage")
    add("")
    add("| Item | Count |")
    add("|---|---:|")
    add(f"| Windows expected (30 days x 288) | {cov['windows_expected']} |")
    add(f"| Windows listed by gamma (closed, BTC up-or-down 5m) | {cov['windows_listed']} |")
    add(f"| Windows with a settled outcome | {cov['windows_resolved']} |")
    add(f"| Windows dropped: no contract price history | {cov['windows_no_price_history']} |")
    add(f"| Windows dropped: no BTC price at window open | {cov['windows_no_btc_open']} |")
    add(f"| Windows with at least one scored state | {cov['windows_with_states']} |")
    add(f"| Decision states expected (windows x 4 times) | {cov['states_expected']} |")
    add(f"| States dropped: no BTC price at decision time | {cov['states_no_btc_price']} |")
    add(f"| States dropped: no contract quote within 120 s | {cov['states_no_contract_quote']} |")
    add(f"| States scored | {cov['states_scored']} |")
    add("")
    add("## Headline (all decision states, pooled)")
    add("")
    add("Brier and log loss: lower is better. Hit rate counts calls on the side of 0.5 with a 95% Wilson CI. ")
    add("`Brier - MKT` is the paired per-state difference with a window-clustered bootstrap 95% CI; negative means the model beats the market.")
    add("")
    add("| Model | States | Brier | Brier - MKT [95% CI] | Log loss | Hit rate [95% CI] | ECE |")
    add("|---|---:|---:|---|---:|---|---:|")
    for name in PROB_NAMES:
        h = head[name]
        bvm = h.get("brier_vs_mkt", {})
        diff = "baseline" if name == "MKT" else f"{_f(bvm.get('mean'))} {_ci(bvm.get('ci'))}"
        add(
            f"| {name} | {h['n']} | {_f(h.get('brier'), 4)} | {diff} | {_f(h.get('log_loss'), 4)} | "
            f"{_f(h.get('hit_rate'), 3)} {_ci(h.get('hit_ci'))} | {_f(h.get('ece'), 3)} |"
        )
    add("")
    add("## Reliability (10 bins, pooled)")
    add("")
    for name in ("P", "MKT"):
        add(f"**{name}**")
        add("")
        add("| Bin | n | Mean predicted | Observed Up |")
        add("|---|---:|---:|---:|")
        for row in head[name].get("reliability", []):
            add(f"| {row['lo']:.1f}-{row['hi']:.1f} | {row['n']} | {_f(row['mean_p'])} | {_f(row['freq'])} |")
        add("")
    add("## By decision time")
    add("")
    add("| Seconds left | Model | States | Brier | Hit rate |")
    add("|---:|---|---:|---:|---:|")
    for s in DECISION_SECONDS:
        blocks = result["by_time"][str(s)]
        for name in PROB_NAMES:
            b = blocks[name]
            add(f"| {s} | {name} | {b.get('n', 0)} | {_f(b.get('brier'), 4)} | {_f(b.get('hit_rate'))} |")
    add("")
    add("## Weekly stability")
    add("")
    add("Week 1 = days 1-7 of the period, oldest first. Week 5 is partial.")
    add("")
    add("| Week | Days | Windows | Brier A | Brier B | Brier C | Brier P | Brier MKT | Hit P | Hit MKT | Trades (A/B/C/P) | Mean realized PnL (P) |")
    add("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    for row in result["weekly"]:
        days = f"{row['days'][0][5:]} to {row['days'][-1][5:]}"
        tr = row["trades"]
        add(
            f"| {row['week']} | {days} | {row['windows']} | {_f(row['brier']['A'], 4)} | {_f(row['brier']['B'], 4)} | "
            f"{_f(row['brier']['C'], 4)} | {_f(row['brier']['P'], 4)} | {_f(row['brier']['MKT'], 4)} | "
            f"{_f(row['hit']['P'])} | {_f(row['hit']['MKT'])} | "
            f"{tr['A']}/{tr['B']}/{tr['C']}/{tr['P']} | {_f(row['mean_pnl']['P'], 3)} |"
        )
    add("")
    split = result["split"]
    add("## Chronological split")
    add("")
    add(f"First 3 weeks = days 1-21 ({split['first_3_weeks']['windows']} windows); last week = days 24-30 ({split['last_week']['windows']} windows). {split['note'][0].upper() + split['note'][1:]}.")
    add("")
    add("| Model | Brier first 3 wk | Brier last wk | Hit first 3 wk | Hit last wk | Trades first 3 wk | Trades last wk | Mean PnL first 3 wk | Mean PnL last wk |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in PROB_NAMES:
        a = split["first_3_weeks"]
        b = split["last_week"]
        ta = a["trades"].get(name, {}) if name in a["trades"] else {}
        tb = b["trades"].get(name, {}) if name in b["trades"] else {}
        add(
            f"| {name} | {_f(a['brier'][name], 4)} | {_f(b['brier'][name], 4)} | {_f(a['hit'][name])} | {_f(b['hit'][name])} | "
            f"{ta.get('n', '-')} | {tb.get('n', '-')} | {_f(ta.get('mean_pnl'), 3)} | {_f(tb.get('mean_pnl'), 3)} |"
            if name != "MKT"
            else f"| MKT | {_f(a['brier'][name], 4)} | {_f(b['brier'][name], 4)} | {_f(a['hit'][name])} | {_f(b['hit'][name])} | - | - | - | - |"
        )
    add("")
    add("## Trading simulation (bot mispricing entry rules, held to settlement)")
    add("")
    add(
        f"Rules: quoted side price in [{ENTRY_MIN_CONTRACT_PRICE:.2f}, {ENTRY_MAX_CONTRACT_PRICE:.2f}], "
        f"edge >= {EDGE_THRESHOLD:.2f}, expected profit net of fee and {EXPECTED_SLIPPAGE_BPS:.0f} bps slippage "
        f">= {MIN_EXPECTED_PROFIT_USDC:.2f} USDC, one entry per window at the earliest qualifying decision time, "
        f"fixed stake {STAKE_USDC:.0f} USDC, Polymarket crypto taker fee (rate {CRYPTO_FEE_RATE}). No exits, hedges or conviction regime "
        "(those need order-book depth this study does not have). Mean PnL CI resamples windows."
    )
    add("")
    add("| Model | Trades | Win rate [95% CI] | Mean realized PnL / trade [95% CI] | Model EV / trade (net) | Mean quote | Total PnL | Max drawdown |")
    add("|---|---:|---|---|---:|---:|---:|---:|")
    for name in MODELS:
        s = result["sim"][name]
        if not s.get("n"):
            add(f"| {name} | 0 | n/a | n/a | n/a | n/a | 0.00 | 0.00 |")
            continue
        add(
            f"| {name} | {s['n']} | {_f(s['win_rate'])} {_ci(s['win_ci'])} | {_f(s['mean_pnl'], 3)} {_ci(s['mean_pnl_ci'])} | "
            f"{_f(s['mean_model_ev'], 3)} | {_f(s['mean_quote'])} | {_f(s['total_pnl'], 2)} | {_f(s['max_drawdown'], 2)} |"
        )
    add("")
    add("## JEV (optional second opinion, directional states only)")
    add("")
    jev_stats = result["jev"]
    add(f"- Status: `{jev_stats.get('status')}`; eligible directional states: {jev_stats.get('eligible', 0)}")
    if jev_stats.get("status") == "ran":
        add(
            f"- Network calls: {jev_stats.get('api_calls')}; failures after retries: {jev_stats.get('failures')}; "
            f"disk-cache hits: {jev_stats.get('cache_hits')}; sampled run: {jev_stats.get('sampled')}"
        )
        add(
            f"- Latency of successful calls (ms): p50 {_f(jev_stats.get('latency_ms_p50'), 0)}, "
            f"p95 {_f(jev_stats.get('latency_ms_p95'), 0)}, max {_f(jev_stats.get('latency_ms_max'), 0)}"
        )
        add(f"- Error classes: {jev_stats.get('errors') or 'none'}")
    subset = result["jev_subset"]
    add("")
    add(f"Scored on the {subset['n_states']} states where JEV answered (same states for every row):")
    add("")
    add("| Model | States | Brier | Hit rate [95% CI] |")
    add("|---|---:|---:|---|")
    for name in ("JEV", "P", "A", "MKT"):
        b = subset[name]
        add(f"| {name} | {b.get('n', 0)} | {_f(b.get('brier'), 4)} | {_f(b.get('hit_rate'))} {_ci(b.get('hit_ci'))} |")
    add("")
    add("## Conclusion")
    add("")
    add(conclusion(result))
    add("")
    add("## Limitations")
    add("")
    add(
        "- Window open and decision prices come from Binance BTCUSDT 1-second klines. Polymarket resolves on "
        "Chainlink BTC/USD (`resolutionSource`), so the open and final prices can differ slightly from the settlement reference."
    )
    add(
        "- Contract prices come from the CLOB prices-history endpoint at its finest fidelity (about one point per "
        "minute with gaps). A quote must be no older than 120 s; otherwise the state is dropped and counted above."
    )
    add(
        "- Features are approximations of the bot's live features. The contract quote is the mid of the Up and Down tokens, "
        "normalized; market conviction is proxied by the strongest-side price; book depth, queue imbalance, microprice and "
        "one-sidedness are set to neutral because the history has no book data. Model C is calibrated on all prior decision "
        "states, not only on the bot's traded resolutions."
    )
    add(
        "- The trading sim is a simplified rule set: fixed stake, no exits, no hedges, no late-conviction regime, no fill or "
        "queue model beyond a flat slippage charge."
    )
    add(
        "- The sim's positive PnL is most likely a data artifact, not an edge: contract quotes are up to 120 s old while "
        "BTC prices are per second, so a model can react to BTC moves the stale quote has not priced yet. The same models "
        "lose to the market on Brier, which is the cleaner test. Live trading would face fresh quotes and real fills."
    )
    add(
        "- Decision states inside one window are correlated. Brier and hit-rate CIs use Wilson intervals on states, while "
        "the paired Brier differences and trade PnL CIs resample whole windows."
    )
    add("")
    add("## Reproduce")
    add("")
    add("```bash")
    add("python research/month_backtest/fetch.py --days 30")
    add("python research/month_backtest/run.py            # JEV runs when a key is available")
    add("python research/month_backtest/run.py --no-jev   # no network")
    add("```")
    add("")
    return "\n".join(lines)


def conclusion(result: dict) -> str:
    head = result["headline"]
    best = None
    for name in ("A", "B", "C", "P"):
        bvm = head[name].get("brier_vs_mkt", {})
        if bvm.get("ci") and bvm["ci"][1] is not None and bvm["ci"][1] < 0:
            best = name if best is None or bvm["mean"] < head[best]["brier_vs_mkt"]["mean"] else best
    sim_pos = [
        name for name in MODELS
        if result["sim"][name].get("mean_pnl_ci") and result["sim"][name]["mean_pnl_ci"][0] is not None
        and result["sim"][name]["mean_pnl_ci"][0] > 0
    ]
    if best is None:
        calibration = (
            "No model beats the contract-price baseline on Brier with a 95% CI below zero over the month."
        )
    else:
        calibration = (
            f"Model {best} has a lower Brier than the contract-price baseline with a 95% CI below zero "
            f"({_f(head[best]['brier_vs_mkt']['mean'], 4)} {_ci(head[best]['brier_vs_mkt']['ci'], 4)}). "
            "Check the weekly table: a single good week is not evidence of edge."
        )
    if sim_pos:
        trading = (
            f"The simulated trades of {', '.join(sim_pos)} have a mean realized PnL per trade whose 95% CI lies above zero "
            "after fees and slippage in this simplified sim."
        )
    else:
        trading = (
            "No model's simulated trades have a mean realized PnL per trade whose 95% CI lies above zero after fees and "
            "slippage. The sim does not show a surviving edge over the market baseline."
        )
    return (
        f"{calibration} {trading} Treat the result as a first month-long measurement, not a live edge claim: the "
        "trade sim omits exits, hedges and book-depth gating, and the feature set is approximated."
    )


# ----------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end-day", default=None, help="last complete UTC day (default: yesterday)")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    parser.add_argument("--no-jev", action="store_true")
    parser.add_argument("--summary", default=str(SUMMARY_PATH))
    parser.add_argument("--docs", default=str(DOCS_PATH))
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    end = fetch.last_complete_day() if args.end_day is None else datetime.fromisoformat(args.end_day).date()
    days = fetch.day_range(end, args.days)

    markets: list[dict] = []
    for day in days:
        path = cache_dir / "markets" / f"{day}.json"
        if path.exists():
            markets.extend(json.loads(path.read_text(encoding="utf-8")))
    series, t0, sources = load_btc(days, cache_dir)
    windows, coverage = build_windows(markets, series, t0, cache_dir, days)
    coverage["btc_sources"] = sources
    print(f"windows with states: {len(windows)} of {EXPECTED_WINDOWS} expected; btc sources {sources}", file=sys.stderr)

    ledger_path = cache_dir / "runs" / "model_c_ledger.jsonl"
    compute_model_probs(windows, ledger_path)

    jev_max = int(os.environ.get("JEV_MAX_CALLS", "0") or 0)
    jev_stats: dict = {"status": "disabled"}
    if not args.no_jev:
        jev_stats = run_jev(all_states(windows), cache_dir, jev_max)
    result = analyse(windows, coverage, jev_stats)
    result["period"] = {"start_day": days[0], "end_day": days[-1], "days": len(days)}

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in result.items() if k != "windows"}
    summary["windows"] = result["windows"]
    summary["columns"] = {"windows": ["start_ts", "resolved_up", "mkt_up_at_60s"]}
    text = json.dumps(summary, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) > SUMMARY_MAX_BYTES:
        raise SystemExit(f"summary exceeds {SUMMARY_MAX_BYTES} bytes")
    summary_path.write_text(text + "\n", encoding="utf-8")

    Path(args.docs).write_text(render_markdown(result, generated), encoding="utf-8", newline="\n")
    print(f"wrote {args.docs} and {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
