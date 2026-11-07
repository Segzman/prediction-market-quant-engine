from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Callable


def fetch_binance_agg_trade_price(
    asset: str,
    target_ts_ms: int,
    *,
    binance_rest_symbols: dict[str, str],
    fetch_binance_rest_json: Callable,
    logger: logging.Logger | None = None,
) -> float | None:
    symbol = binance_rest_symbols[asset]
    payload = fetch_binance_rest_json(
        f"/api/v3/aggTrades?symbol={symbol}&startTime={target_ts_ms}&limit=1",
        logger=logger,
    )
    if not payload:
        return None
    return float(payload[0]["p"])


def resolve_with_binance(
    asset: str,
    window_end_ts: int,
    *,
    fetch_binance_agg_trade_price_fn: Callable[[str, int], float | None],
) -> dict | None:
    start_price = fetch_binance_agg_trade_price_fn(asset, (window_end_ts - 300) * 1000)
    end_price = fetch_binance_agg_trade_price_fn(asset, window_end_ts * 1000)
    if start_price is None or end_price is None:
        return None
    return {
        "resolved_up": end_price > start_price,
        "closed": True,
        "closed_time": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(window_end_ts)),
        "final_price": end_price,
        "price_to_beat": start_price,
        "outcome_prices": [1.0, 0.0] if end_price > start_price else [0.0, 1.0],
        "uma_resolution_status": "BINANCE",
    }


def resolve_market_outcome(
    market_client,
    *,
    asset: str,
    market_slug: str,
    window_end_ts: int,
    prefer_binance: bool,
    resolve_with_binance_fn: Callable[[str, int], dict | None],
) -> dict | None:
    if prefer_binance:
        with contextlib.suppress(Exception):
            resolution = resolve_with_binance_fn(asset, window_end_ts)
            if resolution is not None:
                return resolution
    if not hasattr(market_client, "get_market_resolution"):
        return None
    return market_client.get_market_resolution(market_slug)


async def wait_for_market_resolution(
    market_client,
    asset: str,
    market_slug: str,
    window_end_ts: int,
    *,
    prefer_binance: bool,
    resolve_market_outcome_fn: Callable,
    timeout_seconds: int = 180,
    poll_interval: float = 5.0,
    logger: logging.Logger | None = None,
):
    if not market_slug:
        return None

    await asyncio.sleep(max(0, window_end_ts - int(time.time()) + 5))
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resolution = await asyncio.to_thread(
                resolve_market_outcome_fn,
                market_client,
                asset=asset,
                market_slug=market_slug,
                window_end_ts=window_end_ts,
                prefer_binance=prefer_binance,
            )
        except Exception as exc:
            if logger is not None:
                logger.warning("Resolution lookup failed for %s: %s", market_slug, exc)
            resolution = None

        if resolution is not None:
            return resolution
        await asyncio.sleep(poll_interval)

    return None


def sweep_unresolved_trades_once(
    track,
    market_client,
    *,
    target_asset: str,
    extract_market_slug_fn: Callable[[str], str],
    resolve_market_outcome_fn: Callable,
    is_demo_client_fn: Callable[[object], bool],
    now_ts: int | None = None,
    logger: logging.Logger | None = None,
) -> int:
    ledger = track.ledger
    now = int(time.time()) if now_ts is None else now_ts
    resolved_count = 0
    resolution_cache: dict[str, dict | None] = {}
    for trade in ledger.get_unresolved_trades():
        order_id = trade.get("order_id")
        market_slug = trade.get("market_slug") or extract_market_slug_fn(trade.get("market_url", ""))
        window_end_ts = int(trade.get("window_end_ts", 0) or 0)
        if not order_id or not market_slug or window_end_ts <= 0:
            continue
        if now < window_end_ts + 5:
            continue

        if market_slug not in resolution_cache:
            try:
                resolution_cache[market_slug] = resolve_market_outcome_fn(
                    market_client,
                    asset=str(trade.get("asset") or target_asset),
                    market_slug=market_slug,
                    window_end_ts=window_end_ts,
                    prefer_binance=not is_demo_client_fn(market_client),
                )
            except Exception as exc:
                if logger is not None:
                    logger.warning("Background resolution sweep failed for %s: %s", market_slug, exc)
                resolution_cache[market_slug] = None
        resolution = resolution_cache[market_slug]
        if resolution is None:
            continue

        feature_snapshot = (trade.get("feature_snapshot") or {}).copy()
        calibration_fields = {
            key: feature_snapshot[key]
            for key in (
                "move_pct_bin",
                "time_bin",
                "base_prob_bin",
                "directional_consistency_bin",
                "acceleration_bin",
                "model_c_bucket",
            )
            if key in feature_snapshot
        }
        if ledger.has_terminal_record(order_id):
            continue
        ledger.record_resolution(
            order_id,
            window_end_ts,
            bool(resolution["resolved_up"]),
            str(trade["direction"]),
            float(trade["amount_usdc"]),
            float(trade["price"]),
            final_price=resolution.get("final_price"),
            price_to_beat=resolution.get("price_to_beat"),
            closed_time=resolution.get("closed_time"),
            uma_resolution_status=resolution.get("uma_resolution_status"),
            feature_snapshot=calibration_fields or None,
        )
        track.state.record_resolution(order_id, bool(resolution["resolved_up"]))
        if track.model == str(trade.get("model")) and trade.get("role", "PRIMARY") != "HEDGE":
            won = (
                (str(trade["direction"]) == "UP" and bool(resolution["resolved_up"]))
                or (str(trade["direction"]) == "DOWN" and not bool(resolution["resolved_up"]))
            )
            track.stats.last_resolution = "WIN" if won else "LOSS"
            track.stats.note(f"Recovered {'WIN' if won else 'LOSS'} for {market_slug}")
        resolved_count += 1
        if logger is not None:
            logger.info("Recovered resolution for %s", market_slug)

    return resolved_count
