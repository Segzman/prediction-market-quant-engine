"""Tests for the JEV decision log. All network access is faked."""

from __future__ import annotations

import json

import strategy.jev as jev
from strategy.probability import ModelContext


def _context(**overrides) -> ModelContext:
    values = {
        "strongest_side": "UP",
        "btc_poly_agreement": -1.0,
        "tick_move_pct": 0.001,
        "directional_consistency": 0.7,
        "acceleration_pct": 0.0,
        "up_price_momentum": 0.01,
        "down_price_momentum": 0.0,
    }
    values.update(overrides)
    return ModelContext(**values)


def _records(path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fake_urlopen(body: dict):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(body).encode()

    def _open(req, timeout=None):
        return _Resp()

    return _open


def _enable(monkeypatch, log_path, *, cost: float = 0.0) -> None:
    monkeypatch.setattr(jev, "JEV_LOG_FILE", str(log_path))
    monkeypatch.setattr(jev, "JEV_COST_PER_CALL_USD", cost)
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", True)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    jev._cache.clear()


def test_logs_api_call_with_latency_and_cost(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    body = {"answers": {"btc_poly_agreement": {"noul": 0.82}}}
    _enable(monkeypatch, log_path, cost=0.002)
    monkeypatch.setattr(jev.urllib.request, "urlopen", _fake_urlopen(body))

    decision = jev.resolve_btc_confirmation(
        _context(),
        seconds_remaining=60,
        market_price=0.85,
        window_id="condition-1",
        window_end_ts=12345,
    )

    assert decision is True
    records = _records(log_path)
    assert len(records) == 1
    record = records[0]
    assert record["window_id"] == "condition-1"
    assert record["window_end_ts"] == 12345
    assert record["seconds_remaining"] == 60
    assert record["market_price"] == 0.85
    assert record["strongest_side"] == "UP"
    assert record["features"]["tick_move_pct"] == 0.001
    assert record["api_call"] is True
    assert record["skip_reason"] is None
    assert record["cache_hit"] is False
    assert isinstance(record["latency_ms"], (int, float))
    assert record["latency_ms"] >= 0
    assert record["probability"] == 0.82
    assert record["error_class"] is None
    assert record["deterministic_decision"] is False
    assert record["jev_decision"] is True
    assert record["final_decision"] is True
    assert record["cost_per_call_usd"] == 0.002
    assert record["estimated_cost_usd"] == 0.002


def test_logs_cache_hit_without_http_call(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    body = {"answers": {"btc_poly_agreement": {"noul": 0.82}}}
    _enable(monkeypatch, log_path)
    monkeypatch.setattr(jev.urllib.request, "urlopen", _fake_urlopen(body))

    context = _context()
    assert jev.resolve_btc_confirmation(
        context, seconds_remaining=60, market_price=0.85
    ) is True
    assert jev.resolve_btc_confirmation(
        context, seconds_remaining=60, market_price=0.85
    ) is True

    records = _records(log_path)
    assert len(records) == 2
    assert records[0]["api_call"] is True
    assert records[0]["cache_hit"] is False
    assert records[1]["api_call"] is False
    assert records[1]["cache_hit"] is True
    assert records[1]["latency_ms"] is None
    assert records[1]["probability"] == 0.82


def test_failure_still_fails_closed_and_logs_error(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    _enable(monkeypatch, log_path)

    def _boom(req, timeout=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(jev.urllib.request, "urlopen", _boom)

    decision = jev.resolve_btc_confirmation(
        _context(btc_poly_agreement=1.0),
        seconds_remaining=60,
        market_price=0.85,
    )

    assert decision is None
    record = _records(log_path)[0]
    assert record["api_call"] is True
    assert record["error_class"] == "TimeoutError"
    assert record["probability"] is None
    assert record["jev_decision"] is None
    assert record["deterministic_decision"] is True
    assert record["final_decision"] is True


def test_logging_error_does_not_break_decision(monkeypatch, tmp_path):
    body = {"answers": {"btc_poly_agreement": {"noul": 0.82}}}
    _enable(monkeypatch, tmp_path / "jev_calls.jsonl")
    monkeypatch.setattr(jev.urllib.request, "urlopen", _fake_urlopen(body))
    monkeypatch.setattr(
        jev,
        "_log_decision",
        lambda record: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert jev.resolve_btc_confirmation(
        _context(), seconds_remaining=60, market_price=0.85
    ) is True


def test_unwritable_log_path_does_not_break_decision(monkeypatch, tmp_path):
    body = {"answers": {"btc_poly_agreement": {"noul": 0.82}}}
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(jev.urllib.request, "urlopen", _fake_urlopen(body))

    assert jev.resolve_btc_confirmation(
        _context(), seconds_remaining=60, market_price=0.85
    ) is True


def test_logs_disabled_skip(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    monkeypatch.setattr(jev, "JEV_LOG_FILE", str(log_path))
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", False)

    assert jev.resolve_btc_confirmation(
        _context(), seconds_remaining=60, market_price=0.85
    ) is None

    record = _records(log_path)[0]
    assert record["api_call"] is False
    assert record["skip_reason"] == "disabled"
    assert record["jev_decision"] is None
    assert record["final_decision"] is False


def test_logs_no_key_skip(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    monkeypatch.setattr(jev, "JEV_LOG_FILE", str(log_path))
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", True)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    assert jev.resolve_btc_confirmation(
        _context(), seconds_remaining=60, market_price=0.85
    ) is None

    record = _records(log_path)[0]
    assert record["api_call"] is False
    assert record["skip_reason"] == "no_key"


def test_logs_cheap_and_agreed_skip(monkeypatch, tmp_path):
    log_path = tmp_path / "jev_calls.jsonl"
    monkeypatch.setattr(jev, "JEV_LOG_FILE", str(log_path))
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", True)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")

    def _forbidden(*args, **kwargs):
        raise AssertionError("API must not be called")

    monkeypatch.setattr(jev, "query_btc_agreement", _forbidden)

    assert jev.resolve_btc_confirmation(
        _context(btc_poly_agreement=1.0),
        seconds_remaining=60,
        market_price=0.60,
    ) is None

    record = _records(log_path)[0]
    assert record["api_call"] is False
    assert record["skip_reason"] == "cheap_and_agreed"
    assert record["deterministic_decision"] is True
    assert record["final_decision"] is True
