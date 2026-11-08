"""Jev (TypeSafe System One) pilot judgments for the signal path.

Sync-only (strategy layer does no async I/O). Every public helper is
fail-closed: any missing key, timeout, transport error, or malformed
answer returns None and the caller keeps the deterministic behavior.

Costs are bounded by only calling the API where the deterministic check
is contested or the entry is expensive (see resolve_btc_confirmation),
plus a short TTL cache keyed on bucketed inputs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from config import (
    JEV_API_TIMEOUT_SECONDS,
    JEV_COST_PER_CALL_USD,
    JEV_LOG_FILE,
    JEV_NOUL_ENABLED,
    JEV_NOUL_MIN_PROBABILITY,
    LATE_CONVICTION_HIGH_PRICE_THRESHOLD,
)

JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

_CACHE_TTL_SECONDS = 15.0
_cache: dict[tuple, tuple[float | None, float]] = {}


def _api_key() -> str:
    return os.getenv("TYPESAFE_API_KEY", "")


def _cache_lookup(key: tuple) -> tuple[bool, float | None]:
    """Return (hit, value). Expired entries count as misses."""
    entry = _cache.get(key)
    if entry is None:
        return False, None
    value, ts = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return False, None
    return True, value


def _cache_put(key: tuple, value: float | None) -> None:
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (value, time.time())


def _features(model_context) -> dict:
    if model_context is None:
        return {}
    return {
        "tick_move_pct": model_context.tick_move_pct,
        "directional_consistency": model_context.directional_consistency,
        "acceleration_pct": model_context.acceleration_pct,
        "up_price_momentum": model_context.up_price_momentum,
        "down_price_momentum": model_context.down_price_momentum,
    }


def _decision_record(
    *,
    model_context,
    seconds_remaining: int,
    market_price: float,
    window_id: str | None,
    window_end_ts: int | None,
    deterministic: bool | None,
    api_call: bool,
    skip_reason: str | None,
    cache_hit: bool,
    latency_ms: float | None,
    probability: float | None,
    error_class: str | None,
) -> dict:
    jev_decision = (
        probability >= JEV_NOUL_MIN_PROBABILITY if probability is not None else None
    )
    final_decision = jev_decision if jev_decision is not None else deterministic
    return {
        "ts": time.time(),
        "window_id": window_id,
        "window_end_ts": window_end_ts,
        "seconds_remaining": seconds_remaining,
        "market_price": market_price,
        "strongest_side": (
            model_context.strongest_side if model_context is not None else None
        ),
        "features": _features(model_context),
        "api_call": api_call,
        "skip_reason": skip_reason,
        "cache_hit": cache_hit,
        "latency_ms": latency_ms,
        "probability": probability,
        "error_class": error_class,
        "deterministic_decision": deterministic,
        "jev_decision": jev_decision,
        "final_decision": final_decision,
        "cost_per_call_usd": JEV_COST_PER_CALL_USD,
        "estimated_cost_usd": JEV_COST_PER_CALL_USD if api_call else 0.0,
    }


def _log_decision(record: dict) -> None:
    """Append one JSON line. Never raises; logging must not affect decisions."""
    try:
        if not JEV_LOG_FILE:
            return
        line = json.dumps(record, separators=(",", ":"), default=str)
        with open(JEV_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _extract_probability(answer: dict) -> float | None:
    for field in ("noul", "probability", "value"):
        raw = answer.get(field)
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            return min(1.0, max(0.0, float(raw)))
    return None


def query_btc_agreement(
    *,
    strongest_side: str,
    seconds_remaining: int,
    tick_move_pct: float,
    directional_consistency: float,
    acceleration_pct: float,
    up_price_momentum: float,
    down_price_momentum: float,
    timeout: float | None = None,
    meta: dict | None = None,
) -> float | None:
    """P(BTC movement corroborates Polymarket's strongest side). None = unknown."""
    if meta is None:
        meta = {}
    meta.setdefault("api_call", False)
    meta.setdefault("cache_hit", False)
    meta.setdefault("latency_ms", None)
    meta.setdefault("error_class", None)
    if strongest_side not in {"UP", "DOWN"}:
        meta["skip_reason"] = "invalid_side"
        return None
    key = os.getenv("TYPESAFE_API_KEY", "")
    if not key:
        meta["skip_reason"] = "no_key"
        return None
    cache_key = (
        strongest_side,
        max(0, seconds_remaining // 5),
        round(tick_move_pct, 5),
        round(directional_consistency, 2),
        round(acceleration_pct, 5),
        round(up_price_momentum, 5),
        round(down_price_momentum, 5),
    )
    hit, cached = _cache_lookup(cache_key)
    if hit and cached is not None:
        meta["cache_hit"] = True
        return cached

    direction_word = "positive" if strongest_side == "UP" else "negative"
    state = {
        "seconds_remaining": seconds_remaining,
        "strongest_side": strongest_side,
        "tick_move_pct": tick_move_pct,
        "directional_consistency": directional_consistency,
        "acceleration_pct": acceleration_pct,
        "up_price_momentum": up_price_momentum,
        "down_price_momentum": down_price_momentum,
    }
    payload = {
        "state": state,
        "model": JEV_MODEL,
        "questions": {
            "btc_poly_agreement": {
                "type": "noul",
                "instructions": (
                    "Does observed BTC movement corroborate Polymarket's "
                    f"strongest side ({strongest_side})? {strongest_side} "
                    f"requires {direction_word} BTC movement. Weigh window "
                    "direction against recent tick direction, consistency "
                    "and acceleration, and whether contract movement "
                    "supports that side. Judge directional agreement, not "
                    "settlement probability."
                ),
            }
        },
    }
    meta["api_call"] = True
    started = time.perf_counter()
    try:
        req = urllib.request.Request(
            JEV_API_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(
            req, timeout=timeout or JEV_API_TIMEOUT_SECONDS
        ) as resp:
            body = json.load(resp)
        answer = (body.get("answers") or {}).get("btc_poly_agreement") or {}
        prob = _extract_probability(answer)
    except Exception as exc:
        meta["error_class"] = type(exc).__name__
        prob = None
    finally:
        meta["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    _cache_put(cache_key, prob)
    return prob


def resolve_btc_confirmation(
    model_context,
    *,
    seconds_remaining: int,
    market_price: float,
    window_id: str | None = None,
    window_end_ts: int | None = None,
) -> bool | None:
    """Jev confirmation override. None = keep deterministic check.

    Only spends an API call where it can change the outcome: expensive
    (high-price) entries that require confirmation, or cases where the
    deterministic check already disagrees.
    """
    deterministic = (
        model_context.btc_poly_agreement > 0 if model_context is not None else None
    )
    base = {
        "model_context": model_context,
        "seconds_remaining": seconds_remaining,
        "market_price": market_price,
        "window_id": window_id,
        "window_end_ts": window_end_ts,
        "deterministic": deterministic,
    }

    def log(**overrides) -> None:
        record = _decision_record(**base, **overrides)
        try:
            _log_decision(record)
        except Exception:
            pass

    if not JEV_NOUL_ENABLED:
        log(
            api_call=False,
            skip_reason="disabled",
            cache_hit=False,
            latency_ms=None,
            probability=None,
            error_class=None,
        )
        return None
    if not _api_key():
        log(
            api_call=False,
            skip_reason="no_key",
            cache_hit=False,
            latency_ms=None,
            probability=None,
            error_class=None,
        )
        return None
    if model_context is None:
        log(
            api_call=False,
            skip_reason="no_context",
            cache_hit=False,
            latency_ms=None,
            probability=None,
            error_class=None,
        )
        return None
    if market_price < LATE_CONVICTION_HIGH_PRICE_THRESHOLD and deterministic:
        log(
            api_call=False,
            skip_reason="cheap_and_agreed",
            cache_hit=False,
            latency_ms=None,
            probability=None,
            error_class=None,
        )
        return None

    meta: dict = {
        "api_call": False,
        "cache_hit": False,
        "latency_ms": None,
        "error_class": None,
    }
    prob = query_btc_agreement(
        strongest_side=model_context.strongest_side,
        seconds_remaining=seconds_remaining,
        tick_move_pct=model_context.tick_move_pct,
        directional_consistency=model_context.directional_consistency,
        acceleration_pct=model_context.acceleration_pct,
        up_price_momentum=model_context.up_price_momentum,
        down_price_momentum=model_context.down_price_momentum,
        meta=meta,
    )
    log(
        api_call=bool(meta.get("api_call")),
        skip_reason=meta.get("skip_reason"),
        cache_hit=bool(meta.get("cache_hit")),
        latency_ms=meta.get("latency_ms"),
        probability=prob,
        error_class=meta.get("error_class"),
    )
    if prob is None:
        return None
    return prob >= JEV_NOUL_MIN_PROBABILITY
