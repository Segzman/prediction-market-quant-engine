"""Pilot tests for strategy/jev.py. All network access is faked."""

from __future__ import annotations

import json
from contextlib import contextmanager

import strategy.jev as jev
from strategy.probability import ModelContext


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


@contextmanager
def _patched(monkeypatch, body: dict | None, key: str = "k"):
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    if body is None:
        def _boom(req, timeout=None):
            raise TimeoutError("slow")

        monkeypatch.setattr(jev.urllib.request, "urlopen", _boom)
    else:
        monkeypatch.setattr(jev.urllib.request, "urlopen", _fake_urlopen(body))
    jev._cache.clear()
    yield
    jev._cache.clear()


def test_query_parses_noul_field(monkeypatch):
    body = {"answers": {"btc_poly_agreement": {"type": "noul", "noul": 0.82}}}
    with _patched(monkeypatch, body):
        prob = jev.query_btc_agreement(
            strongest_side="UP",
            seconds_remaining=60,
            tick_move_pct=0.001,
            directional_consistency=0.7,
            acceleration_pct=0.0,
            up_price_momentum=0.01,
            down_price_momentum=0.0,
        )
    assert prob == 0.82


def test_query_falls_back_to_probability_field(monkeypatch):
    body = {"answers": {"btc_poly_agreement": {"probability": 0.3}}}
    with _patched(monkeypatch, body):
        prob = jev.query_btc_agreement(
            strongest_side="DOWN",
            seconds_remaining=60,
            tick_move_pct=-0.001,
            directional_consistency=0.7,
            acceleration_pct=0.0,
            up_price_momentum=0.0,
            down_price_momentum=0.01,
        )
    assert prob == 0.3


def test_query_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev._cache.clear()
    assert jev.query_btc_agreement(
        strongest_side="UP",
        seconds_remaining=60,
        tick_move_pct=0.0,
        directional_consistency=0.5,
        acceleration_pct=0.0,
        up_price_momentum=0.0,
        down_price_momentum=0.0,
    ) is None


def test_query_returns_none_on_transport_error(monkeypatch):
    with _patched(monkeypatch, None):
        assert jev.query_btc_agreement(
            strongest_side="UP",
            seconds_remaining=60,
            tick_move_pct=0.0,
            directional_consistency=0.5,
            acceleration_pct=0.0,
            up_price_momentum=0.0,
            down_price_momentum=0.0,
        ) is None


def test_resolve_skips_api_for_cheap_confirmed_entries(monkeypatch):
    ctx = ModelContext(strongest_side="UP", btc_poly_agreement=1.0)
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", True)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")

    def _forbidden(*args, **kwargs):
        raise AssertionError("API must not be called")

    monkeypatch.setattr(jev, "query_btc_agreement", _forbidden)
    assert jev.resolve_btc_confirmation(ctx, seconds_remaining=60, market_price=0.60) is None


def test_resolve_applies_threshold(monkeypatch):
    ctx = ModelContext(strongest_side="UP", btc_poly_agreement=-1.0)
    monkeypatch.setattr(jev, "JEV_NOUL_ENABLED", True)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setattr(jev, "query_btc_agreement", lambda **kwargs: 0.75)
    monkeypatch.setattr(jev, "JEV_NOUL_MIN_PROBABILITY", 0.60)
    assert jev.resolve_btc_confirmation(ctx, seconds_remaining=60, market_price=0.85) is True
    monkeypatch.setattr(jev, "JEV_NOUL_MIN_PROBABILITY", 0.80)
    assert jev.resolve_btc_confirmation(ctx, seconds_remaining=60, market_price=0.85) is False
