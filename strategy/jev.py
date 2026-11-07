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


def _cache_get(key: tuple) -> float | None | object:
    """Return cached probability, None on miss/expiry. _MISS = cached miss."""
    entry = _cache.get(key)
    if entry is None:
        return None
    value, ts = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: tuple, value: float | None) -> None:
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (value, time.time())


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
) -> float | None:
    """P(BTC movement corroborates Polymarket's strongest side). None = unknown."""
    if strongest_side not in {"UP", "DOWN"}:
        return None
    key = os.getenv("TYPESAFE_API_KEY", "")
    if not key:
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
    cached = _cache_get(cache_key)
    if cached is not None:
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
    except Exception:
        prob = None
    _cache_put(cache_key, prob)
    return prob


def resolve_btc_confirmation(
    model_context,
    *,
    seconds_remaining: int,
    market_price: float,
) -> bool | None:
    """Jev confirmation override. None = keep deterministic check.

    Only spends an API call where it can change the outcome: expensive
    (high-price) entries that require confirmation, or cases where the
    deterministic check already disagrees.
    """
    if not JEV_NOUL_ENABLED or not _api_key():
        return None
    if model_context is None:
        return None
    deterministic = model_context.btc_poly_agreement > 0
    if (
        market_price < LATE_CONVICTION_HIGH_PRICE_THRESHOLD
        and deterministic
    ):
        return None
    prob = query_btc_agreement(
        strongest_side=model_context.strongest_side,
        seconds_remaining=seconds_remaining,
        tick_move_pct=model_context.tick_move_pct,
        directional_consistency=model_context.directional_consistency,
        acceleration_pct=model_context.acceleration_pct,
        up_price_momentum=model_context.up_price_momentum,
        down_price_momentum=model_context.down_price_momentum,
    )
    if prob is None:
        return None
    return prob >= JEV_NOUL_MIN_PROBABILITY
