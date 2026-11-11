"""Bankroll, trade P&L and the dashboard curve must agree, with entry and exit fees included."""
import pytest

from state.position import ExitReason
from state.session import BotState
from strategy.entry_helpers import estimate_trade_fee_usdc


def _entry(state, oid, amount=4.0, price=0.80, direction="UP"):
    state.record_trade(direction, amount, price, 0.05, "A", oid, True, 100)
    fee = estimate_trade_fee_usdc(asset="BTC", market_price=price, bet_size=amount)
    state.record_fee(oid, fee)
    return fee


def test_early_exit_with_partial_close_matches_bankroll():
    from state.position import Position

    state = BotState()
    start = state.current_bankroll_usdc()
    entry_fee = _entry(state, "o1")
    pos = Position(order_id="o1", window_end_ts=100, direction="UP", size_usdc=4.0, entry_price=0.80, entry_ts=0.0)
    shares_half = 2.0 / 0.80
    fee1 = estimate_trade_fee_usdc(asset="BTC", market_price=0.90, bet_size=0, shares=shares_half)
    r1 = pos.close(0.90, 1.0, ExitReason.TAKE_PROFIT, fee_usdc=fee1, partial_pct=0.5)
    state.realized_pnl += r1
    state.record_partial_exit("o1", r1)
    fee2 = estimate_trade_fee_usdc(asset="BTC", market_price=0.70, bet_size=0, shares=shares_half)
    r2 = pos.close(0.70, 2.0, ExitReason.STOP_LOSS, fee_usdc=fee2, partial_pct=1.0)
    state.realized_pnl += r2
    state.record_early_exit("o1", position_pnl=pos.pnl_usdc)

    trade = state.trade_history[0]
    expected = shares_half * 0.90 - 2.0 - fee1 + shares_half * 0.70 - 2.0 - fee2 - entry_fee
    assert trade.pnl == pytest.approx(expected)
    assert state.current_bankroll_usdc() - start == pytest.approx(trade.pnl)
    assert trade.status == ("WIN" if expected > 0 else "LOSS")


def test_settlement_charges_entry_fee_and_counts_partials():
    state = BotState()
    start = state.current_bankroll_usdc()
    entry_fee = _entry(state, "o2", amount=4.0, price=0.80)
    state.record_resolution("o2", resolved_up=True)
    trade = state.trade_history[0]
    assert trade.pnl == pytest.approx(4.0 * (1 / 0.80 - 1) - entry_fee)  # +1.00 minus fee
    assert state.current_bankroll_usdc() - start == pytest.approx(trade.pnl)


def test_dashboard_curve_and_total_use_trade_pnl():
    from ops.dashboard import _pnl_curve

    state = BotState()
    _entry(state, "a")
    _entry(state, "b", direction="DOWN")
    state.record_resolution("a", resolved_up=True)
    state.record_resolution("b", resolved_up=True)
    resolved = [t for t in state.trade_history if t.resolved]
    curve = _pnl_curve(resolved)
    assert curve[0] == 0.0
    assert curve[-1] == pytest.approx(state.realized_pnl, abs=1e-4)
    assert curve[-1] == pytest.approx(sum(t.pnl for t in resolved), abs=1e-4)


def test_restore_track_state_rebuilds_bankroll_from_ledger(tmp_path):
    from bot import restore_track_state
    from persistence.ledger import Ledger

    led = Ledger(str(tmp_path / "t.jsonl"))
    for oid in ("s", "e", "c", "o"):
        led.record_trade(window_end_ts=100, direction="UP", amount_usdc=4.0, price=0.80, edge=0.05, ev=0.0,
                         model="A", order_id=oid, dry_run=True, market_url="", market_slug="", asset="BTC",
                         open_price=0.0, role="PRIMARY", track="BOTH")
    led.record_resolution("s", 100, True, "UP", 4.0, 0.80)                       # old row: no fees_usdc
    led.record_early_exit("e", 100, pnl=-0.50, exit_price=0.70, reason="STOP_LOSS", fees_usdc=0.20)
    led.record_cancellation("c", 100)
    state = BotState()
    counts = restore_track_state(state, led, "BTC")

    entry_fee = estimate_trade_fee_usdc(asset="BTC", market_price=0.80, bet_size=4.0)
    by = {t.order_id: t for t in state.trade_history}
    assert by["s"].pnl == pytest.approx(1.0 - entry_fee) and by["s"].status == "WIN"
    assert by["e"].pnl == pytest.approx(-0.70) and by["e"].status == "LOSS"
    assert by["c"].status == "CANCELLED" and by["o"].status == "OPEN" and not by["o"].resolved
    assert counts == {"trades": 4, "resolved": 2, "cancelled": 1, "pending": 1}
    assert state.realized_pnl == pytest.approx(1.0 - entry_fee - 0.70)
    assert state.pending_exposure_usdc() == 4.0  # only the pending trade, until the sweep settles it


def test_market_tape_writes_one_row_per_tick_by_utc_day(tmp_path):
    import json

    from persistence.tape import MarketTape, tape_row
    from state.session import PriceObservation

    state = BotState()
    state.price_observations = {"COINBASE": PriceObservation("COINBASE", 73000.5, 0.0)}
    payload = {"generated_ts": 1794355200.0, "btc_price": 73000.5, "window_seconds_remaining": 42,
               "market": {"up_bid": 0.55, "up_ask": 0.56, "down_bid": 0.44, "down_ask": 0.45},
               "freshness": {"btc_price_age_seconds": 0.4, "polymarket_book_age_seconds": 1.1}}
    tape = MarketTape(str(tmp_path))
    tape.append(tape_row(payload, state))
    tape.append(tape_row(payload, state))
    rows = [json.loads(line) for line in (tmp_path / "2026-11-11.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["up_ask"] == 0.56 and rows[0]["btc_src"] == {"COINBASE": 73000.5} and rows[0]["secs_left"] == 42


def test_partial_close_then_settlement_agrees_across_state_ledger_restore(tmp_path):
    """Audit case: $4 at 0.80, half sold at 0.90, rest held to a win."""
    from bot import restore_track_state
    from persistence.ledger import Ledger, net_pnl
    from state.position import Position

    led = Ledger(str(tmp_path / "t.jsonl"))
    led.record_trade(window_end_ts=100, direction="UP", amount_usdc=4.0, price=0.80, edge=0.05, ev=0.0,
                     model="A", order_id="p", dry_run=True, asset="BTC", role="PRIMARY", track="BOTH")
    state = BotState()
    entry_fee = _entry(state, "p")
    pos = Position(order_id="p", window_end_ts=100, direction="UP", size_usdc=4.0, entry_price=0.80, entry_ts=0.0)
    fee1 = estimate_trade_fee_usdc(asset="BTC", market_price=0.90, bet_size=0, shares=2.5)
    r1 = pos.close(0.90, 1.0, ExitReason.TAKE_PROFIT, fee_usdc=fee1, partial_pct=0.5)
    state.realized_pnl += r1
    state.record_partial_exit("p", r1)
    state.update_trade_amount("p", pos.remaining_size_usdc)
    state.record_resolution("p", resolved_up=True)
    trade = state.trade_history[0]
    led.record_resolution("p", 100, True, "UP", trade.amount_usdc, 0.80,
                          fees_usdc=trade.fee_usdc, partial_net_usdc=trade.partial_pnl)

    expected = r1 + 2.0 * (1 / 0.80 - 1) - entry_fee
    assert trade.pnl == pytest.approx(expected)
    assert state.realized_pnl == pytest.approx(expected)
    assert net_pnl(led.get_all_resolutions()[0]) == pytest.approx(expected)
    restored = BotState()
    restore_track_state(restored, led, "BTC")
    assert restored.realized_pnl == pytest.approx(expected)
    assert restored.total_trades == 1


def test_net_loss_with_positive_gross_counts_as_loss():
    from persistence.ledger import net_pnl

    row = {"pnl": 0.05, "fees_usdc": 0.1099}  # sold 5 shares 0.80 -> 0.81
    assert net_pnl(row) < 0


def test_entry_gate_prices_expected_exit_fee():
    from strategy.entry_helpers import early_exit_share

    state = BotState()
    assert early_exit_share(state) == 1.0  # no history: price the full round trip
    for i in range(10):
        _entry(state, f"s{i}")
        if i < 4:
            state.record_resolution(f"s{i}", resolved_up=True)      # settled
        else:
            state.record_early_exit(f"s{i}", position_pnl=0.10)     # sold early
    assert early_exit_share(state) == pytest.approx(0.6)


def test_resolution_prefers_polymarket_over_binance():
    from ops.resolution import BINANCE_FALLBACK_AFTER_SECONDS, resolve_market_outcome

    class Client:
        result = {"resolved_up": False}
        def get_market_resolution(self, slug):
            return self.result

    binance = lambda asset, end: {"resolved_up": True}  # noqa: E731
    kw = dict(asset="BTC", market_slug="s", window_end_ts=1000, prefer_binance=True, resolve_with_binance_fn=binance)
    assert resolve_market_outcome(Client(), now_ts=1010, **kw) == {"resolved_up": False}
    Client.result = None  # official result not posted yet
    assert resolve_market_outcome(Client(), now_ts=1010, **kw) is None
    assert resolve_market_outcome(Client(), now_ts=1000 + BINANCE_FALLBACK_AFTER_SECONDS, **kw) == {"resolved_up": True}


def test_sweep_settles_remaining_stake_with_fees(tmp_path):
    from types import SimpleNamespace

    from ops.resolution import sweep_unresolved_trades_once
    from persistence.ledger import Ledger, net_pnl

    led = Ledger(str(tmp_path / "t.jsonl"))
    led.record_trade(window_end_ts=100, direction="UP", amount_usdc=4.0, price=0.80, edge=0.05, ev=0.0,
                     model="A", order_id="p", dry_run=True, asset="BTC", role="PRIMARY", track="BOTH",
                     market_slug="btc-updown-5m-0")
    state = BotState()
    entry_fee = _entry(state, "p")
    state.record_partial_exit("p", 0.2)
    state.update_trade_amount("p", 2.0)
    track = SimpleNamespace(ledger=led, state=state, model="A", stats=SimpleNamespace(note=lambda m: None))
    client = SimpleNamespace(get_market_resolution=lambda slug: {"resolved_up": True})
    n = sweep_unresolved_trades_once(track, client, target_asset="BTC", extract_market_slug_fn=lambda u: "",
                                     resolve_market_outcome_fn=lambda c, **k: c.get_market_resolution(k["market_slug"]),
                                     is_demo_client_fn=lambda c: False, now_ts=200)
    assert n == 1
    expected = 2.0 * (1 / 0.80 - 1) - entry_fee + 0.2
    assert net_pnl(led.get_all_resolutions()[0]) == pytest.approx(expected)
    assert state.trade_history[0].pnl == pytest.approx(expected)
