from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from config import (
    ASSET_SERIES_IDS,
    ASSET_SERIES_SLUGS,
    POLYMARKET_FUNDER,
    POLYMARKET_HOST,
    POLYMARKET_SIGNATURE_TYPE,
)
from market.orderer import AmbiguousOrderError

# Transport failures where the request may have reached the exchange but the
# response was lost. Matched by class name to avoid a hard `requests` dependency.
_AMBIGUOUS_TRANSPORT_ERROR_NAMES = frozenset(
    {
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectionError",
        "ChunkedEncodingError",
        "RemoteDisconnected",
        "ProtocolError",
    }
)


def _is_ambiguous_transport_error(exc: BaseException) -> bool:
    return any(
        cls.__name__ in _AMBIGUOUS_TRANSPORT_ERROR_NAMES for cls in type(exc).__mro__
    )

try:  # pragma: no cover - optional dependency surface
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        ApiCreds,
        AssetType,
        BalanceAllowanceParams,
        OpenOrderParams,
        OrderArgs,
        TradeParams,
    )
except Exception:  # pragma: no cover - optional dependency surface
    ApiCreds = None
    AssetType = None
    BalanceAllowanceParams = None
    ClobClient = None
    OpenOrderParams = None
    OrderArgs = None
    TradeParams = None


@dataclass
class AccountSnapshot:
    balance_usdc: float
    allowance_usdc: float
    available_to_trade_usdc: float
    open_orders: list[dict]


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_numeric_value(payload: Any, keys: set[str]) -> float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in keys:
                parsed = _coerce_float(value)
                if parsed is not None:
                    return parsed
            parsed = _find_numeric_value(value, keys)
            if parsed is not None:
                return parsed
        return None
    if isinstance(payload, list):
        for item in payload:
            parsed = _find_numeric_value(item, keys)
            if parsed is not None:
                return parsed
    return None


def _extract_order_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "orders"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        if payload:
            return [payload]
    return []


def _extract_trade_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "trades"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        if payload:
            return [payload]
    return []


def _extract_event_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "events"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        if payload.get("id"):
            return [payload]
    return []


def _iso_z(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _book_levels(book: Any, side: str) -> list:
    """Ask/bid levels from object- or dict-shaped book payloads."""
    if isinstance(book, dict):
        return book.get(side) or []
    return getattr(book, side, []) or []


def _level_field(level: Any, field: str) -> Any:
    if isinstance(level, dict):
        return level.get(field, "")
    return getattr(level, field, "")


class PublicPolymarketClient:
    GAMMA_HOST = "https://gamma-api.polymarket.com"

    def __init__(self, *, asset: str = "BTC"):
        self.asset = asset.upper()
        self._series_id: str | None = None

    def get_markets(self, next_cursor: str = "MA==") -> dict:
        del next_cursor
        return {"data": self._get_asset_window_markets()}

    def get_order_book(self, token_id: str) -> dict:
        query = urllib.parse.urlencode({"token_id": token_id})
        return self._get_json(f"{POLYMARKET_HOST}/book?{query}")

    def create_order(self, token_id: str, price: float, size: float, side: str) -> dict:
        raise RuntimeError("PublicPolymarketClient is read-only")

    def cancel_order(self, order_id: str) -> dict:
        raise RuntimeError("PublicPolymarketClient is read-only")

    def get_order(self, order_id: str) -> dict:
        raise RuntimeError("PublicPolymarketClient is read-only")

    def get_fee_rate_bps(self, token_id: str) -> int:
        del token_id
        return 0

    def get_trades(
        self,
        *,
        order_id: str | None = None,
        asset_id: str | None = None,
        after: int | None = None,
    ) -> list[dict]:
        del order_id, asset_id, after
        return []

    def get_market_metadata(self, token_id: str) -> dict:
        book = self.get_order_book(token_id)
        asks = book.get("asks", [])
        best_ask = (
            min(float(ask["price"]) for ask in asks)
            if asks
            else None
        )
        return {
            "best_ask": best_ask,
            "tick_size": _coerce_float(book.get("tick_size")) or 0.01,
            "min_order_size": _coerce_float(book.get("min_order_size")) or 0.0,
            "neg_risk": bool(book.get("neg_risk")) if book.get("neg_risk") is not None else False,
        }

    def get_event_by_slug(self, slug: str) -> dict:
        encoded = urllib.parse.quote(slug, safe="")
        return self._get_json(f"{self.GAMMA_HOST}/events/slug/{encoded}")

    def get_market_resolution(self, slug: str) -> dict | None:
        event = self.get_event_by_slug(slug)
        markets = event.get("markets") or []
        if not markets:
            return None

        market = markets[0]
        outcome_prices = market.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)
        if len(outcome_prices) < 2:
            return None

        parsed = [float(value) for value in outcome_prices]
        resolved_up: bool | None = None
        if parsed[0] >= 0.999 and parsed[1] <= 0.001:
            resolved_up = True
        elif parsed[1] >= 0.999 and parsed[0] <= 0.001:
            resolved_up = False

        if resolved_up is None:
            return None

        metadata = event.get("eventMetadata") or {}
        return {
            "resolved_up": resolved_up,
            "closed": bool(event.get("closed")),
            "closed_time": market.get("closedTime") or event.get("closedTime"),
            "final_price": metadata.get("finalPrice"),
            "price_to_beat": metadata.get("priceToBeat"),
            "outcome_prices": parsed,
            "uma_resolution_status": market.get("umaResolutionStatus"),
        }

    def _get_json(self, url: str) -> Any:
        import socket
        import urllib.error
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except (TimeoutError, urllib.error.URLError, socket.gaierror) as e:
            import logging
            logging.getLogger("market.client").warning(f"API unreachable: {e}")
            raise RuntimeError(f"Polymarket API unavailable: {e}") from e

    def _normalize_market(self, market: dict) -> dict:
        outcomes = market.get("outcomes", [])
        token_ids = market.get("clobTokenIds", [])
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        tokens = [
            {"outcome": outcome, "token_id": token_id}
            for outcome, token_id in zip(outcomes, token_ids, strict=False)
        ]
        return {
            "market_slug": market.get("slug", ""),
            "condition_id": market.get("conditionId", ""),
            "question": market.get("question", ""),
            "end_date_iso": market.get("endDate", ""),
            "series_slug": market.get("seriesSlug", ""),
            "tokens": tokens,
        }

    def _get_asset_window_markets(self) -> list[dict]:
        series_id = self._get_series_id()
        if series_id is None:
            return []

        # The /series/{id} endpoint only surfaces a far-future daily batch of
        # events; the currently-active 5-minute windows are reachable via the
        # date-filtered /events query. Query a band around now and filter again
        # in code (defense in depth against the API ignoring the date params).
        now_dt = datetime.now(UTC)
        now_ts = now_dt.timestamp()
        query = urllib.parse.urlencode(
            {
                "series_id": series_id,
                "closed": "false",
                "end_date_min": _iso_z(now_ts - 360),
                "end_date_max": _iso_z(now_ts + 1500),
                "order": "endDate",
                "ascending": "true",
                "limit": 40,
            }
        )
        events = _extract_event_list(self._get_json(f"{self.GAMMA_HOST}/events?{query}"))

        candidates: list[tuple[float, dict]] = []
        for event in events:
            end_date = event.get("endDate")
            event_id = event.get("id")
            if not end_date or not event_id:
                continue
            end_ts = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
            if end_ts < now_ts - 300 or end_ts > now_ts + 1200:
                continue
            candidates.append((abs(end_ts - now_ts), event))

        candidates.sort(key=lambda item: item[0])

        results: list[dict] = []
        for _, event in candidates[:8]:
            target_series = (event.get("seriesSlug") or "").lower()
            markets = event.get("markets")
            if not markets:
                detail = self._get_json(f"{self.GAMMA_HOST}/events/{event.get('id')}")
                markets = detail.get("markets", [])
                event = {**detail, **event}
            for market in markets:
                results.append(
                    self._normalize_market(
                        {
                            "slug": market.get("slug", event.get("slug", "")),
                            "conditionId": market.get("conditionId", ""),
                            "question": market.get("question", event.get("title", "")),
                            "endDate": market.get("endDate", event.get("endDate", "")),
                            "outcomes": market.get("outcomes", "[]"),
                            "clobTokenIds": market.get("clobTokenIds", "[]"),
                            "seriesSlug": target_series,
                        }
                    )
                )

        return results

    def _get_series_id(self) -> str | None:
        if self._series_id is not None:
            return self._series_id

        hardcoded = ASSET_SERIES_IDS.get(self.asset)
        if hardcoded:
            self._series_id = hardcoded
            return self._series_id

        target_series = {slug.lower() for slug in ASSET_SERIES_SLUGS.get(self.asset, ())}
        offset = 0
        limit = 500

        while True:
            url = (
                f"{self.GAMMA_HOST}/events?active=true&closed=false"
                f"&limit={limit}&offset={offset}"
            )
            events = self._get_json(url)
            if not events:
                return None

            for event in events:
                for series in event.get("series", []):
                    slug = (series.get("slug") or "").lower()
                    if slug in target_series:
                        self._series_id = str(series.get("id"))
                        return self._series_id

            if len(events) < limit:
                return None
            offset += limit


class PolymarketClient(PublicPolymarketClient):
    def __init__(
        self,
        private_key: str,
        api_key: str,
        secret: str,
        passphrase: str,
        *,
        asset: str = "BTC",
        signature_type: int = POLYMARKET_SIGNATURE_TYPE,
        funder: str = POLYMARKET_FUNDER,
    ):
        super().__init__(asset=asset)
        self._client = self._build_trading_client(
            private_key,
            api_key,
            secret,
            passphrase,
            signature_type=signature_type,
            funder=funder,
        )

    def get_order_book(self, token_id: str) -> dict:
        book = self._client.get_order_book(token_id)
        return self._normalize_order_book(book)

    def create_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        *,
        order_type: str = "FOK",
        post_only: bool = False,
    ) -> dict:
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        order = self._client.create_order(order_args)
        try:
            response = self._client.post_order(
                order, orderType=order_type, post_only=post_only
            )
        except Exception as exc:  # noqa: BLE001 - classify transport ambiguity
            if _is_ambiguous_transport_error(exc):
                raise AmbiguousOrderError(str(exc) or type(exc).__name__) from exc
            raise
        if isinstance(response, dict):
            order_id = (
                response.get("orderID")
                or response.get("orderId")
                or response.get("id")
            )
            normalized = dict(response)
            if order_id is not None:
                normalized["orderID"] = order_id
            return normalized

        order_id = getattr(response, "orderID", None) or getattr(response, "id", None)
        return {"orderID": order_id}

    def cancel_order(self, order_id: str) -> dict:
        return self._client.cancel(order_id)

    def get_order(self, order_id: str) -> dict:
        return self._client.get_order(order_id)

    def get_fee_rate_bps(self, token_id: str) -> int:
        return int(self._client.get_fee_rate_bps(token_id))

    def get_open_orders(self, market: str | None = None, asset_id: str | None = None) -> list[dict]:
        params = None
        if OpenOrderParams is not None:
            params = OpenOrderParams(market=market, asset_id=asset_id)
        return _extract_order_list(self._client.get_orders(params))

    def get_trades(
        self,
        *,
        order_id: str | None = None,
        asset_id: str | None = None,
        after: int | None = None,
    ) -> list[dict]:
        params = None
        if TradeParams is not None:
            params = TradeParams(id=order_id, asset_id=asset_id, after=after)
        payload = self._client.get_trades(params)
        return _extract_trade_list(payload)

    def cancel_all_orders(self) -> dict:
        return self._client.cancel_all()

    def get_account_snapshot(self) -> AccountSnapshot:
        if BalanceAllowanceParams is None:
            raise RuntimeError("BalanceAllowanceParams is unavailable in this environment")

        asset_type = getattr(AssetType, "COLLATERAL", "COLLATERAL")
        params = BalanceAllowanceParams(
            asset_type=asset_type,
            signature_type=POLYMARKET_SIGNATURE_TYPE,
        )
        payload = self._client.get_balance_allowance(params)
        balance = _find_numeric_value(
            payload,
            {
                "balance",
                "availablebalance",
                "available_balance",
                "available",
                "total",
            },
        )
        allowance = _find_numeric_value(
            payload,
            {
                "allowance",
                "availableallowance",
                "available_allowance",
                "approved",
            },
        )
        balance_value = balance or 0.0
        allowance_value = allowance or 0.0
        return AccountSnapshot(
            balance_usdc=balance_value,
            allowance_usdc=allowance_value,
            available_to_trade_usdc=max(0.0, min(balance_value, allowance_value)),
            open_orders=self.get_open_orders(),
        )

    def _build_trading_client(
        self,
        private_key: str,
        api_key: str,
        secret: str,
        passphrase: str,
        *,
        signature_type: int,
        funder: str,
    ) -> Any:
        if ClobClient is None or ApiCreds is None or OrderArgs is None:
            raise RuntimeError(
                "py-clob-client is not installed or unavailable in this environment"
            )

        effective_funder = funder or None
        creds = None
        if api_key.strip() and secret.strip() and passphrase.strip():
            creds = ApiCreds(
                api_key=api_key,
                api_secret=secret,
                api_passphrase=passphrase,
            )
        else:
            bootstrap = ClobClient(
                POLYMARKET_HOST,
                chain_id=137,
                key=private_key,
                signature_type=signature_type,
                funder=effective_funder,
            )
            creds = bootstrap.create_or_derive_api_creds()

        return ClobClient(
            POLYMARKET_HOST,
            chain_id=137,
            key=private_key,
            creds=creds,
            signature_type=signature_type,
            funder=effective_funder,
        )

    def _normalize_order_book(self, book: Any) -> dict:
        asks = []
        for ask in _book_levels(book, "asks"):
            asks.append({"price": str(_level_field(ask, "price")), "size": str(_level_field(ask, "size"))})

        bids = []
        for bid in _book_levels(book, "bids"):
            bids.append({"price": str(_level_field(bid, "price")), "size": str(_level_field(bid, "size"))})

        return {
            "market": getattr(book, "market", None),
            "asset_id": getattr(book, "asset_id", None),
            "asks": asks,
            "bids": bids,
            "tick_size": getattr(book, "tick_size", None),
            "neg_risk": getattr(book, "neg_risk", None),
            "min_order_size": getattr(book, "min_order_size", None),
        }
