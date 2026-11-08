"""Tests for research/jev_report.py aggregation on a tiny fixture."""

from __future__ import annotations

import json

from research.jev_report import build_report, load_outcomes, render_markdown


def _record(**overrides) -> dict:
    record = {
        "ts": 1000.0,
        "window_id": "w",
        "window_end_ts": 100,
        "seconds_remaining": 60,
        "market_price": 0.85,
        "strongest_side": "UP",
        "features": {},
        "api_call": True,
        "skip_reason": None,
        "cache_hit": False,
        "latency_ms": 100.0,
        "probability": 0.8,
        "error_class": None,
        "deterministic_decision": False,
        "jev_decision": True,
        "final_decision": True,
        "cost_per_call_usd": 0.002,
        "estimated_cost_usd": 0.002,
    }
    record.update(overrides)
    return record


def _fixture() -> list[dict]:
    return [
        _record(ts=1000.0, window_end_ts=100, strongest_side="UP"),
        _record(
            ts=1010.0,
            window_end_ts=200,
            strongest_side="DOWN",
            latency_ms=200.0,
            probability=None,
            error_class="TimeoutError",
            jev_decision=None,
            final_decision=False,
        ),
        _record(
            ts=1020.0,
            window_end_ts=100,
            strongest_side="UP",
            api_call=False,
            skip_reason="cheap_and_agreed",
            latency_ms=None,
            probability=None,
            deterministic_decision=True,
            jev_decision=None,
            final_decision=True,
            estimated_cost_usd=0.0,
        ),
        _record(
            ts=1030.0,
            window_end_ts=300,
            strongest_side="UP",
            api_call=False,
            cache_hit=True,
            latency_ms=None,
            probability=0.8,
            deterministic_decision=True,
            jev_decision=True,
            final_decision=True,
            estimated_cost_usd=0.0,
        ),
        _record(
            ts=4000.0,
            window_end_ts=200,
            strongest_side="DOWN",
            api_call=False,
            skip_reason="disabled",
            latency_ms=None,
            probability=None,
            deterministic_decision=True,
            jev_decision=None,
            final_decision=True,
            estimated_cost_usd=0.0,
        ),
    ]


def test_build_report_counts_and_rates():
    outcomes = {100: True, 200: False, 300: True}
    report = build_report(_fixture(), outcomes)

    assert report["total_decisions"] == 5
    assert report["api_calls"] == 2
    assert report["cache_hits"] == 1
    assert report["cache_hit_rate"] == 1 / 3
    assert report["skipped_by_reason"] == {"cheap_and_agreed": 1, "disabled": 1}
    assert report["latency_p50_ms"] == 150.0
    assert report["latency_p95_ms"] == 195.0
    assert report["latency_max_ms"] == 200.0
    assert report["errors"] == 1
    assert report["error_rate"] == 0.5
    assert report["jev_decisions"] == 2
    assert report["agreement_rate"] == 0.5
    assert report["changed_decisions"] == 1
    assert report["total_cost_usd"] == 0.004
    assert report["sessions"] == 2
    assert report["cost_per_session_usd"] == 0.002
    assert report["joined_outcomes"] == 5
    assert report["jev_confirmed"] == {"n": 2, "correct": 2}
    assert report["deterministic_only"] == {"n": 2, "correct": 2}
    assert report["small_sample"] is True


def test_render_markdown_includes_sections_and_warning():
    report = build_report(_fixture(), {100: True, 200: False, 300: True})
    text = render_markdown(report, log_path="jev_calls.jsonl", ledger_path="trades.jsonl")

    assert "# JEV Experiment Report" in text
    assert "API calls made | 2" in text
    assert "Cache hit rate | 33.3%" in text
    assert "cheap_and_agreed | 1" in text
    assert "p95 | 195.000 ms" in text
    assert "JEV vs deterministic agreement | 50.0%" in text
    assert "Decisions changed by JEV | 1" in text
    assert "Estimated total | $0.0040" in text
    assert "JEV confirmed | 2 | 2 | 100.0%" in text
    assert "Deterministic only | 2 | 2 | 100.0%" in text
    assert "Small sample warning" in text
    assert "\u2014" not in text
    assert "\u2013" not in text


def test_empty_log_renders_guidance():
    report = build_report([])
    text = render_markdown(report, log_path="missing.jsonl")
    assert report["total_decisions"] == 0
    assert "No decisions logged yet" in text


def test_load_outcomes_joins_window_outcomes_and_resolutions(tmp_path):
    path = tmp_path / "trades.jsonl"
    rows = [
        {"type": "window_outcome", "window_end_ts": 100, "resolved_up": True},
        {"type": "resolution", "window_end_ts": 200, "resolved_up": False},
        {"type": "trade", "window_end_ts": 300, "resolved_up": True},
        {"type": "window_outcome", "window_end_ts": "bad", "resolved_up": True},
        {"type": "window_outcome", "window_end_ts": 400, "resolved_up": None},
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    assert load_outcomes(path) == {100: True, 200: False}
