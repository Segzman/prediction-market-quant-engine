from __future__ import annotations

import json

import pytest

from webapp.manager import BotRunManager, RunConfig


def test_build_command_for_shadow_live_uses_models_and_no_dashboard(tmp_path):
    manager = BotRunManager(tmp_path)
    command = manager.build_command(
        RunConfig(
            mode="shadow-live",
            asset="ETH",
            models=("A", "B", "C"),
            bankroll=12.5,
            duration=300,
            debug=True,
        )
    )

    assert command[1] == "bot.py"
    assert "--shadow-live" in command
    assert "--models" in command
    assert "A,B,C" in command
    assert "--no-dashboard" in command
    assert "--debug" in command
    assert "--duration" in command


def test_build_command_for_live_adds_noninteractive_confirmation(tmp_path):
    manager = BotRunManager(tmp_path)
    command = manager.build_command(
        RunConfig(mode="live", asset="BTC", models=("A",), bankroll=10.0)
    )

    assert "--live" in command
    assert "--yes-live" in command


def test_build_command_rejects_multi_model_live(tmp_path):
    manager = BotRunManager(tmp_path)
    with pytest.raises(ValueError, match="one model"):
        manager.build_command(
            RunConfig(mode="live", asset="BTC", models=("A", "B"), bankroll=10.0)
        )


def test_current_run_loads_status_report_and_logs(tmp_path):
    manager = BotRunManager(tmp_path)
    ledger_file = tmp_path / "run.jsonl"
    status_file = tmp_path / "status.json"
    log_file = tmp_path / "run.log"

    ledger_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "trade", "order_id": "o-1", "model": "A", "role": "PRIMARY"}),
                json.dumps(
                    {
                        "type": "fill",
                        "order_id": "o-1",
                        "details": {
                            "estimated_fee_usdc": 0.11,
                            "fill_vs_signal_bps": 7.0,
                            "submitted_vs_signal_bps": 4.0,
                        },
                    }
                ),
                json.dumps({"type": "resolution", "order_id": "o-1", "pnl": 1.3}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    status_file.write_text(
        json.dumps(
            {
                "mode": "SHADOW LIVE",
                "asset": "BTC",
                "market": {"slug": "btc-updown-5m-1", "phase": "ENTRY_WINDOW"},
                "models": [{"model": "A", "total_trades": 1}],
            }
        ),
        encoding="utf-8",
    )
    log_file.write_text("line-1\nline-2\n", encoding="utf-8")
    manager._write_state(
        {
            "run_id": "run-1",
            "pid": 999999,
            "started_ts": 1.0,
            "command": ["python3", "bot.py", "--shadow-live"],
            "config": {
                "mode": "shadow-live",
                "asset": "BTC",
                "models": ["A"],
                "bankroll": 10.0,
                "duration": 60,
                "debug": False,
            },
            "ledger_file": str(ledger_file),
            "status_file": str(status_file),
            "log_file": str(log_file),
        }
    )

    status = manager.current_run()

    assert status["configured"] is True
    assert status["running"] is False
    assert status["status"]["market"]["slug"] == "btc-updown-5m-1"
    assert status["report"]["rows"]["A"]["net_pnl"] == pytest.approx(1.19)
    assert "BOTH" in status["report"]["tracks"]
    assert status["report"]["tracks"]["BOTH"]["rows"]["A"]["net_pnl"] == pytest.approx(1.19)
    assert status["report"]["comparison"] == {}
    assert status["report"]["fee_context"]["market_class"] == "crypto"
    assert status["logs"] == ["line-1", "line-2"]


def test_current_run_discovers_external_active_run_when_saved_state_is_stale(tmp_path, monkeypatch):
    manager = BotRunManager(tmp_path)
    runtime_dir = manager.runtime_dir
    run_id = "20260424_230322"
    status_file = runtime_dir / f"{run_id}_status.json"
    log_file = runtime_dir / f"{run_id}.log"
    ledger_file = runtime_dir / f"{run_id}_trades_both.jsonl"
    conviction_file = runtime_dir / f"{run_id}_trades_conviction.jsonl"
    mispricing_file = runtime_dir / f"{run_id}_trades_mispricing.jsonl"

    status_file.write_text(
        json.dumps(
            {
                "generated_ts": 1000.0,
                "mode": "DRY_RUN",
                "market": {"slug": "btc-updown-5m-1"},
                "tracks": [],
            }
        ),
        encoding="utf-8",
    )
    log_file.write_text("live-line\n", encoding="utf-8")
    ledger_file.write_text("", encoding="utf-8")
    conviction_file.write_text("", encoding="utf-8")
    mispricing_file.write_text("", encoding="utf-8")

    manager._write_state(
        {
            "run_id": "stale-run",
            "pid": 999999,
            "started_ts": 1.0,
            "command": ["python3", "bot.py"],
            "config": {"mode": "shadow-live"},
            "ledger_file": str(runtime_dir / "stale_trades_both.jsonl"),
            "ledger_file_conviction": str(runtime_dir / "stale_trades_conviction.jsonl"),
            "ledger_file_mispricing": str(runtime_dir / "stale_trades_mispricing.jsonl"),
            "status_file": str(runtime_dir / "stale_status.json"),
            "log_file": str(runtime_dir / "stale.log"),
        }
    )

    monkeypatch.setattr(manager, "_pid_running", lambda pid: pid == 1234)
    monkeypatch.setattr(manager, "_lock_holder_pid", lambda: 1234)
    monkeypatch.setattr("webapp.manager.time.time", lambda: 1010.0)

    status = manager.current_run()

    assert status["configured"] is True
    assert status["running"] is True
    assert status["pid"] == 1234
    assert status["paths"]["status_file"] == str(status_file)
    assert status["paths"]["log_file"] == str(log_file)
    assert status["status"]["market"]["slug"] == "btc-updown-5m-1"
    assert status["logs"] == ["live-line"]


def test_current_run_enriches_status_from_ledger_candidates_and_trades(tmp_path):
    manager = BotRunManager(tmp_path)
    runtime_dir = manager.runtime_dir
    status_file = runtime_dir / "run_status.json"
    log_file = runtime_dir / "run.log"
    conviction_file = runtime_dir / "run_trades_conviction.jsonl"
    mispricing_file = runtime_dir / "run_trades_mispricing.jsonl"
    both_file = runtime_dir / "run_trades_both.jsonl"

    status_file.write_text(
        json.dumps(
            {
                "engine_scores": {},
                "tracks": [
                    {
                        "name": "CONVICTION_ONLY",
                        "signal": None,
                        "all_trades": [
                            {
                                "ts": 100,
                                "direction": "UP",
                                "price": 0.81,
                                "amount": 4.05,
                                "regime": "",
                            }
                        ],
                    },
                    {"name": "MISPRICING_ONLY", "signal": None, "all_trades": []},
                    {"name": "BOTH", "signal": None, "all_trades": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    log_file.write_text("line-1\n", encoding="utf-8")
    conviction_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "candidate",
                        "track": "CONVICTION_ONLY",
                        "model": "A",
                        "ts": 99.5,
                        "direction": "UP",
                        "confidence": 0.72,
                        "entry_regime": "LATE_CONVICTION",
                        "would_trade": True,
                        "probabilities": {"A": 0.61, "B": 0.66, "C": 0.68, "P": 0.7},
                        "window_end_ts": 300,
                    }
                ),
                json.dumps(
                    {
                        "type": "trade",
                        "track": "CONVICTION_ONLY",
                        "model": "A",
                        "ts": 100.2,
                        "direction": "UP",
                        "price": 0.81,
                        "amount_usdc": 4.05,
                        "window_end_ts": 300,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mispricing_file.write_text("", encoding="utf-8")
    both_file.write_text("", encoding="utf-8")
    manager._write_state(
        {
            "run_id": "run-1",
            "pid": 999999,
            "started_ts": 1.0,
            "command": ["python3", "bot.py", "--shadow-live"],
            "config": {"mode": "shadow-live"},
            "ledger_file": str(both_file),
            "ledger_file_conviction": str(conviction_file),
            "ledger_file_mispricing": str(mispricing_file),
            "status_file": str(status_file),
            "log_file": str(log_file),
        }
    )

    status = manager.current_run()

    assert status["status"]["engine_scores"] == {
        "A": pytest.approx(0.61),
        "B": pytest.approx(0.66),
        "C": pytest.approx(0.68),
        "P": pytest.approx(0.7),
    }
    conviction_track = status["status"]["tracks"][0]
    assert conviction_track["signal"] == {
        "direction": "UP",
        "regime": "LATE_CONVICTION",
        "confidence": pytest.approx(0.72),
    }
    assert conviction_track["all_trades"][0]["regime"] == "LATE_CONVICTION"
