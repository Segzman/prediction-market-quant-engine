"""Aggregate the JEV decision log into a markdown report.

Reads one JSON line per resolve_btc_confirmation decision (see
strategy/jev.py) and optionally joins window outcomes from the trade
ledger. Prints the report and writes docs/jev_report.md by default.

Usage:
    python -m research.jev_report
    python -m research.jev_report --ledger trades.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import JEV_LOG_FILE

SMALL_SAMPLE_MIN = 30
SESSION_GAP_SECONDS = 30 * 60


def _read_jsonl(path: str | Path) -> list[dict]:
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


def load_log(path: str | Path) -> list[dict]:
    return _read_jsonl(path)


def load_outcomes(path: str | Path) -> dict[int, bool]:
    outcomes: dict[int, bool] = {}
    for record in _read_jsonl(path):
        if record.get("type") not in {"window_outcome", "resolution"}:
            continue
        resolved_up = record.get("resolved_up")
        if not isinstance(resolved_up, bool):
            continue
        try:
            window_end_ts = int(record["window_end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        outcomes[window_end_ts] = resolved_up
    return outcomes


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _session_count(records: list[dict]) -> int:
    timestamps = sorted(
        float(record["ts"]) for record in records if isinstance(record.get("ts"), (int, float))
    )
    if not timestamps:
        return 1 if records else 0
    sessions = 1
    for previous, current in zip(timestamps, timestamps[1:], strict=False):
        if current - previous > SESSION_GAP_SECONDS:
            sessions += 1
    return sessions


def _accuracy(records: list[dict], outcomes: dict[int, bool]) -> dict:
    joined = [
        record
        for record in records
        if record.get("window_end_ts") in outcomes and record.get("strongest_side") in {"UP", "DOWN"}
    ]
    correct = 0
    for record in joined:
        resolved_up = bool(outcomes[record["window_end_ts"]])
        if (record["strongest_side"] == "UP") == resolved_up:
            correct += 1
    return {"n": len(joined), "correct": correct}


def build_report(records: list[dict], outcomes: dict[int, bool] | None = None) -> dict:
    outcomes = outcomes or {}
    api_calls = [record for record in records if record.get("api_call")]
    cache_hits = [record for record in records if record.get("cache_hit")]

    skipped_by_reason: dict[str, int] = {}
    for record in records:
        reason = record.get("skip_reason")
        if reason:
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

    latencies = [
        float(record["latency_ms"])
        for record in api_calls
        if isinstance(record.get("latency_ms"), (int, float))
    ]
    errors = [record for record in api_calls if record.get("error_class")]

    with_jev = [record for record in records if record.get("jev_decision") is not None]
    agreed = [
        record for record in with_jev if record.get("jev_decision") == record.get("deterministic_decision")
    ]
    changed = [
        record for record in with_jev if record.get("jev_decision") != record.get("deterministic_decision")
    ]

    total_cost = sum(float(record.get("estimated_cost_usd") or 0.0) for record in records)
    sessions = _session_count(records)

    joined = [
        record
        for record in records
        if record.get("window_end_ts") in outcomes and record.get("strongest_side") in {"UP", "DOWN"}
    ]
    jev_confirmed = [record for record in joined if record.get("jev_decision") is True]
    deterministic_only = [
        record
        for record in joined
        if record.get("jev_decision") is None and record.get("deterministic_decision") is True
    ]

    query_attempts = len(api_calls) + len(cache_hits)
    return {
        "total_decisions": len(records),
        "api_calls": len(api_calls),
        "cache_hits": len(cache_hits),
        "cache_hit_rate": (len(cache_hits) / query_attempts if query_attempts else 0.0),
        "skipped_by_reason": skipped_by_reason,
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "latency_max_ms": max(latencies) if latencies else None,
        "errors": len(errors),
        "error_rate": len(errors) / len(api_calls) if api_calls else 0.0,
        "jev_decisions": len(with_jev),
        "agreement_rate": len(agreed) / len(with_jev) if with_jev else 0.0,
        "changed_decisions": len(changed),
        "total_cost_usd": total_cost,
        "sessions": sessions,
        "cost_per_session_usd": total_cost / sessions if sessions else 0.0,
        "cost_per_api_call_usd": (total_cost / len(api_calls) if api_calls else 0.0),
        "joined_outcomes": len(joined),
        "jev_confirmed": _accuracy(jev_confirmed, outcomes),
        "deterministic_only": _accuracy(deterministic_only, outcomes),
        "small_sample": (len(jev_confirmed) < SMALL_SAMPLE_MIN or len(deterministic_only) < SMALL_SAMPLE_MIN),
    }


def _rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def _number(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}{suffix}"


def render_markdown(
    report: dict,
    *,
    log_path: str | Path,
    ledger_path: str | Path | None = None,
) -> str:
    lines = [
        "# JEV Experiment Report",
        "",
        f"Log: `{log_path}`",
        f"Decisions: {report['total_decisions']}",
    ]
    if ledger_path:
        lines.append(f"Outcome join: `{ledger_path}` ({report['joined_outcomes']} joined)")
    else:
        lines.append("Outcome join: not requested (pass `--ledger trades.jsonl`)")
    lines.append("")

    if report["total_decisions"] == 0:
        lines.extend(
            [
                "No decisions logged yet. Run the bot with `JEV_NOUL_ENABLED=1` "
                "and `TYPESAFE_API_KEY` set, then rerun this report.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## Calls",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| API calls made | {report['api_calls']} |",
            f"| Cache hits | {report['cache_hits']} |",
            f"| Cache hit rate | {_rate(report['cache_hit_rate'])} |",
            f"| Errors | {report['errors']} |",
            f"| Error rate | {_rate(report['error_rate'])} |",
            "",
            "## Skipped by reason",
            "",
        ]
    )
    if report["skipped_by_reason"]:
        lines.extend(["| Reason | Count |", "| --- | --- |"])
        for reason, count in sorted(report["skipped_by_reason"].items()):
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("No skipped decisions.")
    lines.append("")

    lines.extend(
        [
            "## Latency",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| p50 | {_number(report['latency_p50_ms'], ' ms')} |",
            f"| p95 | {_number(report['latency_p95_ms'], ' ms')} |",
            f"| max | {_number(report['latency_max_ms'], ' ms')} |",
            "",
            "## Decisions",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| JEV answers | {report['jev_decisions']} |",
            f"| JEV vs deterministic agreement | {_rate(report['agreement_rate'])} |",
            f"| Decisions changed by JEV | {report['changed_decisions']} |",
            "",
            "## Cost",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Estimated total | ${report['total_cost_usd']:.4f} |",
            f"| Estimated per session | ${report['cost_per_session_usd']:.4f} |",
            f"| Estimated per API call | ${report['cost_per_api_call_usd']:.4f} |",
            f"| Sessions (30 min gap) | {report['sessions']} |",
            "",
            "## Outcomes",
            "",
        ]
    )
    if report["joined_outcomes"] == 0:
        lines.append(
            "No outcomes joined. Pass `--ledger <ledger file>` to compare "
            "JEV-confirmed and deterministic-only decisions with resolved windows."
        )
    else:
        jev = report["jev_confirmed"]
        det = report["deterministic_only"]
        jev_rate = _rate(jev["correct"] / jev["n"]) if jev["n"] else "n/a"
        det_rate = _rate(det["correct"] / det["n"]) if det["n"] else "n/a"
        lines.extend(
            [
                "| Group | Correct | Sample | Accuracy |",
                "| --- | --- | --- | --- |",
                f"| JEV confirmed | {jev['correct']} | {jev['n']} | {jev_rate} |",
                f"| Deterministic only | {det['correct']} | {det['n']} | {det_rate} |",
            ]
        )
        if report["small_sample"]:
            lines.extend(
                [
                    "",
                    f"> Small sample warning: outcome comparisons need at least "
                    f"{SMALL_SAMPLE_MIN} resolved windows per group before they "
                    f"mean anything. Current samples: JEV confirmed {jev['n']}, "
                    f"deterministic only {det['n']}.",
                ]
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the JEV experiment report.")
    parser.add_argument("--log", default=JEV_LOG_FILE, help="Decision log path")
    parser.add_argument(
        "--ledger",
        default=None,
        help="Optional ledger path used to join window outcomes",
    )
    parser.add_argument(
        "--out",
        default="docs/jev_report.md",
        help="Markdown output path",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print only, do not write the markdown file",
    )
    args = parser.parse_args(argv)

    records = load_log(args.log)
    outcomes = load_outcomes(args.ledger) if args.ledger else {}
    report = build_report(records, outcomes)
    text = render_markdown(report, log_path=args.log, ledger_path=args.ledger)
    print(text)
    if not args.no_write:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
