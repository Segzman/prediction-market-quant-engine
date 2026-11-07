from __future__ import annotations

import json

import pytest

from ops.alerts import AlertManager
from ops.lock import process_lock


def test_alert_manager_writes_jsonl(tmp_path):
    alerts_file = tmp_path / "alerts.jsonl"
    manager = AlertManager(str(alerts_file))
    manager.emit("critical", "Test alert", asset="BTC", count=2)

    lines = alerts_file.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])
    assert payload["level"] == "CRITICAL"
    assert payload["message"] == "Test alert"
    assert payload["context"]["asset"] == "BTC"


def _failing_then_ok(fail_times):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise OSError("connection refused")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp()

    return calls, fake_urlopen


def test_webhook_retries_then_succeeds(tmp_path, monkeypatch):
    import ops.alerts as alerts_mod

    calls, fake = _failing_then_ok(2)
    monkeypatch.setattr(alerts_mod.urllib.request, "urlopen", fake)
    manager = AlertManager(str(tmp_path / "a.jsonl"), webhook_url="https://example.test/hook")

    ok = manager._send_webhook({"level": "CRITICAL", "message": "x"}, backoff=0.0)

    assert ok is True
    assert calls["n"] == 3


def test_webhook_failure_does_not_raise_and_keeps_local_record(tmp_path, monkeypatch):
    import ops.alerts as alerts_mod

    calls, fake = _failing_then_ok(99)  # always fail
    monkeypatch.setattr(alerts_mod.urllib.request, "urlopen", fake)
    monkeypatch.setattr(alerts_mod.time, "sleep", lambda *_: None)
    alerts_file = tmp_path / "a.jsonl"
    manager = AlertManager(str(alerts_file), webhook_url="https://example.test/hook")

    payload = manager.emit("critical", "drawdown breach")  # must not raise

    assert payload["message"] == "drawdown breach"
    # The local record must survive even when the webhook is unreachable.
    lines = alerts_file.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["message"] == "drawdown breach"
    # Retried up to the default max attempts before giving up.
    assert calls["n"] == 3


def test_process_lock_rejects_second_holder(tmp_path):
    lock_path = tmp_path / "bot.lock"
    with process_lock(str(lock_path)):
        with pytest.raises(RuntimeError, match="holds the lock"):
            with process_lock(str(lock_path)):
                pass
