"""Question-framing harness for the JEV judgment API.

Runs one framing (framings/<name>.json) over a split of data/states.jsonl,
scores it against the realized BTC direction, and writes results/<split>/<name>.json.

Guards:
- on-disk response cache keyed by the exact request payload (+ repeat index),
  so reruns cost nothing; only probabilities and latencies are stored;
- hard call budget across the whole experiment (env JEV_MAX_CALLS, default
  1500), tracked in cache/budget.json; cache hits are free;
- at most 4 concurrent calls, 10 s timeout per call.

Subcommands:
  run      <split> <framing.json>...   score framings on dev or holdout
  variance <framing.json>              re-ask identical dev states, measure spread
  table                                print a markdown summary of results/

The API key is read at runtime from TYPESAFE_API_KEY or the macOS keychain
item (service typesafe-api, account sekun). It is never printed or stored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATES = HERE / "data" / "states.jsonl"
FRAMINGS = HERE / "framings"
RESULTS = HERE / "results"
CACHE_DIR = HERE / "cache"
CACHE_FILE = CACHE_DIR / "responses.jsonl"
BUDGET_FILE = CACHE_DIR / "budget.json"

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
QUESTION = "btc_poly_agreement"
THRESHOLD = 0.60
TIMEOUT_S = 10.0
CONCURRENCY = 4
DEFAULT_MAX_CALLS = 1500
DEV_SAMPLE = 60
LOG_EPS = 1e-3
BINS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


# ---------- pure helpers (unit tested) ----------

def render_instructions(template: str, side: str) -> str:
    return template.format(
        strongest_side=side,
        direction_word="positive" if side == "UP" else "negative",
    )


def request_payload(framing: dict, state: dict) -> dict:
    """Same shape as strategy/jev.py: state + one noul question."""
    sub = {k: state[k] for k in framing["state_fields"]}
    return {
        "state": sub,
        "model": MODEL,
        "questions": {QUESTION: {
            "type": framing.get("question_type", "noul"),
            "instructions": render_instructions(framing["instructions"], state["strongest_side"]),
        }},
    }


def cache_key(payload: dict, repeat: int = 0) -> str:
    blob = json.dumps({"payload": payload, "repeat": repeat}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def to_agreement(raw: float, side: str, answer_mode: str) -> float:
    """Map the API number to P(strongest side is corroborated)."""
    raw = min(1.0, max(0.0, float(raw)))
    if answer_mode == "p_up":
        return raw if side == "UP" else 1.0 - raw
    return raw


def extract_probability(answer: dict) -> float | None:
    """Same fields and clamping as strategy/jev.py::_extract_probability."""
    for field in ("noul", "probability", "value"):
        raw = answer.get(field)
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            return min(1.0, max(0.0, float(raw)))
    return None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(q / 100.0 * len(s)) - 1))
    return s[idx]


def _logloss(p: float, y: bool) -> float:
    p = min(1 - LOG_EPS, max(LOG_EPS, p))
    return -math.log(p) if y else -math.log(1 - p)


def metrics(rows: list[dict]) -> dict:
    """rows: dicts with p (P agree), agree (bool truth), det (bool deterministic check),
    lat_ms (float or None), src ('api'|'cache'|'error'|'budget')."""
    scored = [r for r in rows if r.get("p") is not None]
    n = len(scored)
    attempted = sum(1 for r in rows if r["src"] in ("api", "error"))
    errors = sum(1 for r in rows if r["src"] == "error")
    out: dict = {
        "n_states": len(rows),
        "n_scored": n,
        "api_calls": sum(1 for r in rows if r["src"] in ("api", "error")),
        "cache_hits": sum(1 for r in rows if r["src"] == "cache"),
        "budget_skipped": sum(1 for r in rows if r["src"] == "budget"),
        "error_rate": round(errors / attempted, 4) if attempted else None,
    }
    if n == 0:
        return out
    ps = [r["p"] for r in scored]
    ys = [bool(r["agree"]) for r in scored]
    dets = [bool(r["det"]) for r in scored]
    out["brier"] = round(sum((p - float(y)) ** 2 for p, y in zip(ps, ys)) / n, 4)
    out["log_loss"] = round(sum(_logloss(p, y) for p, y in zip(ps, ys)) / n, 4)
    out["acc_at_0.5"] = round(sum((p >= 0.5) == y for p, y in zip(ps, ys)) / n, 4)
    out["acc_at_0.60"] = round(sum((p >= THRESHOLD) == y for p, y in zip(ps, ys)) / n, 4)
    confirm = [y for p, y in zip(ps, ys) if p >= THRESHOLD]
    out["confirm_rate_at_0.60"] = round(len(confirm) / n, 4)
    out["precision_at_0.60"] = round(sum(confirm) / len(confirm), 4) if confirm else None
    out["det_agreement"] = round(sum((p >= 0.5) == d for p, d in zip(ps, dets)) / n, 4)
    cal = []
    for lo, hi in zip(BINS[:-1], BINS[1:]):
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        cal.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": len(idx),
            "mean_p": round(sum(ps[i] for i in idx) / len(idx), 4) if idx else None,
            "observed": round(sum(ys[i] for i in idx) / len(idx), 4) if idx else None,
        })
    out["calibration"] = cal
    lats = [r["lat_ms"] for r in rows if r.get("lat_ms") is not None]
    out["latency_p50_ms"] = round(percentile(lats, 50), 1) if lats else None
    out["latency_p95_ms"] = round(percentile(lats, 95), 1) if lats else None
    return out


def deterministic_baseline(rows: list[dict]) -> dict:
    """The deterministic check as a 0/1 probability, scored the same way."""
    as_rows = [dict(r, p=1.0 if r["det"] else 0.0, src="api") for r in rows]
    m = metrics(as_rows)
    return {k: m[k] for k in ("n_scored", "brier", "log_loss", "acc_at_0.5", "acc_at_0.60")}


def sample_evenly(items: list, n: int | None) -> list:
    if n is None or n >= len(items):
        return list(items)
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)] if n > 1 else items[:1]


# ---------- I/O: key, cache, budget ----------

def get_api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if key:
        return key
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", "typesafe-api", "-a", "sekun", "-w"],
        capture_output=True, text=True,
    )
    key = proc.stdout.strip()
    if proc.returncode != 0 or not key:
        raise SystemExit("no API key: set TYPESAFE_API_KEY or keychain item typesafe-api")
    return key


class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        self.lock = threading.Lock()
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.data[rec["k"]] = {"p": rec["p"], "lat_ms": rec["lat_ms"]}

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, p: float, lat_ms: float) -> None:
        with self.lock:
            self.data[key] = {"p": p, "lat_ms": lat_ms}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps({"k": key, "p": p, "lat_ms": round(lat_ms, 1)}) + "\n")


class Budget:
    """Counts every attempted API call across all runs. Cache hits do not count."""

    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = limit
        self.lock = threading.Lock()
        self.used = json.loads(path.read_text())["used"] if path.exists() else 0

    def reserve(self) -> bool:
        with self.lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"used": self.used, "limit": self.limit}))
            return True


def call_api(payload: dict, key: str) -> tuple[float | None, float]:
    """Returns (raw probability or None, latency ms). Raises on transport error."""
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body = json.load(resp)
    lat = (time.perf_counter() - t0) * 1000.0
    answer = (body.get("answers") or {}).get(QUESTION) or {}
    return extract_probability(answer), lat


class Runner:
    def __init__(self, cache: Cache, budget: Budget, key_getter=get_api_key, caller=call_api):
        self.cache = cache
        self.budget = budget
        self.key_getter = key_getter
        self.caller = caller
        self._key: str | None = None

    def _key_value(self) -> str:
        if self._key is None:
            self._key = self.key_getter()
        return self._key

    def score_state(self, framing: dict, state: dict, repeat: int = 0) -> dict:
        payload = request_payload(framing, state)
        key = cache_key(payload, repeat)
        hit = self.cache.get(key)
        if hit is not None:
            raw, lat, src = hit["p"], hit["lat_ms"], "cache"
        elif not self.budget.reserve():
            return {"id": state["id"], "p": None, "lat_ms": None, "src": "budget"}
        else:
            try:
                raw, lat = self.caller(payload, self._key_value())
            except Exception:
                return {"id": state["id"], "p": None, "lat_ms": None, "src": "error"}
            if raw is None:
                return {"id": state["id"], "p": None, "lat_ms": round(lat, 1), "src": "error"}
            self.cache.put(key, raw, lat)
            src = "api"
        p = to_agreement(raw, state["strongest_side"], framing.get("answer_mode", "agreement"))
        return {"id": state["id"], "p": round(p, 6), "raw": raw, "lat_ms": round(lat, 1), "src": src}

    def run(self, framing: dict, states: list[dict], repeat: int = 0) -> list[dict]:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            return list(pool.map(lambda s: self.score_state(framing, s, repeat), states))


def load_states(split: str | None = None) -> list[dict]:
    with open(STATES, encoding="utf-8") as fh:
        states = [json.loads(line) for line in fh if line.strip()]
    return [s for s in states if split is None or s["split"] == split]


def load_framing(path: Path) -> dict:
    framing = json.loads(path.read_text())
    framing["_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return framing


def score_rows(rows: list[dict], states: list[dict]) -> list[dict]:
    by_id = {s["id"]: s for s in states}
    out = []
    for r in rows:
        s = by_id[r["id"]]
        out.append({**r, "agree": s["agree"], "det": s["det_agreement"]})
    return out


def make_runner(max_calls: int | None = None) -> Runner:
    limit = max_calls if max_calls is not None else int(os.environ.get("JEV_MAX_CALLS", DEFAULT_MAX_CALLS))
    return Runner(Cache(CACHE_FILE), Budget(BUDGET_FILE, limit))


def cmd_run(args) -> None:
    runner = make_runner()
    states = load_states(args.split)
    sample = sample_evenly(states, args.sample if args.sample else (DEV_SAMPLE if args.split == "dev" else None))
    for path in args.framings:
        framing = load_framing(Path(path))
        rows = runner.run(framing, sample)
        scored = score_rows(rows, sample)
        result = {
            "name": framing["name"],
            "framing_hash": framing["_hash"],
            "split": args.split,
            "n_states_split": len(states),
            "n_states_sampled": len(sample),
            "metrics": metrics(scored),
            "deterministic_baseline": deterministic_baseline(scored),
            "rows": [{"id": r["id"], "p": r["p"], "lat_ms": r["lat_ms"], "src": r["src"]} for r in rows],
        }
        out_dir = RESULTS / args.split
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{framing['name']}.json").write_text(json.dumps(result, indent=1) + "\n")
        m = result["metrics"]
        print(json.dumps({k: m.get(k) for k in ("name", "brier", "log_loss", "acc_at_0.60", "api_calls", "error_rate")}
                         | {"name": framing["name"]}))


def cmd_variance(args) -> None:
    runner = make_runner()
    framing = load_framing(Path(args.framing))
    states = sample_evenly(load_states("dev"), args.states)
    per_state = {s["id"]: [] for s in states}
    for rep in range(args.repeats):
        for r in runner.run(framing, states, repeat=rep):
            if r["p"] is not None:
                per_state[r["id"]].append(r["p"])
    spreads, flip50, flip60 = [], 0, 0
    for vals in per_state.values():
        if len(vals) < 2:
            continue
        mean = sum(vals) / len(vals)
        spreads.append(max(vals) - min(vals))
        flip50 += (min(vals) < 0.5 <= max(vals))
        flip60 += (min(vals) < THRESHOLD <= max(vals))
    summary = {
        "framing": framing["name"],
        "states": len(states),
        "repeats": args.repeats,
        "mean_range": round(sum(spreads) / len(spreads), 4) if spreads else None,
        "max_range": round(max(spreads), 4) if spreads else None,
        "states_flipping_at_0.5": flip50,
        "states_flipping_at_0.60": flip60,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "variance.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary))


def cmd_table(args) -> None:
    print("| split | framing | n | Brier | log loss | acc@0.5 | acc@0.60 | det agree | p50 ms | p95 ms | err | calls |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for split in ("dev", "holdout"):
        for path in sorted((RESULTS / split).glob("*.json")):
            r = json.loads(path.read_text())
            m = r["metrics"]
            print(f"| {split} | {r['name']} | {m['n_scored']} | {m.get('brier')} | {m.get('log_loss')} | "
                  f"{m.get('acc_at_0.5')} | {m.get('acc_at_0.60')} | {m.get('det_agreement')} | "
                  f"{m.get('latency_p50_ms')} | {m.get('latency_p95_ms')} | {m['error_rate']} | {m['api_calls']} |")
    base = None
    for path in sorted((RESULTS / "holdout").glob("*.json")):
        base = base or json.loads(path.read_text())["deterministic_baseline"]
    if base:
        print(f"\ndeterministic check (holdout sample): {base}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("split", choices=["dev", "holdout"])
    p.add_argument("framings", nargs="+")
    p.add_argument("--sample", type=int, default=None)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("variance")
    p.add_argument("framing")
    p.add_argument("--states", type=int, default=20)
    p.add_argument("--repeats", type=int, default=3)
    p.set_defaults(fn=cmd_variance)
    p = sub.add_parser("table")
    p.set_defaults(fn=cmd_table)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
