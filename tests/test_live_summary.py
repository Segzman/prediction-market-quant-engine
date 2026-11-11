import json

import pytest

from research.live_summary import build_summary, main, render_markdown, wilson_interval

NOW = 1_800_000_000.0
FORBIDDEN = (
    "order_id",
    "wallet",
    "address",
    "private",
    "secret",
    "passphrase",
    "0x",
    "/Users",
    "market_slug",
    "market_url",
)


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec) + "\n")


@pytest.fixture
def state(tmp_path):
    """Tiny paper run: 3 trades, 2 settled (one win, one loss), 1 open, plus JEV calls."""
    root = tmp_path / "state"
    trades = [
        {
            "type": "trade",
            "ts": NOW - 3600,
            "window_end_ts": 100,
            "direction": "UP",
            "amount_usdc": 5.0,
            "price": 0.60,
            "edge": 0.10,
            "order_id": "ord-secret-1",
            "market_slug": "btc-up-100",
            "market_url": "https://example.test/x",
        },
        {
            "type": "trade",
            "ts": NOW - 1800,
            "window_end_ts": 200,
            "direction": "DOWN",
            "amount_usdc": 5.0,
            "price": 0.50,
            "edge": 0.05,
            "order_id": "ord-secret-2",
        },
        {
            "type": "trade",
            "ts": NOW - 60,
            "window_end_ts": 300,
            "direction": "UP",
            "amount_usdc": 5.0,
            "price": 0.55,
            "edge": 0.04,
            "order_id": "ord-open",
        },
    ]
    resolutions = [
        # UP at 0.60, resolved UP: win, pnl = 5 * (1/0.6 - 1)
        {
            "type": "resolution",
            "ts": NOW - 3000,
            "order_id": "ord-secret-1",
            "window_end_ts": 100,
            "resolved_up": True,
            "pnl": 5 * (1 / 0.60 - 1),
        },
        # DOWN at 0.50, resolved UP: loss
        {
            "type": "resolution",
            "ts": NOW - 1000,
            "order_id": "ord-secret-2",
            "window_end_ts": 200,
            "resolved_up": True,
            "pnl": -5.0,
        },
    ]
    outcomes = [
        {"type": "window_outcome", "ts": NOW - 2900, "window_end_ts": 100, "resolved_up": True},
        {"type": "window_outcome", "ts": NOW - 900, "window_end_ts": 200, "resolved_up": True},
    ]
    _write_jsonl(root / "ledgers" / "both.jsonl", trades + resolutions + outcomes)
    _write_jsonl(
        root / "jev_calls.jsonl",
        [
            {"ts": NOW - 500, "api_call": True, "latency_ms": 120.0, "probability": 0.6},
            {"ts": NOW - 400, "api_call": True, "latency_ms": 480.0, "probability": 0.4},
            {"ts": NOW - 300, "api_call": False, "latency_ms": None},
        ],
    )
    status = {
        "generated_ts": NOW - 30,
        "mode": "SHADOW",
        "total_pnl": 0.0,
        "freshness": {
            "btc_price_quorum_met": True,
            "btc_price_stale": False,
            "polymarket_book_stale": False,
        },
    }
    (root / "runtime_status.json").write_text(json.dumps(status), encoding="utf-8")
    return root


def test_summary_fields_and_values(state):
    summary = build_summary(state, NOW)
    assert summary["updated_at"] == "2027-01-15T08:00:00Z"
    assert summary["paper_trades"] == 3
    assert summary["settled_trades"] == 2
    assert summary["windows_observed"] == 3
    assert summary["feeds_healthy"] is True
    assert summary["uptime"]["bot_up"] is True
    assert summary["uptime"]["heartbeat_age_seconds"] == 30.0
    assert summary["win_rate"]["wins"] == 1
    assert summary["win_rate"]["rate"] == 0.5
    low, high = summary["win_rate"]["wilson95"]
    assert 0 <= low < 0.5 < high <= 1
    gross = 5 * (1 / 0.60 - 1) - 5.0
    assert summary["net_pnl_usdc"]["gross"] == pytest.approx(gross, abs=1e-4)
    assert summary["net_pnl_usdc"]["net"] < summary["net_pnl_usdc"]["gross"]
    assert summary["net_pnl_usdc"]["bootstrap95"] is None  # fewer than 20 settled
    assert summary["brier"]["n"] == 2
    # Model P = price + edge; both trades resolve as the bot's side lost/won
    expected_model = ((0.70 - 1) ** 2 + (0.55 - 0) ** 2) / 2
    expected_market = ((0.60 - 1) ** 2 + (0.50 - 0) ** 2) / 2
    assert summary["brier"]["model"] == pytest.approx(expected_model, abs=1e-4)
    assert summary["brier"]["market_baseline"] == pytest.approx(expected_market, abs=1e-4)
    assert summary["jev"]["api_calls"] == 2
    assert summary["jev"]["latency_ms_p50"] == 300.0


def test_outputs_written_and_no_forbidden_fields(state, tmp_path):
    out = tmp_path / "status"
    assert main([str(state), "--out", str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "LIVE.md",
        "badge-pnl.json",
        "badge-trades.json",
        "badge-updated.json",
        "badge-uptime.json",
        "live.json",
    ]
    for name in names:
        text = (out / name).read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{token!r} leaked into {name}"

    live = json.loads((out / "live.json").read_text(encoding="utf-8"))
    assert live["jev"]["api_calls"] == 2
    for badge in ("pnl", "trades", "uptime", "updated"):
        payload = json.loads((out / f"badge-{badge}.json").read_text(encoding="utf-8"))
        assert payload["schemaVersion"] == 1
        assert set(payload) == {"schemaVersion", "label", "message", "color"}

    md = (out / "LIVE.md").read_text(encoding="utf-8")
    assert "Paper trading only" in md and "no claim of edge" in md.lower()


def test_bootstrap_ci_once_enough_trades(tmp_path):
    root = tmp_path / "state"
    records = []
    for i in range(25):
        records.append(
            {
                "type": "trade",
                "ts": NOW - 1000 + i,
                "window_end_ts": 1000 + i,
                "direction": "UP",
                "amount_usdc": 2.0,
                "price": 0.5,
                "edge": 0.1,
                "order_id": f"o{i}",
            }
        )
        records.append(
            {
                "type": "resolution",
                "ts": NOW - 500 + i,
                "order_id": f"o{i}",
                "window_end_ts": 1000 + i,
                "resolved_up": i % 2 == 0,
                "pnl": 1.0 if i % 2 == 0 else -2.0,
            }
        )
    _write_jsonl(root / "ledgers" / "both.jsonl", records)
    summary = build_summary(root, NOW)
    low, high = summary["net_pnl_usdc"]["bootstrap95"]
    assert low < high
    assert summary["settled_trades"] == 25


def test_refuses_to_publish_when_mode_is_live(state, tmp_path):
    status = json.loads((state / "runtime_status.json").read_text(encoding="utf-8"))
    status["mode"] = "LIVE"
    (state / "runtime_status.json").write_text(json.dumps(status), encoding="utf-8")
    assert main([str(state), "--out", str(tmp_path / "status")]) == 2
    assert not (tmp_path / "status").exists()


def test_empty_state_still_renders(tmp_path):
    out = tmp_path / "status"
    assert main([str(tmp_path / "empty"), "--out", str(out)]) == 0
    live = json.loads((out / "live.json").read_text(encoding="utf-8"))
    assert live["paper_trades"] == 0
    assert live["win_rate"]["rate"] is None
    assert live["feeds_healthy"] is False


def test_wilson_interval_bounds():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(5, 10)
    assert 0 < low < 0.5 < high < 1


def test_summary_splits_models(state):
    import shutil

    shutil.copy(state / "ledgers" / "both.jsonl", state / "ledgers" / "conviction.jsonl")
    summary = build_summary(state, NOW)
    both, conv = summary["tracks"]["both"], summary["tracks"]["conviction"]
    assert both == conv and both["paper_trades"] == 3 and both["settled_trades"] == 2
    assert summary["settled_trades"] == 4
    assert summary["net_pnl_usdc"]["net"] == pytest.approx(2 * both["net_pnl_usdc"], abs=1e-3)
    md = render_markdown(summary)
    assert "## By model" in md and "| BOTH |" in md and "| CONVICTION |" in md
