from __future__ import annotations

from ops.live_startup import reconcile_open_order_intents
from persistence.ledger import Ledger


def _held_ambiguous_intent(ledger: Ledger, cid: str, *, token_id: str, window_end_ts: int):
    """Journal an intent the way submit_trade does after an ambiguous failure.

    IN_FLIGHT and *no* submission carries the client_order_id (the order may
    have reached the exchange, but we never confirmed an order_id).
    """
    ledger.record_order_intent(
        cid,
        window_end_ts=window_end_ts,
        status="IN_FLIGHT",
        details={
            "token_id": token_id,
            "direction": "UP",
            "role": "PRIMARY",
            "amount_usdc": 5.0,
            "track": "A",
            "model": "A",
            "market_slug": "btc-updown-5m-100",
            "asset": "BTC",
        },
    )


class _FakeClient:
    def __init__(self, *, trades=None, open_orders=None):
        self._trades = trades or []
        self._open_orders = open_orders or []
        self.cancelled: list[str] = []

    def get_trades(self, after=None):
        del after
        return list(self._trades)

    def get_open_orders(self):
        return list(self._open_orders)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"status": "CANCELLED"}


def test_reconcile_adopts_ambiguous_fill_as_managed_trade(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    _held_ambiguous_intent(ledger, "cid-fill", token_id="up-1", window_end_ts=100)

    client = _FakeClient(
        trades=[{"order_id": "live-123", "asset_id": "up-1", "size": "8.62", "price": "0.59"}]
    )

    result = reconcile_open_order_intents(
        client, ledger, now=200, lookback_seconds=3600, abort_grace_seconds=60
    )

    assert result.reconciled_filled == 1
    # The ambiguous fill is now a managed, monitorable trade.
    unresolved = ledger.get_unresolved_trades()
    assert len(unresolved) == 1
    assert unresolved[0]["order_id"] == "live-123"
    # Carries market context so the resolution sweep can settle it mid-session.
    assert unresolved[0]["market_slug"] == "btc-updown-5m-100"
    assert unresolved[0]["model"] == "A"
    fills = ledger.get_all_fills()
    assert fills[0]["details"]["inline_reconciliation"] is True
    # The id stays blocked (an order this cid produced is now live) but is no
    # longer an *open* (unresolved) intent.
    assert ledger.has_open_order_intent("cid-fill") is True
    assert ledger.get_open_order_intents() == []


def test_reconcile_cancels_unconfirmed_resting_order(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    _held_ambiguous_intent(ledger, "cid-open", token_id="up-1", window_end_ts=100)

    client = _FakeClient(
        open_orders=[{"id": "open-9", "asset_id": "up-1", "price": "0.58", "size": "8.0"}]
    )

    result = reconcile_open_order_intents(
        client, ledger, now=200, lookback_seconds=3600, abort_grace_seconds=60
    )

    assert result.cancelled == 1
    assert client.cancelled == ["open-9"]
    cancellations = ledger.get_all_cancellations()
    assert cancellations[0]["order_id"] == "open-9"
    # An unconfirmed, cancelled order frees the id.
    assert ledger.has_open_order_intent("cid-open") is False


def test_reconcile_aborts_stale_intent_after_grace(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    _held_ambiguous_intent(ledger, "cid-stale", token_id="up-1", window_end_ts=100)

    client = _FakeClient()  # nothing live matches

    result = reconcile_open_order_intents(
        client, ledger, now=100 + 61, lookback_seconds=3600, abort_grace_seconds=60
    )

    assert result.aborted == 1
    assert result.still_pending == 0
    assert ledger.has_open_order_intent("cid-stale") is False


def test_reconcile_holds_intent_within_grace(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    _held_ambiguous_intent(ledger, "cid-young", token_id="up-1", window_end_ts=100)

    client = _FakeClient()  # nothing live yet

    result = reconcile_open_order_intents(
        client, ledger, now=100 + 10, lookback_seconds=3600, abort_grace_seconds=60
    )

    assert result.still_pending == 1
    assert result.aborted == 0
    # Still blocked until we know more.
    assert ledger.has_open_order_intent("cid-young") is True
    assert len(ledger.get_open_order_intents()) == 1


def test_reconcile_skips_successful_intent_with_submission(tmp_path):
    ledger = Ledger(filepath=str(tmp_path / "trades.jsonl"))
    # A normal *successful* order: IN_FLIGHT intent AND a submission carrying it.
    ledger.record_order_intent("cid-ok", window_end_ts=100, status="IN_FLIGHT",
                               details={"token_id": "up-1"})
    ledger.record_submission(
        "real-1", 100, direction="UP", amount_usdc=5.0, submitted_price=0.58,
        market_price=0.58, model="A", dry_run=False, client_order_id="cid-ok",
    )

    client = _FakeClient(
        trades=[{"order_id": "real-1", "asset_id": "up-1", "size": "8.0", "price": "0.58"}]
    )

    result = reconcile_open_order_intents(
        client, ledger, now=200, lookback_seconds=3600, abort_grace_seconds=60
    )

    assert result.reconciled_filled == 0
    assert result.cancelled == 0
    assert result.aborted == 0
    assert result.still_pending == 0
