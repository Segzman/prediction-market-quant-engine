"""Build the JEV decision-state dataset from the local trade logs.

Reads two read-only JSONL logs (path from env JEV_SOURCE_DIR, default
~/Downloads/codex_plan), reconstructs per-window features from the
candidate series, and writes an anonymized states.jsonl. No order, wallet,
token or market ids are written.

Features the production prompt expects but the logs do not store are
reconstructed from the candidate series (see reconstruct_features). They
are approximations of the live bot's values, not copies of them.

Usage: python dataset.py
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "states.jsonl"
SOURCE_DIR = Path(os.environ.get("JEV_SOURCE_DIR", "~/Downloads/codex_plan")).expanduser()
SOURCE_FILES = ("trades_both.jsonl", "trades_conviction.jsonl")

LOOKBACK_S = 30.0  # momentum / acceleration lookback
CLOSE_MAX_SECONDS = 15  # last candidate must be this close to window end
GRID_SECONDS = 30  # keep one state per grid bucket per window
DEV_FRACTION = 0.70


def load_candidates(paths) -> tuple[list[dict], dict]:
    """Union of candidate records, deduped on (window_end_ts, ts)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    counts = {"read": 0, "duplicate": 0, "bad_json": 0}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    counts["bad_json"] += 1
                    continue
                if rec.get("type") != "candidate":
                    continue
                counts["read"] += 1
                key = (rec.get("window_end_ts"), round(float(rec["ts"]), 3))
                if key in seen:
                    counts["duplicate"] += 1
                    continue
                seen.add(key)
                out.append(rec)
    return out, counts


def _price_at(series: list[tuple[float, float]], t: float) -> float | None:
    """Latest value at or before t - LOOKBACK_S, or None if none exists."""
    target = t - LOOKBACK_S
    best = None
    for ts, val in series:
        if ts <= target:
            best = val
        else:
            break
    return best


def reconstruct_features(window: list[dict], rec: dict, prior: list[dict]) -> dict | None:
    """Features for one candidate, computed from the window's earlier candidates.

    tick_move_pct: percent move of BTC from window open to now.
    directional_consistency: share of BTC ticks since open whose sign matches
        the sign of the current move (0..1).
    acceleration_pct: tick_move_pct now minus tick_move_pct LOOKBACK_S ago.
    up/down_price_momentum: relative contract price change over LOOKBACK_S.
    Returns None when a lookback point does not exist yet (skipped state).
    """
    open_price = float(rec["open_price"])
    btc = float(rec["btc_price"])
    t = float(rec["ts"])
    if open_price <= 0:
        return None
    tick_move = (btc - open_price) / open_price * 100.0

    moves = [float(p["btc_price"]) - open_price for p in prior] + [btc - open_price]
    side = 1 if btc >= open_price else -1
    same = sum(1 for m in moves if (m >= 0) == (side > 0))
    consistency = same / len(moves)

    btc_series = [(float(p["ts"]), float(p["btc_price"])) for p in prior]
    btc_then = _price_at(btc_series, t)
    if btc_then is None:
        return None
    tick_then = (btc_then - open_price) / open_price * 100.0
    acceleration = tick_move - tick_then

    feats = {
        "tick_move_pct": round(tick_move, 6),
        "directional_consistency": round(consistency, 4),
        "acceleration_pct": round(acceleration, 6),
    }
    for side_name in ("up", "down"):
        key = f"{side_name}_price"
        if rec.get(key) is None:
            return None
        series = [(float(p["ts"]), float(p[key])) for p in prior if p.get(key) is not None]
        then = _price_at(series, t)
        now = float(rec[key])
        if then is None or then <= 0:
            return None
        feats[f"{side_name}_price_momentum"] = round((now - then) / then, 6)
    return feats


def build_states(candidates: list[dict], stats: dict) -> list[dict]:
    by_window: dict[int, list[dict]] = defaultdict(list)
    for rec in candidates:
        by_window[int(rec["window_end_ts"])].append(rec)

    states: list[dict] = []
    for wend in sorted(by_window):
        recs = sorted(by_window[wend], key=lambda r: float(r["ts"]))
        last = min(recs, key=lambda r: r["seconds_remaining"] if r["seconds_remaining"] is not None else 1e9)
        if last["seconds_remaining"] is None or last["seconds_remaining"] > CLOSE_MAX_SECONDS:
            stats["skip_window_no_close"] = stats.get("skip_window_no_close", 0) + 1
            continue
        close = float(last["btc_price"])
        open_price = float(recs[0]["open_price"])
        btc_up = close > open_price

        grid_seen: set[int] = set()
        for i, rec in enumerate(recs):
            secs = rec.get("seconds_remaining")
            if secs is None:
                stats["skip_missing_seconds"] = stats.get("skip_missing_seconds", 0) + 1
                continue
            bucket = int(secs) // GRID_SECONDS
            if bucket in grid_seen:
                continue
            side = rec.get("strongest_side")
            if side not in ("UP", "DOWN"):
                stats["skip_no_side"] = stats.get("skip_no_side", 0) + 1
                continue
            if rec.get("btc_confirmed") is None:
                stats["skip_missing_det_check"] = stats.get("skip_missing_det_check", 0) + 1
                continue
            feats = reconstruct_features(recs, rec, recs[:i])
            if feats is None:
                stats["skip_no_lookback"] = stats.get("skip_no_lookback", 0) + 1
                continue
            grid_seen.add(bucket)
            agree = btc_up if side == "UP" else (not btc_up)
            states.append(
                {
                    "window_end_ts": wend,
                    "ts": round(float(rec["ts"]), 3),
                    "seconds_remaining": int(secs),
                    "strongest_side": side,
                    "strongest_price": float(rec["up_price"] if side == "UP" else rec["down_price"]),
                    "up_price": float(rec["up_price"]),
                    "down_price": float(rec["down_price"]),
                    "det_agreement": bool(rec["btc_confirmed"]),
                    "btc_up": btc_up,
                    "agree": agree,
                    **feats,
                }
            )
    return states


def main() -> None:
    paths = [SOURCE_DIR / name for name in SOURCE_FILES]
    candidates, counts = load_candidates(paths)
    stats: dict = dict(counts)
    states = build_states(candidates, stats)

    windows = sorted({s["window_end_ts"] for s in states})
    cut = windows[int(len(windows) * DEV_FRACTION)] if windows else 0
    for s in states:
        s["split"] = "dev" if s["window_end_ts"] < cut else "holdout"
    for i, s in enumerate(states):
        s["id"] = i

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for s in states:
            fh.write(json.dumps(s, sort_keys=True) + "\n")

    n_dev = sum(1 for s in states if s["split"] == "dev")
    print(
        json.dumps(
            {
                "stats": stats,
                "windows": len(windows),
                "states": len(states),
                "dev": n_dev,
                "holdout": len(states) - n_dev,
                "dev_windows": sum(1 for w in windows if w < cut),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
