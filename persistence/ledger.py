from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime

from config import LEDGER_FILE
from state.types import Direction


def net_pnl(resolution: dict) -> float:
    """Net P&L of a terminal row: gross pnl minus logged fees plus earlier partial closes.

    Rows written before fees were logged carry no fees_usdc and count as gross.
    """
    return (
        float(resolution.get("pnl") or 0.0)
        - float(resolution.get("fees_usdc") or 0.0)
        + float(resolution.get("partial_net_usdc") or 0.0)
    )


class Ledger:
    def __init__(self, filepath: str = LEDGER_FILE):
        self.filepath = filepath
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        if not os.path.exists(filepath):
            with open(filepath, "a", encoding="utf-8"):
                pass

    def record_trade(
        self,
        window_end_ts: int,
        direction: str,
        amount_usdc: float,
        price: float,
        edge: float,
        ev: float,
        model: str,
        order_id: str | None,
        dry_run: bool,
        market_url: str = "",
        market_slug: str = "",
        asset: str = "",
        open_price: float | None = None,
        role: str = "PRIMARY",
        track: str = "",
        feature_snapshot: dict | None = None,
        execution_snapshot: dict | None = None,
    ) -> None:
        record = {
            "type": "trade",
            "ts": time.time(),
            "window_end_ts": window_end_ts,
            "direction": direction,
            "amount_usdc": amount_usdc,
            "price": price,
            "edge": edge,
            "ev": ev,
            "model": model,
            "order_id": order_id,
            "dry_run": dry_run,
            "market_url": market_url,
            "market_slug": market_slug,
            "asset": asset,
            "role": role,
            "track": track,
        }
        if open_price is not None:
            record["open_price"] = open_price
        if feature_snapshot:
            record["feature_snapshot"] = feature_snapshot
        if execution_snapshot:
            record["execution_snapshot"] = execution_snapshot
        self._append(record)

    def record_submission(
        self,
        order_id: str,
        window_end_ts: int,
        *,
        direction: str,
        amount_usdc: float,
        submitted_price: float,
        market_price: float,
        model: str,
        dry_run: bool,
        role: str = "PRIMARY",
        track: str = "",
        phase: str = "ENTRY",
        client_order_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        record = {
            "type": "submission",
            "ts": time.time(),
            "order_id": order_id,
            "window_end_ts": window_end_ts,
            "direction": direction,
            "amount_usdc": amount_usdc,
            "submitted_price": submitted_price,
            "market_price": market_price,
            "model": model,
            "dry_run": dry_run,
            "role": role,
            "track": track,
            "phase": phase,
        }
        if client_order_id:
            record["client_order_id"] = client_order_id
        if details:
            record["details"] = details
        self._append(record)

    def record_order_intent(
        self,
        client_order_id: str,
        *,
        window_end_ts: int,
        status: str = "IN_FLIGHT",
        details: dict | None = None,
    ) -> None:
        """Journal an order-submission intent for idempotency.

        Written BEFORE the network submit so a retry after a dropped response
        cannot duplicate a possibly-live order. ``status`` transitions:
        ``IN_FLIGHT`` (blocks resubmission) -> ``ABORTED`` (pre-network failure,
        frees the id for a clean retry). A successful submit leaves the intent
        IN_FLIGHT so the id stays blocked.
        """
        record = {
            "type": "order_intent",
            "ts": time.time(),
            "client_order_id": client_order_id,
            "window_end_ts": window_end_ts,
            "status": status,
        }
        if details:
            record["details"] = details
        self._append(record)

    def record_resolution(
        self,
        order_id: str,
        window_end_ts: int,
        resolved_up: bool,
        direction: str,
        amount_usdc: float,
        price: float,
        final_price: float | None = None,
        price_to_beat: float | None = None,
        closed_time: str | None = None,
        uma_resolution_status: str | None = None,
        feature_snapshot: dict | None = None,
        fees_usdc: float | None = None,
        partial_net_usdc: float = 0.0,
    ) -> None:
        side = Direction(direction)
        won = (side == Direction.UP and resolved_up) or (
            side == Direction.DOWN and not resolved_up
        )
        pnl = amount_usdc * (1 / price - 1) if won else -amount_usdc
        record = {
            "type": "resolution",
            "ts": time.time(),
            "order_id": order_id,
            "window_end_ts": window_end_ts,
            "resolved_up": resolved_up,
            "pnl": pnl,
        }
        if fees_usdc is not None:
            record["fees_usdc"] = fees_usdc
        if partial_net_usdc:
            record["partial_net_usdc"] = partial_net_usdc  # earlier partial closes, already net of fees
        if final_price is not None:
            record["final_price"] = final_price
        if price_to_beat is not None:
            record["price_to_beat"] = price_to_beat
        if closed_time is not None:
            record["closed_time"] = closed_time
        if uma_resolution_status is not None:
            record["uma_resolution_status"] = uma_resolution_status
        if feature_snapshot:
            record.update(feature_snapshot)
        self._append(record)

    def record_early_exit(
        self,
        order_id: str,
        window_end_ts: int,
        pnl: float,
        exit_price: float,
        reason: str,
        fees_usdc: float | None = None,
    ) -> None:
        """pnl is gross of fees; fees_usdc (entry + exit taker fees) when known."""
        record = {
            "type": "resolution",
            "ts": time.time(),
            "order_id": order_id,
            "window_end_ts": window_end_ts,
            "resolved_up": None,
            "pnl": pnl,
            "final_price": exit_price,
            "uma_resolution_status": f"EARLY_EXIT_{reason}",
        }
        if fees_usdc is not None:
            record["fees_usdc"] = fees_usdc
        self._append(record)

    def record_cancellation(
        self,
        order_id: str,
        window_end_ts: int,
        status: str = "CANCELLED",
        details: dict | None = None,
    ) -> None:
        record = {
            "type": "cancellation",
            "ts": time.time(),
            "order_id": order_id,
            "window_end_ts": window_end_ts,
            "status": status,
        }
        if details:
            record["details"] = details
        self._append(record)

    def record_fill(
        self,
        order_id: str,
        window_end_ts: int,
        status: str,
        filled_size: float,
        avg_price: float,
        details: dict | None = None,
    ) -> None:
        record = {
            "type": "fill",
            "ts": time.time(),
            "order_id": order_id,
            "window_end_ts": window_end_ts,
            "status": status,
            "filled_size": filled_size,
            "avg_price": avg_price,
        }
        if details:
            record["details"] = details
        self._append(record)

    def record_candidate(
        self,
        *,
        window_end_ts: int,
        track: str,
        model: str,
        direction: str | None,
        regime_candidate: str,
        reason_code: str,
        reason_detail: str = "",
        would_trade: bool = False,
        skip_reason: str = "",
        entry_regime: str = "",
        market_price: float | None = None,
        up_price: float | None = None,
        down_price: float | None = None,
        liquidity_score: float | None = None,
        book_spread: float | None = None,
        gross_expected_profit_usdc: float | None = None,
        net_expected_profit_usdc: float | None = None,
        estimated_cost_usdc: float | None = None,
        planned_bet_usdc: float | None = None,
        edge: float | None = None,
        ev: float | None = None,
        confidence: float | None = None,
        selected_probability: float | None = None,
        direction_probability: float | None = None,
        strongest_side: str = "NONE",
        market_conviction: float | None = None,
        btc_confirmed: bool | None = None,
        seconds_remaining: int | None = None,
        btc_price: float | None = None,
        open_price: float | None = None,
        volatility: float | None = None,
        probabilities: dict[str, float] | None = None,
        details: dict | None = None,
    ) -> None:
        record = {
            "type": "candidate",
            "ts": time.time(),
            "window_end_ts": window_end_ts,
            "track": track,
            "model": model,
            "direction": direction,
            "regime_candidate": regime_candidate,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "would_trade": would_trade,
            "skip_reason": skip_reason,
            "entry_regime": entry_regime,
            "market_price": market_price,
            "up_price": up_price,
            "down_price": down_price,
            "liquidity_score": liquidity_score,
            "book_spread": book_spread,
            "gross_expected_profit_usdc": gross_expected_profit_usdc,
            "net_expected_profit_usdc": net_expected_profit_usdc,
            "estimated_cost_usdc": estimated_cost_usdc,
            "planned_bet_usdc": planned_bet_usdc,
            "edge": edge,
            "ev": ev,
            "confidence": confidence,
            "selected_probability": selected_probability,
            "direction_probability": direction_probability,
            "strongest_side": strongest_side,
            "market_conviction": market_conviction,
            "btc_confirmed": btc_confirmed,
            "seconds_remaining": seconds_remaining,
            "btc_price": btc_price,
            "open_price": open_price,
            "volatility": volatility,
            "probabilities": probabilities or {},
        }
        if details:
            record["details"] = details
        self._append(record)

    def record_window_outcome(
        self,
        window_end_ts: int,
        resolved_up: bool,
        market_slug: str | None = None,
        final_price: float | None = None,
    ) -> None:
        """Journal the resolved direction of a window with no trade attached.

        Opens calibration data for windows the bot watched but skipped:
        replay and report join these to candidate records by window_end_ts.
        Never counted as a trade (no order_id, no pnl).
        """
        record = {
            "type": "window_outcome",
            "ts": time.time(),
            "window_end_ts": window_end_ts,
            "resolved_up": resolved_up,
        }
        if market_slug is not None:
            record["market_slug"] = market_slug
        if final_price is not None:
            record["final_price"] = final_price
        self._append(record)

    def get_all_window_outcomes(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "window_outcome"
        ]

    def has_window_outcome(self, window_end_ts: int) -> bool:
        return any(
            outcome.get("window_end_ts") == window_end_ts
            for outcome in self.get_all_window_outcomes()
        )

    def get_all_trades(self) -> list[dict]:
        return [record for record in self._read_records() if record.get("type") == "trade"]

    def get_all_submissions(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "submission"
        ]

    def get_all_resolutions(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "resolution"
        ]

    def get_all_cancellations(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "cancellation"
        ]

    def get_all_fills(self) -> list[dict]:
        return [record for record in self._read_records() if record.get("type") == "fill"]

    def get_all_candidates(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "candidate"
        ]

    def get_order_ids(self) -> set[str]:
        return {
            str(record.get("order_id"))
            for record in self._read_records()
            if record.get("order_id")
        }

    def has_fill_record(self, order_id: str) -> bool:
        if not order_id:
            return False
        return any(fill.get("order_id") == order_id for fill in self.get_all_fills())

    def get_all_order_intents(self) -> list[dict]:
        return [
            record for record in self._read_records() if record.get("type") == "order_intent"
        ]

    def get_open_order_intents(self) -> list[dict]:
        """Return the latest intent record per id whose latest status is IN_FLIGHT.

        These are intents still awaiting a terminal outcome — including held
        ambiguous submissions whose order may or may not have reached the
        exchange. Used by inline reconciliation to resolve them mid-session.
        """
        latest: dict[str, dict] = {}
        for record in self._read_records():
            if record.get("type") == "order_intent" and record.get("client_order_id"):
                latest[record["client_order_id"]] = record
        return [rec for rec in latest.values() if rec.get("status") == "IN_FLIGHT"]

    def has_open_order_intent(self, client_order_id: str) -> bool:
        """True if the most recent intent for this id is not ABORTED.

        An open intent means an order with this client_order_id was already
        submitted (or may have reached the exchange after an ambiguous failure),
        so it must not be submitted again.
        """
        if not client_order_id:
            return False
        latest_status: str | None = None
        for record in self._read_records():
            if (
                record.get("type") == "order_intent"
                and record.get("client_order_id") == client_order_id
            ):
                latest_status = record.get("status")
        return latest_status is not None and latest_status != "ABORTED"

    def get_unresolved_trades(self) -> list[dict]:
        closed_ids = {
            record.get("order_id")
            for record in self.get_all_resolutions()
            if record.get("order_id")
        }
        closed_ids.update(
            record.get("order_id")
            for record in self.get_all_cancellations()
            if record.get("order_id")
        )
        return [
            trade
            for trade in self.get_all_trades()
            if trade.get("order_id") and trade.get("order_id") not in closed_ids
        ]

    def has_terminal_record(self, order_id: str) -> bool:
        if not order_id:
            return False
        return any(
            record.get("order_id") == order_id
            for record in (
                self.get_all_resolutions() + self.get_all_cancellations()
            )
        )

    def get_daily_summary(self, date_str: str | None = None) -> dict:
        trades = self.get_all_trades()
        resolutions = self.get_all_resolutions()
        cancellations = self.get_all_cancellations()

        if date_str is None:
            if trades:
                latest_trade = max(trades, key=lambda record: record["window_end_ts"])
                target_date = datetime.fromtimestamp(
                    latest_trade["window_end_ts"], tz=UTC
                ).date()
            else:
                target_date = datetime.now(UTC).date()
            date_str = target_date.isoformat()
        else:
            target_date = datetime.fromisoformat(date_str).date()

        daily_trades = [
            trade
            for trade in trades
            if datetime.fromtimestamp(
                trade["window_end_ts"], tz=UTC
            ).date()
            == target_date
        ]
        primary_trades = [
            trade for trade in daily_trades if trade.get("role", "PRIMARY") != "HEDGE"
        ]
        trade_ids = {trade.get("order_id") for trade in daily_trades}
        primary_trade_ids = {trade.get("order_id") for trade in primary_trades}
        daily_resolutions = [
            resolution
            for resolution in resolutions
            if resolution.get("order_id") in trade_ids
        ]
        daily_cancellations = [
            cancellation
            for cancellation in cancellations
            if cancellation.get("order_id") in trade_ids
        ]
        primary_resolutions = [
            resolution
            for resolution in daily_resolutions
            if resolution.get("order_id") in primary_trade_ids
        ]
        primary_cancellations = [
            cancellation
            for cancellation in daily_cancellations
            if cancellation.get("order_id") in primary_trade_ids
        ]

        wins = sum(1 for resolution in primary_resolutions if net_pnl(resolution) > 0)
        losses = sum(1 for resolution in primary_resolutions if net_pnl(resolution) <= 0)
        total_pnl = sum(net_pnl(resolution) for resolution in daily_resolutions)
        avg_edge = (
            sum(trade["edge"] for trade in primary_trades) / len(primary_trades)
            if primary_trades
            else 0.0
        )

        return {
            "date": date_str,
            "total_trades": len(primary_trades),
            "resolved_trades": len(primary_resolutions),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / len(primary_resolutions)) if primary_resolutions else 0.0,
            "total_pnl": total_pnl,
            "avg_edge": avg_edge,
            "dry_run_trades": sum(1 for trade in primary_trades if trade.get("dry_run")),
            "hedge_trades": sum(1 for trade in daily_trades if trade.get("role") == "HEDGE"),
            "cancelled_trades": len(primary_cancellations),
            "total_positions": len(daily_trades),
        }

    def get_model_performance(self) -> dict:
        trades = {
            trade["order_id"]: trade for trade in self.get_all_trades() if trade.get("order_id")
        }
        performance: dict[str, dict[str, float]] = {}
        for resolution in self.get_all_resolutions():
            trade = trades.get(resolution["order_id"])
            if not trade:
                continue
            if trade.get("role", "PRIMARY") == "HEDGE":
                continue
            model = trade["model"]
            bucket = performance.setdefault(model, {"trades": 0, "wins": 0})
            bucket["trades"] += 1
            if net_pnl(resolution) > 0:
                bucket["wins"] += 1

        return {
            model: {
                "trades": stats["trades"],
                "win_rate": stats["wins"] / stats["trades"] if stats["trades"] else 0.0,
            }
            for model, stats in performance.items()
        }

    def _append(self, record: dict) -> None:
        payload = json.dumps(record, sort_keys=True)
        with self._lock:
            with open(self.filepath, "a", encoding="utf-8") as handle:
                handle.write(payload + "\n")

    def _read_records(self) -> list[dict]:
        records: list[dict] = []
        with open(self.filepath, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records
