#!/usr/bin/env python3
"""Replay the production JEV question over test-set decision states.

For each test-set state with a directional call (strongest side UP or DOWN),
calls strategy.jev.query_btc_agreement and appends the result to
research/data/jev_replay_cache.jsonl. Cached states are never re-queried.
backtest_report.py reads the cache, so the report needs no network.

Environment:
  TYPESAFE_API_KEY   API key. If unset, read from the macOS keychain item
                     service "typesafe-api", account "sekun". The key is kept in
                     memory only and is never printed or written to disk.
  JEV_MAX_CALLS      hard cap on network calls per run (default 400).

Cache lines contain only state_hash, probability and latency_ms. Failed calls
(probability None) are not cached, so a later run retries them.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_report as br  # noqa: E402  (also puts repo root on sys.path)
from strategy import jev  # noqa: E402

DEFAULT_MAX_CALLS = 400


def _api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if key:
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "typesafe-api", "-a", "sekun", "-w"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _pending(states, cache):
    """Test states not yet in the cache."""
    return [r for r in states if br.jev_state_hash(r) not in cache]


def main() -> int:
    key = _api_key()
    if not key:
        print("No TYPESAFE_API_KEY and no keychain item typesafe-api/sekun. No calls made.")
        return 1
    os.environ["TYPESAFE_API_KEY"] = key
    del key

    try:
        cap = int(os.environ.get("JEV_MAX_CALLS", DEFAULT_MAX_CALLS))
    except ValueError:
        cap = DEFAULT_MAX_CALLS

    _, rows = br.load_sample()
    _, test = br.split_chronological(rows)
    states = [r for r in test if br.is_directional(r)]

    cache_path = br.JEV_CACHE_PATH
    cache = br.load_jev_cache(cache_path)
    uncached = _pending(states, cache)
    pending = uncached
    if cap <= 0:
        pending = []
    elif len(pending) > cap:
        step = math.ceil(len(pending) / cap)
        pending = pending[::step][:cap]

    calls = failures = 0
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "a", encoding="utf-8", newline="\n") as f:
        for r in pending:
            h = br.jev_state_hash(r)
            # query_btc_agreement has a 15 s in-process cache; clear it so each
            # replayed state is a real call and the latency is real.
            jev._cache.clear()
            t0 = time.perf_counter()
            prob = jev.query_btc_agreement(
                strongest_side=r["side"],
                seconds_remaining=r["s"],
                tick_move_pct=r["jev"]["tm"],
                directional_consistency=r["jev"]["dc"],
                acceleration_pct=r["jev"]["ac"],
                up_price_momentum=r["jev"]["um"],
                down_price_momentum=r["jev"]["dm"],
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            calls += 1
            if prob is None:
                failures += 1
                continue
            rec = {"state_hash": h, "probability": round(prob, 6),
                   "latency_ms": round(latency_ms, 1)}
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()

    cache = br.load_jev_cache(cache_path)
    covered = sum(1 for r in states if br.jev_state_hash(r) in cache)
    print(f"directional test states={len(states)} cached_before={len(states) - len(uncached)} "
          f"api_calls={calls} failures={failures} cap={cap} covered_now={covered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
