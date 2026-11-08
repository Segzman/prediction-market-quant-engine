#!/usr/bin/env python3
"""Chronological evaluation report over logged decision states.

Modes:
  --build-sample SRC_DIR   rebuild research/data/sample_trades.jsonl from the raw
                           trades_both.jsonl and trades_conviction.jsonl logs.
  (default)                read the sample, split chronologically by window,
                           write docs/backtest_report.md and, when matplotlib is
                           installed, docs/backtest_reliability.png.

Outcome labels are a PROXY. The raw logs carry no settlement result: resolution
records are early exits with resolved_up=None. A window is labelled UP when its
last observed BTC price is above the window open price. Windows whose last
observation is more than LABEL_MAX_GAP_SECONDS before close are excluded.

Pure stdlib. matplotlib is optional (reliability PNG only).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import POLYMARKET_FEATURE_LOOKBACK_SECONDS  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
SAMPLE_PATH = DATA_DIR / "sample_trades.jsonl"
JEV_CACHE_PATH = DATA_DIR / "jev_replay_cache.jsonl"
REPORT_PATH = REPO_ROOT / "docs" / "backtest_report.md"
PNG_PATH = REPO_ROOT / "docs" / "backtest_reliability.png"

MODELS = ("A", "B", "C", "P")
PROB_NAMES = MODELS + ("market",)
TRAIN_FRACTION = 0.7
SAMPLE_STRIDE = 16
LABEL_MAX_GAP_SECONDS = 30
TICK_LOOKBACK_SECONDS = 12.0
BOOTSTRAP_ITERS = 2000
BOOTSTRAP_SEED = 7
Z95 = 1.959963984540054
SOURCE_FILES = (
    ("both", "trades_both.jsonl"),
    ("conviction", "trades_conviction.jsonl"),
)


# ---------------------------------------------------------------- metrics


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def brier(probs, outcomes):
    return mean([(p - y) ** 2 for p, y in zip(probs, outcomes, strict=False)])


def log_loss(probs, outcomes, eps=1e-6):
    terms = []
    for p, y in zip(probs, outcomes, strict=False):
        p = min(1 - eps, max(eps, p))
        terms.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return mean(terms)


def reliability(probs, outcomes, bins=10):
    """Return (rows, ece). Each row: bin range, n, mean predicted, observed freq."""
    buckets = [[] for _ in range(bins)]
    for p, y in zip(probs, outcomes, strict=False):
        buckets[min(bins - 1, int(p * bins))].append((p, y))
    n = len(probs)
    rows = []
    ece = 0.0
    for i, bucket in enumerate(buckets):
        row = {"lo": i / bins, "hi": (i + 1) / bins, "n": len(bucket)}
        if bucket:
            mp = mean([p for p, _ in bucket])
            freq = mean([y for _, y in bucket])
            ece += len(bucket) / n * abs(mp - freq)
            row.update(mean_p=mp, freq=freq)
        else:
            row.update(mean_p=None, freq=None)
        rows.append(row)
    return rows, (ece if n else None)


def directional_hits(probs, outcomes):
    """Hits and count where the call is UP (p > 0.5) or DOWN (p < 0.5)."""
    hits = n = 0
    for p, y in zip(probs, outcomes, strict=False):
        if p == 0.5:
            continue
        n += 1
        hits += int((p > 0.5) == bool(y))
    return hits, n


def wilson_interval(successes, n, z=Z95):
    if n == 0:
        return None, None
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def max_drawdown(pnls):
    """Largest peak-to-trough fall of the cumulative PnL, starting from 0."""
    peak = cum = worst = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def cluster_bootstrap_mean_ci(values, clusters, iters=BOOTSTRAP_ITERS, seed=BOOTSTRAP_SEED):
    """95% percentile CI of the mean, resampling whole clusters (windows)."""
    groups = defaultdict(list)
    for v, c in zip(values, clusters, strict=False):
        groups[c].append(v)
    keys = list(groups)
    if not keys:
        return None, None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        total = count = 0
        for _ in keys:
            g = groups[rng.choice(keys)]
            total += sum(g)
            count += len(g)
        means.append(total / count)
    means.sort()
    return means[int(0.025 * iters)], means[min(iters - 1, int(0.975 * iters))]


def trade_pnl(stake, price, fee, won):
    """Binary settlement: stake buys stake/price shares paying 1 if won."""
    return (stake / price if won else 0.0) - stake - fee


def expected_trade_pnl(stake, price, p_side, fee):
    """Model-implied expected PnL of the same trade, net of fee."""
    return stake * (p_side / price) - stake - fee


def percentile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(q * len(s)) - 1)]


# ------------------------------------------------- JEV feature reconstruction


def tick_features(price_hist):
    """Mirror of state.session.BotState.tick_trend_features on (ts, price) pairs."""
    if len(price_hist) < 3:
        return 0.0, 0.5, 0.0
    latest = price_hist[-1][0]
    samples = [(t, p) for t, p in price_hist if latest - t <= TICK_LOOKBACK_SECONDS]
    if len(samples) < 3:
        samples = price_hist[-3:]
    first, last = samples[0][1], samples[-1][1]
    tick_move = (last - first) / first if first > 0 else 0.0
    moves = [b[1] - a[1] for a, b in zip(samples, samples[1:], strict=False) if a[1] > 0 and b[1] > 0]
    if moves:
        pos = sum(1 for m in moves if m > 0)
        neg = sum(1 for m in moves if m < 0)
        consistency = max(pos, neg) / len(moves)
    else:
        consistency = 0.5
    mid = max(1, len(samples) // 2)

    def seg(part):
        start = part[0][1]
        return (part[-1][1] - start) / start if start > 0 else 0.0

    accel = seg(samples[mid:]) - seg(samples[: mid + 1])
    return tick_move, consistency, accel


def poly_momentum(poly_hist):
    """Mirror of BotState.polymarket_features momentum on (ts, up, down) tuples."""
    samples = list(poly_hist)
    latest = samples[-1][0]
    window = [s for s in samples if latest - s[0] <= POLYMARKET_FEATURE_LOOKBACK_SECONDS]
    if len(window) < 2:
        window = samples[-2:]

    def mom(values):
        if len(values) < 2:
            return 0.0
        start, end = values[0], values[-1]
        return (end - start) / start if start > 0 else 0.0

    return mom([s[1] for s in window]), mom([s[2] for s in window])


# ------------------------------------------------------------ sample build


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def build_sample(src_dir, out_path=SAMPLE_PATH, stride=SAMPLE_STRIDE):
    """Write the anonymized evaluation sample. Drops order, wallet and market ids."""
    src_dir = Path(src_dir)
    meta = {
        "stride": stride,
        "label_max_gap_s": LABEL_MAX_GAP_SECONDS,
        "windows_total": 0,
        "windows_excluded_gap": 0,
        "candidates_missing_probs": 0,
        "rows": 0,
    }
    out = []
    for src_name, fname in SOURCE_FILES:
        groups = defaultdict(list)
        for r in _load_jsonl(src_dir / fname):
            if r.get("type") == "candidate" and r.get("seconds_remaining") is not None:
                groups[r["window_end_ts"]].append(r)
        for w, rs in groups.items():
            rs.sort(key=lambda r: r["ts"])
            meta["windows_total"] += 1
            final = min(rs, key=lambda r: r["seconds_remaining"])
            if final["seconds_remaining"] > LABEL_MAX_GAP_SECONDS:
                meta["windows_excluded_gap"] += 1
                continue
            y = int(final["btc_price"] > final["open_price"])
            price_hist, poly_hist = [], []
            for i, r in enumerate(rs):
                if r.get("btc_price") is not None:
                    price_hist.append((r["ts"], r["btc_price"]))
                if r.get("up_price") is not None and r.get("down_price") is not None:
                    poly_hist.append((r["ts"], r["up_price"], r["down_price"]))
                keep = i % stride == 0 or bool(r["would_trade"])
                probs = r.get("probabilities") or {}
                if (
                    not all(m in probs for m in MODELS)
                    or r.get("up_price") is None
                    or r.get("down_price") is None
                    or r.get("btc_price") is None
                ):
                    meta["candidates_missing_probs"] += 1
                    continue
                if not keep:
                    continue
                tm, dc, ac = tick_features(price_hist)
                um, dm = poly_momentum(poly_hist)
                row = {
                    "src": src_name,
                    "w": w,
                    "ts": round(r["ts"], 1),
                    "s": int(r["seconds_remaining"]),
                    "side": r["strongest_side"],
                    "up": round(r["up_price"], 4),
                    "dn": round(r["down_price"], 4),
                    "p": {m: round(probs[m], 4) for m in MODELS},
                    "y": y,
                    "bet": bool(r["would_trade"]),
                    "jev": {
                        "tm": round(tm, 6),
                        "dc": round(dc, 4),
                        "ac": round(ac, 6),
                        "um": round(um, 6),
                        "dm": round(dm, 6),
                    },
                }
                if row["bet"]:
                    row["px"] = round(r["market_price"], 4)
                    row["stake"] = round(r["planned_bet_usdc"], 4)
                    row["fee"] = round(r["estimated_cost_usdc"], 6)
                out.append(row)
    out.sort(key=lambda r: (r["w"], r["src"], r["ts"]))
    meta["rows"] = len(out)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"_meta": meta}, separators=(",", ":")) + "\n")
        for row in out:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return meta


# ------------------------------------------------------- load / split / JEV


def load_sample(path=SAMPLE_PATH):
    meta, rows = {}, []
    for r in _load_jsonl(path):
        if "_meta" in r:
            meta = r["_meta"]
        else:
            rows.append(r)
    return meta, rows


def split_chronological(rows, train_fraction=TRAIN_FRACTION):
    """Earliest windows train, latest windows test. A window never straddles."""
    windows = sorted({r["w"] for r in rows})
    train_w = set(windows[: int(len(windows) * train_fraction)])
    train = [r for r in rows if r["w"] in train_w]
    test = [r for r in rows if r["w"] not in train_w]
    return train, test


def jev_state_hash(row):
    j = row["jev"]
    state = {
        "strongest_side": row["side"],
        "seconds_remaining": row["s"],
        "tick_move_pct": j["tm"],
        "directional_consistency": j["dc"],
        "acceleration_pct": j["ac"],
        "up_price_momentum": j["um"],
        "down_price_momentum": j["dm"],
    }
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_jev_cache(path=JEV_CACHE_PATH):
    cache = {}
    if Path(path).exists():
        for rec in _load_jsonl(path):
            cache[rec["state_hash"]] = rec
    return cache


def is_directional(row):
    return row["side"] in ("UP", "DOWN")


def p_up_from_agreement(prob_agree, side):
    """JEV answers P(movement corroborates side); convert to P(Up)."""
    return prob_agree if side == "UP" else 1.0 - prob_agree


def mkt_up(row):
    total = row["up"] + row["dn"]
    return row["up"] / total if total > 0 else 0.5


# ----------------------------------------------------------------- report


def _f(x, nd=4):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ci(lo, hi, nd=3):
    return "n/a" if lo is None else f"[{lo:.{nd}f}, {hi:.{nd}f}]"


def _test_probs(rows, name):
    if name == "market":
        return [mkt_up(r) for r in rows]
    return [r["p"][name] for r in rows]


def prob_row(name, probs, ys):
    hits, n_dir = directional_hits(probs, ys)
    lo, hi = wilson_interval(hits, n_dir)
    _, ece = reliability(probs, ys)
    return {
        "name": name,
        "n": len(ys),
        "brier": brier(probs, ys),
        "logloss": log_loss(probs, ys),
        "ece": ece,
        "hits": hits,
        "n_dir": n_dir,
        "hit_rate": hits / n_dir if n_dir else None,
        "ci": (lo, hi),
    }


def prob_table(rows, jev_map=None):
    ys = [r["y"] for r in rows]
    cols = {m: [r["p"][m] for r in rows] for m in MODELS}
    cols["market"] = [mkt_up(r) for r in rows]
    if jev_map is not None:
        cols["JEV"] = [jev_map[id(r)] for r in rows]
    return [prob_row(name, cols[name], ys) for name in cols]


def first_bets(rows):
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["ts"]):
        if not r["bet"]:
            continue
        key = (r["src"], r["w"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def trade_stats(bets, p_sides=None):
    """p_sides: optional {name: [p_side per trade]} for model-implied EV."""
    outcomes = []
    pnls = []
    clusters = []
    for r in bets:
        won = (r["side"] == "UP") == bool(r["y"])
        outcomes.append(won)
        pnls.append(trade_pnl(r["stake"], r["px"], r["fee"], won))
        clusters.append(r["w"])
    hits = sum(outcomes)
    lo, hi = wilson_interval(hits, len(outcomes))
    ci_lo, ci_hi = cluster_bootstrap_mean_ci(pnls, clusters)
    stats = {
        "n": len(bets),
        "hits": hits,
        "hit_rate": hits / len(bets) if bets else None,
        "hit_ci": (lo, hi),
        "mean_pnl": mean(pnls),
        "pnl_ci": (ci_lo, ci_hi),
        "total_pnl": sum(pnls),
        "max_dd": max_drawdown(pnls),
        "ev": {},
    }
    for name, ps in (p_sides or {}).items():
        evs = [expected_trade_pnl(r["stake"], r["px"], p, r["fee"]) for r, p in zip(bets, ps, strict=False)]
        stats["ev"][name] = mean(evs)
    return stats


def p_side_list(bets, probs_up):
    return [p if r["side"] == "UP" else 1 - p for r, p in zip(bets, probs_up, strict=False)]


def build_report(meta, rows):
    train, test = split_chronological(rows)
    cache = load_jev_cache()
    test_w = sorted({r["w"] for r in test})
    train_w = sorted({r["w"] for r in train})
    test_dir = [r for r in test if is_directional(r)]
    covered = []
    jev_map = {}
    jev_latency = []
    for r in test_dir:
        rec = cache.get(jev_state_hash(r))
        if rec is None or rec.get("probability") is None:
            continue
        covered.append(r)
        jev_map[id(r)] = p_up_from_agreement(rec["probability"], r["side"])
        jev_latency.append(rec["latency_ms"])

    full_ys = [r["y"] for r in test]
    full_table = prob_table(test)
    sub_table = prob_table(covered, jev_map) if covered else []

    bets = first_bets(test)
    bet_side_probs = {m: p_side_list(bets, [r["p"][m] for r in bets]) for m in MODELS}
    bet_side_probs["market"] = p_side_list(bets, [mkt_up(r) for r in bets])
    trades = trade_stats(bets, bet_side_probs)

    jev_bets = [r for r in bets if id(r) in jev_map]
    jev_trades = None
    if jev_bets:
        jev_trades = trade_stats(jev_bets, {"JEV": p_side_list(jev_bets, [jev_map[id(r)] for r in jev_bets])})

    return {
        "meta": meta,
        "rows": len(rows),
        "windows_train": len(train_w),
        "windows_test": len(test_w),
        "states_train": len(train),
        "states_test": len(test),
        "test_directional": len(test_dir),
        "jev_covered": len(covered),
        "jev_pending": len(test_dir) - len(covered),
        "jev_latency": jev_latency,
        "full_table": full_table,
        "sub_table": sub_table,
        "full_ys": full_ys,
        "bets": len(bets),
        "trades": trades,
        "jev_trades": jev_trades,
        "covered": covered,
        "jev_map": jev_map,
        "test": test,
    }


def _prob_md(table):
    lines = [
        "| Model | n | Brier | Log loss | ECE (10 bins) | Directional hit rate | 95% Wilson CI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for t in table:
        lines.append(
            f"| {t['name']} | {t['n']} | {_f(t['brier'])} | {_f(t['logloss'])} | "
            f"{_f(t['ece'])} | {_f(t['hit_rate'])} ({t['hits']}/{t['n_dir']}) | "
            f"{_ci(*t['ci'])} |"
        )
    return "\n".join(lines)


def _reliability_md(probs, ys):
    rel, _ = reliability(probs, ys)
    lines = ["| Bin | n | Mean predicted | Observed freq |", "|---|---:|---:|---:|"]
    for b in rel:
        lines.append(f"| [{b['lo']:.1f}, {b['hi']:.1f}) | {b['n']} | {_f(b['mean_p'])} | {_f(b['freq'])} |")
    return "\n".join(lines)


def render(data):
    m = data["meta"]
    t = data["trades"]
    lines = []
    w = lines.append
    w("# Backtest and evaluation report")
    w("")
    w("_Generated by `research/backtest_report.py`. Do not edit by hand; rerun the script._")
    w("")
    w("## Sample and split")
    w("")
    w(
        "- Source logs: `trades_both.jsonl`, `trades_conviction.jsonl` (two runs over overlapping 5-minute windows, kept as separate sources)."
    )
    w(
        f"- Windows in sources: {m.get('windows_total')}; excluded (last observation more than "
        f"{m.get('label_max_gap_s')} s before close): {m.get('windows_excluded_gap')}."
    )
    w(
        f"- Sample rows: {data['rows']} (1 in {m.get('stride')} ticks plus every would-trade tick; "
        f"chronological order; anonymized)."
    )
    w(
        f"- Chronological split by window end: train {data['windows_train']} windows / {data['states_train']} states; "
        f"**test {data['windows_test']} windows / {data['states_test']} states**."
    )
    w(f"- Test states with a directional call (strongest side UP or DOWN): {data['test_directional']}.")
    w(f"- Test-set decisions (first would-trade tick per source and window): {data['bets']}.")
    w("")
    w(
        "**Sample size is small.** The test set covers a few dozen windows. Ticks inside one window are "
        "strongly correlated, so the effective sample is closer to the number of windows than the number of states. "
        "Confidence intervals below are therefore wide, and no result here establishes an edge."
    )
    w("")
    w(
        "**Outcome labels are a proxy.** The logs contain no settlement result for most windows "
        "(resolution records are early exits with `resolved_up = null`). UP is defined as last observed BTC price "
        "above the window open price, which is close to but not identical to the contract settlement rule. "
        "PnL below is therefore a settlement-style estimate on that proxy, not realized PnL. Recorded early-exit PnL is not used."
    )
    w("")
    w("## Probability metrics, full test set")
    w("")
    w(
        "Probabilities are P(Up). The market row is the normalized mid, `up / (up + down)`. "
        "Models B and C are identical on every logged row (see method notes), so their rows match. "
        "Hit rate counts states with p != 0.5 where the call (p > 0.5 means UP) matches the proxy outcome."
    )
    w("")
    w(_prob_md(data["full_table"]))
    w("")
    w("### Reliability, test set")
    w("")
    for name in PROB_NAMES:
        w(f"**{name}**")
        w("")
        w(_reliability_md(_test_probs(data["test"], name), data["full_ys"]))
        w("")
    w("## JEV comparison (same states, JEV-covered subset)")
    w("")
    w(
        f"JEV was queried on {data['jev_covered']} of {data['test_directional']} test states with a directional call "
        f"(`JEV_MAX_CALLS` cap; {data['jev_pending']} still pending). Every other column is re-scored on exactly these states."
    )
    w("")
    if data["sub_table"]:
        w(_prob_md(data["sub_table"]))
        w("")
        lat = data["jev_latency"]
        w(
            f"JEV latency over covered states (ms): p50 {_f(percentile(lat, 0.5), 1)}, p95 {_f(percentile(lat, 0.95), 1)}, "
            f"n {len(lat)}. Latency is the original call time stored in the cache."
        )
    else:
        w("No JEV cache entries for the test set. Run `python research/jev_replay.py` to populate it.")
    w("")
    w(
        "**What JEV measures.** JEV answers whether observed BTC movement corroborates the strongest Polymarket side "
        "(directional agreement), not the settlement probability. Its probability is mapped to P(Up) as `p` when the side is UP "
        "and `1 - p` when DOWN. This mapping is an assumption, so JEV's Brier and log loss are not directly comparable to the models "
        "as settlement forecasts. Sample size on the covered subset is small."
    )
    w("")
    w("## Trade metrics (test set, bot would-trade decisions)")
    w("")
    w(
        "Same decisions for every column. Realized PnL uses binary settlement on the proxy outcome, "
        "stake/price shares, minus the recorded `estimated_cost_usdc` fee. Model EV is the expected PnL of the same trade "
        "under that model's probability for the traded side."
    )
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Trades (n) | {t['n']} |")
    w(f"| Win rate (proxy) | {_f(t['hit_rate'])} ({t['hits']}/{t['n']}), 95% Wilson CI {_ci(*t['hit_ci'])} |")
    w(
        f"| Mean realized net PnL per trade (USDC) | {_f(t['mean_pnl'], 3)}, 95% cluster-bootstrap CI {_ci(*t['pnl_ci'], nd=3)} |"
    )
    w(f"| Total realized net PnL (USDC) | {_f(t['total_pnl'], 2)} |")
    w(f"| Max drawdown (USDC) | {_f(t['max_dd'], 2)} |")
    for name in PROB_NAMES:
        w(f"| Model EV per trade, {name} (USDC, net of fee) | {_f(t['ev'][name], 3)} |")
    if data["jev_trades"]:
        jt = data["jev_trades"]
        w(
            f"| JEV-covered trades (n) | {jt['n']}, realized mean PnL {_f(jt['mean_pnl'], 3)}, "
            f"JEV EV per trade {_f(jt['ev']['JEV'], 3)} |"
        )
    else:
        w("| JEV-covered trades (n) | 0 |")
    w("")
    w(
        "Trade sample is small and clustered by window; the bootstrap resamples windows, not trades. "
        "Market EV is roughly minus the fee and the book overround (up + down > 1), since the market probability has no edge against its own price."
    )
    w("")
    w("## Method notes")
    w("")
    w(
        "- Models A, B, C, P are the probabilities logged by the bot; nothing is refit here. "
        "In the source logs B and C are identical on every row (8,125 of 8,125 in `trades_both.jsonl`), so the report cannot separate them; "
        "the cause (for example Model C falling back to B, or a logging duplicate) is not visible in these files."
    )
    w(
        "- Logged probabilities are used as-is. The chronological split is used "
        "only to choose which states are scored. Model C's calibration artifact may have been fit on overlapping history, "
        "so train-period scores are not reported."
    )
    w(
        "- Tick and momentum features for JEV are reconstructed from logged candidate ticks (about 2.5 s cadence, "
        "12 s tick lookback, 30 s Polymarket lookback) and are approximations of the production sub-second feed."
    )
    w("- Brier, log loss, ECE and reliability are computed on P(Up) with 10 equal-width bins.")
    w("- Wilson intervals use z = 1.96. Trade bootstrap uses 2000 resamples of windows with a fixed seed.")
    w("")
    return "\n".join(lines)


def write_png(data):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect")
    for name in PROB_NAMES:
        rel, _ = reliability(_test_probs(data["test"], name), data["full_ys"])
        pts = [(b["mean_p"], b["freq"]) for b in rel if b["mean_p"] is not None]
        ax.plot([p for p, _ in pts], [f for _, f in pts], marker="o", label=name)
    ax.set_xlabel("Mean predicted P(Up)")
    ax.set_ylabel("Observed frequency (proxy)")
    ax.set_title("Reliability, test set")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(PNG_PATH, dpi=120)
    plt.close(fig)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-sample", metavar="SRC_DIR", help="rebuild the sample from raw logs in SRC_DIR")
    args = ap.parse_args(argv)
    if args.build_sample:
        meta = build_sample(args.build_sample)
        print(
            f"wrote {SAMPLE_PATH} rows={meta['rows']} windows={meta['windows_total']} "
            f"excluded={meta['windows_excluded_gap']}"
        )
        return 0
    meta, rows = load_sample()
    data = build_report(meta, rows)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(data), encoding="utf-8")
    png = write_png(data)
    print(f"wrote {REPORT_PATH}; png={'yes' if png else 'skipped (matplotlib missing)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
