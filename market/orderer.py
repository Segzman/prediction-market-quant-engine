from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import uuid4

from config import (
    LATE_CONVICTION_SLIPPAGE_BPS,
    LIVE_ALLOW_NEG_RISK,
    LIVE_ORDER_SLIPPAGE_BPS,
    LIVE_ORDER_TYPE,
)
from state.types import Direction


class AmbiguousOrderError(RuntimeError):
    """Order submission failed where the order may or may not have reached the exchange.

    Raised for transport-level failures (timeout / dropped connection) around the
    network submit. The caller must NOT blindly retry, because a matching order
    may already be live; it should reconcile against account state instead.
    """


class OrderStatus(Enum):
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class OrderResult:
    order_id: Optional[str]
    status: OrderStatus
    filled_size: float = 0.0
    avg_price: float = 0.0
    submitted_price: float = 0.0
    fee_rate_bps: int = 0
    dry_run: bool = False


@dataclass
class BookSnapshot:
    ask_count: int
    bid_count: int
    best_ask: float | None
    best_bid: float | None
    best_ask_size: float = 0.0
    best_bid_size: float = 0.0
    total_ask_size: float = 0.0
    total_bid_size: float = 0.0

    def queue_imbalance(self) -> float:
        total = self.best_bid_size + self.best_ask_size
        if total <= 0:
            return 0.0
        return (self.best_bid_size - self.best_ask_size) / total

    def microprice(self) -> float | None:
        if (
            self.best_ask is None
            or self.best_bid is None
            or self.best_ask_size <= 0
            or self.best_bid_size <= 0
        ):
            return None
        total = self.best_bid_size + self.best_ask_size
        if total <= 0:
            return None
        return (
            (self.best_ask * self.best_bid_size) + (self.best_bid * self.best_ask_size)
        ) / total

    def depth_to_fill_usdc(self, reference_price: float | None = None) -> float:
        if self.best_ask is not None and self.total_ask_size > 0:
            return self.total_ask_size * self.best_ask
        if reference_price is not None and self.total_ask_size > 0:
            return self.total_ask_size * reference_price
        return 0.0


class Orderer:
    def __init__(self, client, dry_run: bool = True):
        self._client = client
        self.dry_run = dry_run
        self._logger = logging.getLogger(__name__)

    def place_bet(
        self,
        token_id: str,
        direction: Direction,
        usdc_amount: float,
        market_price: float,
        *,
        entry_regime: str = "MISPRICING",
        side: str = "BUY",
    ) -> OrderResult:
        price, tick_size, min_order_size, neg_risk = self._determine_limit_price(
            token_id=token_id,
            market_price=market_price,
            entry_regime=entry_regime,
            side=side,
        )
        if neg_risk and not LIVE_ALLOW_NEG_RISK:
            raise RuntimeError(f"Refusing to trade neg-risk market for token {token_id}")
        size = round(usdc_amount / max(price, tick_size), 2)
        # Skip minimum size check for SELL exits — must always be able to exit an owned position
        if side != "SELL" and min_order_size and size < min_order_size:
            size = max(size, math.ceil(float(min_order_size) * 100.0) / 100.0)
            usdc_amount = round(size * price, 2)
            self._logger.info(
                "Adjusted order to exchange minimum | token=%s size=%.2f min=%.2f notional=$%.2f",
                token_id,
                size,
                min_order_size,
                usdc_amount,
            )

        if self.dry_run:
            self._logger.info(
                "[DRY RUN LIMIT] Would place %s: %s | $%.2f @ %.2f",
                side,
                direction.value,
                usdc_amount,
                price,
            )
            return OrderResult(
                order_id=f"dry-run-{uuid4()}",
                status=OrderStatus.FILLED,
                filled_size=round(usdc_amount / max(price, 0.01), 2),
                avg_price=price,
                submitted_price=price,
                fee_rate_bps=0,
                dry_run=True,
            )

        response = self._client.create_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            order_type=LIVE_ORDER_TYPE,
        )
        fee_rate_loader = getattr(self._client, "get_fee_rate_bps", None)
        fee_rate_bps = int(fee_rate_loader(token_id)) if callable(fee_rate_loader) else 0
        return OrderResult(
            order_id=response.get("orderID"),
            status=OrderStatus.SUBMITTED,
            filled_size=0.0,
            avg_price=price,
            submitted_price=price,
            fee_rate_bps=fee_rate_bps,
            dry_run=False,
        )

    def minimum_usdc_amount(
        self,
        token_id: str,
        market_price: float,
        *,
        entry_regime: str = "MISPRICING",
        side: str = "BUY",
    ) -> float:
        price, tick_size, min_order_size, _neg_risk = self._determine_limit_price(
            token_id=token_id,
            market_price=market_price,
            entry_regime=entry_regime,
            side=side,
        )
        if side == "SELL" or min_order_size <= 0:
            return 0.0
        min_size = math.ceil(float(min_order_size) * 100.0) / 100.0
        return round(min_size * max(price, tick_size), 2)

    def _determine_limit_price(
        self,
        *,
        token_id: str,
        market_price: float,
        entry_regime: str = "MISPRICING",
        side: str = "BUY",
    ) -> tuple[float, float, float, bool]:
        metadata_loader = getattr(self._client, "get_market_metadata", None)
        metadata = metadata_loader(token_id) if callable(metadata_loader) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        tick_size = max(float(metadata.get("tick_size") or 0.01), 0.0001)
        min_order_size = float(metadata.get("min_order_size") or 0.0)
        neg_risk = bool(metadata.get("neg_risk"))
        snapshot = self.get_book_snapshot(token_id)
        best_ask = snapshot.best_ask or float(metadata.get("best_ask") or market_price)
        best_bid = snapshot.best_bid

        slippage_bps = (
            LATE_CONVICTION_SLIPPAGE_BPS
            if entry_regime == "LATE_CONVICTION"
            else LIVE_ORDER_SLIPPAGE_BPS
        )
        
        if side == "BUY":
            max_price = self._round_down_to_tick(
                min(0.99, market_price * (1 + slippage_bps / 10_000)),
                tick_size,
            )
            ask_reference = best_ask if best_ask is not None else market_price
            price = self._round_up_to_tick(max(ask_reference, market_price), tick_size)
            if price > max_price:
                raise RuntimeError(
                    f"Best ask {price:.4f} exceeds slippage cap {max_price:.4f} for {token_id}"
                )
        else: # SELL
            bid_reference = best_bid if best_bid is not None else market_price
            price = self._round_down_to_tick(min(bid_reference, market_price), tick_size)
            # For forced exits (TIME_EXIT, WINDOW_CLOSE, TP/SL) we must sell at
            # whatever the market offers — never block the exit due to slippage.
            if entry_regime != "EXIT":
                min_price = self._round_up_to_tick(
                    max(0.01, market_price * (1 - slippage_bps / 10_000)),
                    tick_size,
                )
                if price < min_price:
                    raise RuntimeError(
                        f"Best bid {price:.4f} is structurally worse than floor {min_price:.4f} for {token_id}"
                    )

        if price <= 0:
            raise RuntimeError(f"Computed non-positive limit price for token {token_id}")
        self._logger.info(
            "%s-priced limit for %s | regime=%s market=%.4f bid=%s ask=%s -> limit=%.4f",
            side,
            token_id,
            entry_regime,
            market_price,
            f"{best_bid:.4f}" if best_bid is not None else "-",
            f"{best_ask:.4f}" if best_ask is not None else "-",
            price,
        )
        return price, tick_size, min_order_size, neg_risk

    async def wait_for_fill(
        self, order_id: str, deadline_ts: int, poll_interval: float = 5.0
    ) -> OrderResult:
        if self.dry_run:
            return OrderResult(
                order_id=order_id,
                status=OrderStatus.FILLED,
                submitted_price=0.0,
                fee_rate_bps=0,
                dry_run=True,
            )

        while True:
            now = int(time.time())
            if now >= deadline_ts:
                self.cancel(order_id)
                return OrderResult(order_id=order_id, status=OrderStatus.CANCELLED)

            order = self._client.get_order(order_id)
            status = str(order.get("status", "")).upper()
            if status == "FILLED":
                return OrderResult(
                    order_id=order_id,
                    status=OrderStatus.FILLED,
                    filled_size=float(order.get("filled_size", 0.0) or 0.0),
                    avg_price=float(order.get("avg_price", 0.0) or 0.0),
                    submitted_price=float(order.get("price", 0.0) or 0.0),
                    fee_rate_bps=int(order.get("fee_rate_bps", 0) or 0),
                )
            if status in {"PARTIALLY_FILLED", "PARTIAL"}:
                filled_size = float(order.get("filled_size", 0.0) or 0.0)
                avg_price = float(order.get("avg_price", 0.0) or 0.0)
                if now + poll_interval >= deadline_ts and filled_size > 0:
                    self.cancel(order_id)
                    return OrderResult(
                        order_id=order_id,
                        status=OrderStatus.PARTIAL,
                        filled_size=filled_size,
                        avg_price=avg_price,
                        submitted_price=float(order.get("price", 0.0) or 0.0),
                        fee_rate_bps=int(order.get("fee_rate_bps", 0) or 0),
                    )
            if status in {"CANCELLED", "CANCELED"}:
                return OrderResult(order_id=order_id, status=OrderStatus.CANCELLED)
            await asyncio.sleep(poll_interval)

    def place_resting_sell(
        self,
        token_id: str,
        direction: Direction,
        usdc_amount: float,
        limit_price: float,
    ) -> OrderResult:
        """Place a GTC resting limit sell at exactly limit_price.

        Returns immediately with PENDING status. The CLOB fills it automatically
        when the market reaches limit_price — no polling lag.
        In dry-run: returns a fake PENDING order; the polling path handles fills.
        """
        metadata_loader = getattr(self._client, "get_market_metadata", None)
        metadata = metadata_loader(token_id) if callable(metadata_loader) else {}
        tick_size = max(float(metadata.get("tick_size") or 0.01), 0.0001)

        price = self._round_down_to_tick(limit_price, tick_size)
        if price <= 0:
            raise RuntimeError(f"Non-positive resting sell price for token {token_id}")

        size = round(usdc_amount / max(price, tick_size), 2)

        if self.dry_run:
            self._logger.info(
                "[DRY RUN RESTING] Would place GTC SELL: %s | $%.2f @ %.4f",
                direction.value, usdc_amount, price,
            )
            return OrderResult(
                order_id=f"dry-resting-{uuid4()}",
                status=OrderStatus.PENDING,
                filled_size=0.0,
                avg_price=0.0,
                submitted_price=price,
                dry_run=True,
            )

        self._logger.info(
            "RESTING SELL (GTC) for %s | $%.2f @ %.4f (%.2f shares)",
            token_id[:16], usdc_amount, price, size,
        )
        response = self._client.create_order(
            token_id=token_id,
            price=price,
            size=size,
            side="SELL",
            order_type="GTC",
        )
        return OrderResult(
            order_id=response.get("orderID", ""),
            status=OrderStatus.PENDING,
            filled_size=0.0,
            avg_price=0.0,
            submitted_price=price,
            dry_run=False,
        )

    def get_order_status(self, order_id: str) -> OrderResult:
        """Return current fill status of an order.

        In dry-run (or for dry- prefixed IDs): returns PENDING so the polling
        path handles simulation. In live: calls the API.
        """
        if self.dry_run or (order_id and order_id.startswith("dry-")):
            return OrderResult(order_id=order_id, status=OrderStatus.PENDING, dry_run=True)
        try:
            data = self._client.get_order(order_id)
            status_str = str(data.get("status", "PENDING")).upper()
            if status_str == "FILLED":
                status = OrderStatus.FILLED
            elif status_str in {"PARTIALLY_FILLED", "PARTIAL"}:
                status = OrderStatus.PARTIAL
            elif status_str in {"CANCELLED", "CANCELED"}:
                status = OrderStatus.CANCELLED
            else:
                status = OrderStatus.PENDING
            return OrderResult(
                order_id=order_id,
                status=status,
                filled_size=float(data.get("filled_size", 0) or 0),
                avg_price=float(data.get("avg_price", 0) or 0),
                submitted_price=float(data.get("price", 0) or 0),
                fee_rate_bps=int(data.get("fee_rate_bps", 0) or 0),
            )
        except Exception as e:
            self._logger.warning("Failed to check order status for %s: %s", order_id, e)
            return OrderResult(order_id=order_id, status=OrderStatus.PENDING)

    def cancel(self, order_id: str) -> bool:
        if self.dry_run:
            return True
        self._client.cancel_order(order_id)
        return True

    def cancel_all(self) -> bool:
        if self.dry_run:
            return True
        cancel_all_orders = getattr(self._client, "cancel_all_orders", None)
        if not callable(cancel_all_orders):
            return False
        cancel_all_orders()
        return True

    def get_best_ask(self, token_id: str) -> Optional[float]:
        try:
            book = self._client.get_order_book(token_id)
        except RuntimeError as e:
            if "API unavailable" in str(e):
                return None
            raise
        asks = book.get("asks", [])
        if not asks:
            return None
        best = min(float(ask["price"]) for ask in asks)
        return best

    def get_book_snapshot(self, token_id: str) -> BookSnapshot | None:
        def _level_size(level: dict) -> float:
            for key in ("size", "amount"):
                raw = level.get(key)
                if raw in (None, ""):
                    continue
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    continue
            return 0.0

        try:
            book = self._client.get_order_book(token_id)
        except RuntimeError as e:
            if "API unavailable" in str(e):
                return None
            raise
        asks = book.get("asks", [])
        bids = book.get("bids", [])
        best_ask = min((float(ask["price"]) for ask in asks), default=None)
        best_bid = max((float(bid["price"]) for bid in bids), default=None)
        best_ask_size = sum(
            _level_size(ask) for ask in asks if best_ask is not None and float(ask["price"]) == best_ask
        )
        best_bid_size = sum(
            _level_size(bid) for bid in bids if best_bid is not None and float(bid["price"]) == best_bid
        )
        total_ask_size = sum(_level_size(ask) for ask in asks)
        total_bid_size = sum(_level_size(bid) for bid in bids)
        return BookSnapshot(
            ask_count=len(asks),
            bid_count=len(bids),
            best_ask=best_ask,
            best_bid=best_bid,
            best_ask_size=best_ask_size,
            best_bid_size=best_bid_size,
            total_ask_size=total_ask_size,
            total_bid_size=total_bid_size,
        )

    @staticmethod
    def _round_up_to_tick(value: float, tick_size: float) -> float:
        steps = math.ceil((value - 1e-12) / tick_size)
        rounded = steps * tick_size
        return round(rounded, Orderer._tick_precision(tick_size))

    @staticmethod
    def _round_down_to_tick(value: float, tick_size: float) -> float:
        steps = math.floor((value + 1e-12) / tick_size)
        rounded = steps * tick_size
        return round(max(0.0, rounded), Orderer._tick_precision(tick_size))

    @staticmethod
    def _tick_precision(tick_size: float) -> int:
        text = f"{tick_size:.10f}".rstrip("0")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])
