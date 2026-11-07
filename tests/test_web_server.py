from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from webapp.manager import BotRunManager
from webapp.server import build_handler


def _start_server(tmp_path: Path, *, read_only: bool) -> ThreadingHTTPServer:
    manager = BotRunManager(tmp_path)
    runtime_dir = manager.runtime_dir
    status_file = runtime_dir / "status.json"
    status_file.write_text(
        json.dumps(
            {
                "generated_ts": time.time(),
                "mode": "DRY_RUN",
                "total_pnl": 1.25,
                "freshness": {
                    "btc_price_stale": False,
                    "btc_price_quorum_met": True,
                    "btc_healthy_source_count": 3,
                    "polymarket_book_stale": False,
                    "polymarket_book_idle": False,
                },
                "tracks": [],
            }
        ),
        encoding="utf-8",
    )
    manager._write_state(
        {
            "run_id": "run-1",
            "pid": os.getpid(),
            "started_ts": time.time(),
            "command": ["python3", "bot.py", "--shadow-live"],
            "config": {"mode": "shadow-live"},
            "ledger_file": str(runtime_dir / "run_trades_both.jsonl"),
            "ledger_file_conviction": str(runtime_dir / "run_trades_conviction.jsonl"),
            "ledger_file_mispricing": str(runtime_dir / "run_trades_mispricing.jsonl"),
            "status_file": str(status_file),
            "log_file": str(runtime_dir / "run.log"),
        }
    )
    static_dir = Path(__file__).resolve().parent.parent / "webapp" / "static"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        build_handler(manager, static_dir, read_only=read_only),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def test_healthz_reports_paper_mode_and_read_only(tmp_path):
    server = _start_server(tmp_path, read_only=True)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        payload = _get_json(base + "/healthz")
        assert payload["status"] == "ok"
        assert payload["paper_mode"] is True
        assert payload["mode"] in {"DRY_RUN", "SHADOW-LIVE"}
        assert payload["btc_healthy_source_count"] == 3
        assert payload["read_only"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_read_only_rejects_control_posts(tmp_path):
    server = _start_server(tmp_path, read_only=True)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = urllib.request.Request(
            base + "/api/start",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            assert "read-only" in json.load(exc)["error"]
        else:
            raise AssertionError("read-only server accepted a control post")
    finally:
        server.shutdown()
        server.server_close()
