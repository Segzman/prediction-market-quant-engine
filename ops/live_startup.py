from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from persistence.ledger import Ledger


@dataclass
class LivePreflightResult:
    balance_usdc: float
    allowance_usdc: float
    available_usdc: float
    effective_bankroll_usdc: float
    open_order_count: int


@dataclass
class LiveReconciliationResult:
    resolved_count: int = 0
    cancelled_count: int = 0
    fill_count: int = 0
    unresolved_count: int = 0
    untracked_trade_count: int = 0


@dataclass
class IntentReconciliationResult:
    reconciled_filled: int = 0
    cancelled: int = 0
    aborted: int = 0
    still_pending: int = 0


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_trade_order_ids(payload: object, *, nested: bool = True) -> set[str]:
    direct_keys = {
        "order_id",
        "orderid",
        "maker_order_id",
        "makerorderid",
        "taker_order_id",
        "takerorderid",
    }
    ids: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = key.lower()
            if normalized in direct_keys and value:
                ids.add(str(value))
                continue
            if not nested and normalized in {"maker_orders", "orders"}:
                continue
            if nested:
                ids.update(_extract_trade_order_ids(value, nested=nested))
        return ids
    if isinstance(payload, list):
        for item in payload:
            ids.update(_extract_trade_order_ids(item, nested=nested))
    return ids


def _summarize_trade_matches(matches: list[dict]) -> tuple[float, float]:
    total_size = 0.0
    weighted_notional = 0.0
    for match in matches:
        size = (
            _coerce_float(match.get("size"))
            or _coerce_float(match.get("amount"))
            or _coerce_float(match.get("filled_size"))
            or 0.0
        )
        price = (
            _coerce_float(match.get("price"))
            or _coerce_float(match.get("avg_price"))
            or _coerce_float(match.get("maker_price"))
            or 0.0
        )
        if size > 0 and price > 0:
            total_size += size
            weighted_notional += size * price
    avg_price = (weighted_notional / total_size) if total_size > 0 else 0.0
    return total_size, avg_price


def perform_live_preflight(
    client,
    requested_bankroll: float,
    *,
    live_startup_cancel_all: bool,
    live_require_empty_open_orders: bool,
    live_min_available_usdc: float,
    live_balance_buffer_usdc: float,
    min_bet_usdc: float,
) -> LivePreflightResult:
    snapshot = client.get_account_snapshot()
    open_order_count = len(snapshot.open_orders)
    if open_order_count:
        if live_startup_cancel_all:
            client.cancel_all_orders()
            snapshot = client.get_account_snapshot()
            open_order_count = len(snapshot.open_orders)
        if live_require_empty_open_orders and open_order_count:
            raise SystemExit(
                f"Live preflight failed: {open_order_count} open orders found. "
                "Clear them manually or set LIVE_STARTUP_CANCEL_ALL=true"
            )

    if snapshot.available_to_trade_usdc < live_min_available_usdc:
        raise SystemExit(
            "Live preflight failed: available USDC "
            f"${snapshot.available_to_trade_usdc:.2f} is below "
            f"LIVE_MIN_AVAILABLE_USDC=${live_min_available_usdc:.2f}"
        )

    effective_bankroll = min(
        requested_bankroll,
        max(0.0, snapshot.available_to_trade_usdc - live_balance_buffer_usdc),
    )
    if effective_bankroll < min_bet_usdc:
        raise SystemExit(
            "Live preflight failed: effective bankroll "
            f"${effective_bankroll:.2f} is below MIN_BET_USDC=${min_bet_usdc:.2f}"
        )

    return LivePreflightResult(
        balance_usdc=snapshot.balance_usdc,
        allowance_usdc=snapshot.allowance_usdc,
        available_usdc=snapshot.available_to_trade_usdc,
        effective_bankroll_usdc=effective_bankroll,
        open_order_count=open_order_count,
    )


def reconcile_live_ledger(
    client,
    ledger: Ledger,
    *,
    live_recon_lookback_seconds: int,
    market_slug_extractor: Callable[[str], str],
) -> LiveReconciliationResult:
    result = LiveReconciliationResult()
    now = int(time.time())
    recent_live_trades: list[dict] = []
    live_trade_loader = getattr(client, "get_trades", None)
    if callable(live_trade_loader) and live_recon_lookback_seconds > 0:
        with contextlib.suppress(Exception):
            recent_live_trades = live_trade_loader(after=now - live_recon_lookback_seconds)

    for trade in ledger.get_unresolved_trades():
        order_id = trade.get("order_id")
        if not order_id or ledger.has_terminal_record(order_id):
            continue

        matching_live_trades = [
            item for item in recent_live_trades if order_id in _extract_trade_order_ids(item, nested=False)
        ]
        if matching_live_trades and not ledger.has_fill_record(order_id):
            filled_size, avg_price = _summarize_trade_matches(matching_live_trades)
            ledger.record_fill(
                order_id,
                int(trade["window_end_ts"]),
                "FILLED",
                filled_size=filled_size,
                avg_price=avg_price or float(trade.get("price", 0.0) or 0.0),
                details={
                    "startup_reconciliation": True,
                    "trade_match_count": len(matching_live_trades),
                },
            )
            result.fill_count += 1

        market_slug = trade.get("market_slug") or market_slug_extractor(trade.get("market_url", ""))
        if now >= int(trade.get("window_end_ts", 0)) and market_slug:
            resolution = client.get_market_resolution(market_slug)
            if resolution is not None:
                feature_snapshot = ((trade.get("feature_snapshot") or {}).copy())
                if feature_snapshot:
                    feature_snapshot = {
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
                ledger.record_resolution(
                    order_id,
                    int(trade["window_end_ts"]),
                    bool(resolution["resolved_up"]),
                    str(trade["direction"]),
                    float(trade["amount_usdc"]),
                    float(trade["price"]),
                    final_price=resolution.get("final_price"),
                    price_to_beat=resolution.get("price_to_beat"),
                    closed_time=resolution.get("closed_time"),
                    uma_resolution_status=resolution.get("uma_resolution_status"),
                    feature_snapshot=feature_snapshot or None,
                )
                result.resolved_count += 1
                continue

        try:
            order = client.get_order(order_id)
            status = str(order.get("status", "")).upper()
            if status == "FILLED" and not ledger.has_fill_record(order_id):
                ledger.record_fill(
                    order_id,
                    int(trade["window_end_ts"]),
                    "FILLED",
                    filled_size=float(order.get("filled_size", 0.0) or 0.0),
                    avg_price=float(
                        order.get("avg_price", order.get("price", trade.get("price", 0.0))) or 0.0
                    ),
                    details={"startup_reconciliation": True, "source": "order_status"},
                )
                result.fill_count += 1
            if status in {"CANCELLED", "CANCELED", "FAILED"}:
                ledger.record_cancellation(
                    order_id,
                    int(trade["window_end_ts"]),
                    status=status or "CANCELLED",
                    details={"startup_reconciliation": True},
                )
                result.cancelled_count += 1
                continue
        except Exception:
            pass

        result.unresolved_count += 1

    if recent_live_trades:
        live_order_ids = {
            order_id
            for trade in recent_live_trades
            for order_id in _extract_trade_order_ids(trade, nested=False)
            if order_id
        }
        unmanaged_order_ids = live_order_ids - ledger.get_order_ids()
        result.untracked_trade_count = len(unmanaged_order_ids)

    return result


def _open_order_id(order: dict) -> str | None:
    for key in ("order_id", "orderID", "id", "orderid"):
        value = order.get(key)
        if value:
            return str(value)
    return None


def _adopt_filled_intent(
    ledger: Ledger,
    *,
    client_order_id: str,
    details: dict,
    window_end_ts: int,
    order_id: str,
    filled_size: float,
    avg_price: float,
) -> None:
    """Record a held ambiguous order that we discovered actually filled.

    Writes the submission/fill/trade trio so the position becomes a managed,
    monitorable, resolvable trade (it lands in ``get_unresolved_trades``)
    instead of an untracked phantom fill that only surfaces at next startup.
    """
    price = avg_price or _coerce_float(details.get("price")) or 0.0
    amount_usdc = _coerce_float(details.get("amount_usdc")) or 0.0
    direction = str(details.get("direction", ""))
    role = str(details.get("role", "PRIMARY"))
    track = str(details.get("track", ""))
    model = str(details.get("model") or track)
    common = {"inline_reconciliation": True, "role": role, "track": track}
    ledger.record_submission(
        order_id,
        window_end_ts,
        direction=direction,
        amount_usdc=amount_usdc,
        submitted_price=price,
        market_price=price,
        model=model,
        dry_run=False,
        role=role,
        track=track,
        client_order_id=client_order_id,
        details=common,
    )
    ledger.record_fill(
        order_id,
        window_end_ts,
        "FILLED",
        filled_size=filled_size,
        avg_price=price,
        details=common,
    )
    ledger.record_trade(
        window_end_ts=window_end_ts,
        direction=direction,
        amount_usdc=amount_usdc,
        price=price,
        edge=0.0,
        ev=0.0,
        model=model,
        order_id=order_id,
        dry_run=False,
        market_url=str(details.get("market_url", "")),
        market_slug=str(details.get("market_slug", "")),
        asset=str(details.get("asset", "")),
        open_price=_coerce_float(details.get("open_price")),
        role=role,
        track=track,
        execution_snapshot={
            "inline_reconciliation": True,
            "token_id": details.get("token_id"),
        },
    )


def reconcile_open_order_intents(
    client,
    ledger: Ledger,
    *,
    now: int,
    lookback_seconds: int,
    abort_grace_seconds: int,
) -> IntentReconciliationResult:
    """Resolve held (ambiguous) order intents against live account state.

    An ambiguous submission leaves an ``IN_FLIGHT`` intent with no submission
    record: the order may have reached the exchange but we never confirmed an
    order_id. This sweep closes that gap *mid-session* instead of waiting for
    next startup:

    * the order landed and filled  -> adopt it as a managed trade
    * the order is resting unfilled -> cancel the unconfirmed stray, free the id
    * nothing live matches (past grace) -> abort the intent, free the id
    * nothing live matches (within grace) -> leave it held for the next pass
    """
    result = IntentReconciliationResult()
    open_intents = ledger.get_open_order_intents()
    if not open_intents:
        return result

    # An IN_FLIGHT intent that already has a submission carrying its
    # client_order_id is a normal *successful* order, not an ambiguous one.
    submitted_cids = {
        record.get("client_order_id")
        for record in ledger.get_all_submissions()
        if record.get("client_order_id")
    }
    tracked_order_ids = set(ledger.get_order_ids())

    recent_trades: list[dict] = []
    trade_loader = getattr(client, "get_trades", None)
    if callable(trade_loader) and lookback_seconds > 0:
        with contextlib.suppress(Exception):
            recent_trades = trade_loader(after=now - lookback_seconds) or []
    open_orders: list[dict] = []
    open_loader = getattr(client, "get_open_orders", None)
    if callable(open_loader):
        with contextlib.suppress(Exception):
            open_orders = open_loader() or []

    for intent in open_intents:
        client_order_id = intent.get("client_order_id")
        if not client_order_id or client_order_id in submitted_cids:
            continue
        details = intent.get("details") or {}
        token_id = details.get("token_id")
        window_end_ts = int(intent.get("window_end_ts") or 0)

        matched_trades = [
            trade
            for trade in recent_trades
            if token_id
            and str(trade.get("asset_id")) == str(token_id)
            and not (_extract_trade_order_ids(trade, nested=False) & tracked_order_ids)
        ]
        if matched_trades:
            order_ids = sorted(
                oid
                for trade in matched_trades
                for oid in _extract_trade_order_ids(trade, nested=False)
            )
            order_id = order_ids[0] if order_ids else client_order_id
            filled_size, avg_price = _summarize_trade_matches(matched_trades)
            _adopt_filled_intent(
                ledger,
                client_order_id=client_order_id,
                details=details,
                window_end_ts=window_end_ts,
                order_id=order_id,
                filled_size=filled_size,
                avg_price=avg_price,
            )
            ledger.record_order_intent(
                client_order_id,
                window_end_ts=window_end_ts,
                status="RECONCILED",
                details={"inline_reconciliation": "FILLED", "order_id": order_id},
            )
            tracked_order_ids.add(order_id)
            result.reconciled_filled += 1
            continue

        matched_orders = [
            order
            for order in open_orders
            if token_id
            and str(order.get("asset_id")) == str(token_id)
            and _open_order_id(order)
            and _open_order_id(order) not in tracked_order_ids
        ]
        if matched_orders:
            order_id = _open_order_id(matched_orders[0])
            with contextlib.suppress(Exception):
                client.cancel_order(order_id)
            ledger.record_cancellation(
                order_id,
                window_end_ts,
                status="CANCELLED",
                details={"inline_reconciliation": True, "reason": "ambiguous_unconfirmed"},
            )
            ledger.record_order_intent(
                client_order_id,
                window_end_ts=window_end_ts,
                status="ABORTED",
                details={"inline_reconciliation": "CANCELLED", "order_id": order_id},
            )
            result.cancelled += 1
            continue

        if now >= window_end_ts + abort_grace_seconds:
            ledger.record_order_intent(
                client_order_id,
                window_end_ts=window_end_ts,
                status="ABORTED",
                details={"inline_reconciliation": "NOT_FOUND"},
            )
            result.aborted += 1
        else:
            result.still_pending += 1

    return result
