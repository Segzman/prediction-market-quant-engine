import os

from persistence.ledger import Ledger


def test_trade_written_to_file(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="abc123",
        dry_run=True,
        feature_snapshot={"move_pct_bin": "<=0.0000", "time_bin": "30-60"},
        execution_snapshot={"role": "PRIMARY"},
    )
    trades = tmp_ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["direction"] == "UP"
    assert trades[0]["order_id"] == "abc123"
    assert trades[0]["feature_snapshot"]["time_bin"] == "30-60"
    assert trades[0]["execution_snapshot"]["role"] == "PRIMARY"


def test_resolution_written_to_file(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="abc123",
        dry_run=True,
    )
    tmp_ledger.record_resolution(
        "abc123",
        1743612600,
        resolved_up=True,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        feature_snapshot={"move_pct_bin": ">0.0010", "time_bin": "0-30"},
    )
    resolutions = tmp_ledger.get_all_resolutions()
    assert len(resolutions) == 1
    assert resolutions[0]["resolved_up"] is True
    assert resolutions[0]["pnl"] > 0
    assert resolutions[0]["move_pct_bin"] == ">0.0010"


def test_resolution_pnl_negative_on_loss(tmp_ledger):
    tmp_ledger.record_trade(
        window_end_ts=1743612600,
        direction="UP",
        amount_usdc=5.0,
        price=0.58,
        edge=0.07,
        ev=0.42,
        model="A",
        order_id="abc123",
        dry_run=True,
    )
    tmp_ledger.record_resolution(
        "abc123", 1743612600, resolved_up=False, direction="UP", amount_usdc=5.0, price=0.58
    )
    resolutions = tmp_ledger.get_all_resolutions()
    assert resolutions[0]["pnl"] == -5.0


def test_daily_summary_correct(tmp_ledger):
    for i, (direction, resolved_up) in enumerate(
        [("UP", True), ("DOWN", False), ("UP", False)]
    ):
        oid = f"order-{i}"
        ts = 1743612600 + i * 300
        tmp_ledger.record_trade(ts, direction, 5.0, 0.58, 0.07, 0.42, "A", oid, True)
        tmp_ledger.record_resolution(oid, ts, resolved_up, direction, 5.0, 0.58)

    summary = tmp_ledger.get_daily_summary()
    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert abs(summary["win_rate"] - 0.667) < 0.01


def test_file_created_if_not_exists(tmp_path):
    path = str(tmp_path / "new_trades.jsonl")
    Ledger(filepath=path)
    assert os.path.exists(path)


def test_multiple_trades_all_returned(tmp_ledger):
    for i in range(5):
        tmp_ledger.record_trade(
            1743612600 + i * 300, "UP", 5.0, 0.58, 0.07, 0.42, "A", f"order-{i}", True
        )
    assert len(tmp_ledger.get_all_trades()) == 5


def test_hedge_trade_not_counted_as_daily_primary_trade(tmp_ledger):
    ts = 1743612600
    tmp_ledger.record_trade(ts, "UP", 5.0, 0.58, 0.07, 0.42, "A", "primary", True, role="PRIMARY")
    tmp_ledger.record_trade(ts, "DOWN", 1.75, 0.42, -0.02, -0.08, "A", "hedge", True, role="HEDGE")
    tmp_ledger.record_resolution("primary", ts, True, "UP", 5.0, 0.58)
    tmp_ledger.record_resolution("hedge", ts, True, "DOWN", 1.75, 0.42)

    summary = tmp_ledger.get_daily_summary()
    assert summary["total_trades"] == 1
    assert summary["hedge_trades"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0


def test_cancellation_closes_trade_without_counting_as_loss(tmp_ledger):
    ts = 1743612600
    tmp_ledger.record_trade(ts, "UP", 5.0, 0.58, 0.07, 0.42, "A", "primary", True)
    tmp_ledger.record_cancellation("primary", ts)

    summary = tmp_ledger.get_daily_summary()
    assert summary["total_trades"] == 1
    assert summary["cancelled_trades"] == 1
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert tmp_ledger.get_unresolved_trades() == []


def test_has_terminal_record_detects_resolution_and_cancellation(tmp_ledger):
    ts = 1743612600
    tmp_ledger.record_trade(ts, "UP", 5.0, 0.58, 0.07, 0.42, "A", "resolved", True)
    tmp_ledger.record_trade(ts, "UP", 5.0, 0.58, 0.07, 0.42, "A", "cancelled", True)
    tmp_ledger.record_resolution("resolved", ts, True, "UP", 5.0, 0.58)
    tmp_ledger.record_cancellation("cancelled", ts)

    assert tmp_ledger.has_terminal_record("resolved") is True
    assert tmp_ledger.has_terminal_record("cancelled") is True
    assert tmp_ledger.has_terminal_record("missing") is False


def test_fill_event_is_recorded(tmp_ledger):
    ts = 1743612600
    tmp_ledger.record_fill(
        "order-1",
        ts,
        "FILLED",
        filled_size=8.62,
        avg_price=0.58,
        details={"planned_amount_usdc": 5.0},
    )
    fills = tmp_ledger.get_all_fills()
    assert len(fills) == 1
    assert fills[0]["status"] == "FILLED"
    assert fills[0]["avg_price"] == 0.58
    assert fills[0]["details"]["planned_amount_usdc"] == 5.0


def test_submission_event_is_recorded(tmp_ledger):
    ts = 1743612600
    tmp_ledger.record_submission(
        "order-1",
        ts,
        direction="UP",
        amount_usdc=5.0,
        submitted_price=0.58,
        market_price=0.57,
        model="A",
        dry_run=False,
        track="BOTH",
    )
    submissions = tmp_ledger.get_all_submissions()
    assert len(submissions) == 1
    assert submissions[0]["order_id"] == "order-1"
    assert submissions[0]["submitted_price"] == 0.58
    assert submissions[0]["track"] == "BOTH"


def test_candidate_event_is_recorded(tmp_ledger):
    tmp_ledger.record_candidate(
        window_end_ts=1743612600,
        track="MISPRICING_ONLY",
        model="B",
        direction="DOWN",
        regime_candidate="MISPRICING",
        reason_code="NET_PROFIT_TOO_LOW",
        reason_detail="scaled floor blocked entry",
        would_trade=False,
        skip_reason="NET_PROFIT_TOO_LOW",
        market_price=0.41,
        up_price=0.59,
        down_price=0.41,
        liquidity_score=0.82,
        book_spread=0.04,
        probabilities={"A": 0.61, "B": 0.38, "C": 0.57},
    )

    candidates = tmp_ledger.get_all_candidates()
    assert len(candidates) == 1
    assert candidates[0]["track"] == "MISPRICING_ONLY"
    assert candidates[0]["reason_code"] == "NET_PROFIT_TOO_LOW"
    assert candidates[0]["skip_reason"] == "NET_PROFIT_TOO_LOW"
    assert candidates[0]["probabilities"]["B"] == 0.38


def test_unknown_client_order_id_has_no_open_intent(tmp_ledger):
    assert tmp_ledger.has_open_order_intent("never-seen") is False


def test_in_flight_intent_blocks_then_abort_frees(tmp_ledger):
    cid = "cid-abc"
    tmp_ledger.record_order_intent(cid, window_end_ts=300, status="IN_FLIGHT")
    assert tmp_ledger.has_open_order_intent(cid) is True
    # A safe (pre-network) rejection aborts the intent, freeing the id for retry.
    tmp_ledger.record_order_intent(cid, window_end_ts=300, status="ABORTED")
    assert tmp_ledger.has_open_order_intent(cid) is False


def test_in_flight_intent_stays_open_after_successful_submission(tmp_ledger):
    # A successful submit must NOT free the id (would allow a duplicate).
    cid = "cid-live"
    tmp_ledger.record_order_intent(cid, window_end_ts=300, status="IN_FLIGHT")
    tmp_ledger.record_submission(
        "exchange-order-1",
        300,
        direction="UP",
        amount_usdc=5.0,
        submitted_price=0.58,
        market_price=0.58,
        model="A",
        dry_run=False,
        client_order_id=cid,
    )
    assert tmp_ledger.has_open_order_intent(cid) is True


def test_window_outcome_round_trip_and_dedup(tmp_ledger):
    assert tmp_ledger.has_window_outcome(1743612600) is False
    tmp_ledger.record_window_outcome(1743612600, True, market_slug="btc-updown-5m-1")
    tmp_ledger.record_window_outcome(1743612900, False)
    assert tmp_ledger.has_window_outcome(1743612600) is True
    outcomes = tmp_ledger.get_all_window_outcomes()
    assert len(outcomes) == 2
    assert outcomes[0]["resolved_up"] is True
    # Never counted as trades or resolutions.
    assert tmp_ledger.get_all_trades() == []
    assert tmp_ledger.get_all_resolutions() == []
    assert tmp_ledger.get_daily_summary()["total_trades"] == 0


def test_ledger_reads_incrementally_and_skips_candidates(tmp_path):
    from persistence.ledger import Ledger

    p = tmp_path / "l.jsonl"
    led = Ledger(str(p))
    led.record_window_outcome(100, True, market_slug="s")
    p.open("a").write('{"type": "candidate", "ts": 1}\n')
    assert len(led.get_all_window_outcomes()) == 1
    p.open("a").write('{"type": "window_outcome", "window_end_ts": 200')  # half-written line
    assert len(led.get_all_window_outcomes()) == 1
    p.open("a").write(', "resolved_up": false}\n')
    assert [o["window_end_ts"] for o in led.get_all_window_outcomes()] == [100, 200]
    assert len(led.get_all_candidates()) == 1
    assert all(r.get("type") != "candidate" for r in led._read_records())
    p.write_text("")  # replaced file resets the cache
    assert led.get_all_window_outcomes() == []
