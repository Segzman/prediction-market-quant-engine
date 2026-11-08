#!/usr/bin/env python3
"""
Summarize the currently running shadow session for launch signoff.

This script reads the active runtime status and ledgers to produce a concise
signoff-oriented report focused on operational health and launch readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from scripts.report_performance import load_jsonl
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.report_performance import load_jsonl


def find_runtime_status(base_dir: Path) -> Path | None:
    """Find the active runtime status file."""
    runtime_dir = base_dir / ".web_runtime"
    if runtime_dir.exists():
        freshest: tuple[float, Path] | None = None
        now = time.time()
        for status_path in runtime_dir.glob("*_status.json"):
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            generated_ts = payload.get("generated_ts")
            freshness = (
                float(generated_ts)
                if isinstance(generated_ts, (int, float))
                else status_path.stat().st_mtime
            )
            if freshness < now - 30:
                continue
            if freshest is None or freshness > freshest[0]:
                freshest = (freshness, status_path)
        if freshest:
            return freshest[1]

    runtime_status_path = base_dir / "runtime_status.json"
    if runtime_status_path.exists():
        return runtime_status_path
    return None


def get_lock_holder_pid(base_dir: Path) -> int | None:
    """Get the PID of the process holding the bot lock."""
    try:
        result = subprocess.run(
            ["lsof", str(base_dir / ".bot.lock")],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    columns = lines[1].split()
    if len(columns) < 2:
        return None
    try:
        return int(columns[1])
    except ValueError:
        return None


def is_pid_running(pid: int) -> bool:
    """Check if a PID is running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_status(status_path: Path) -> dict:
    """Load and parse runtime status."""
    if not status_path.exists():
        return {}
    return json.loads(status_path.read_text(encoding="utf-8"))


def find_ledger_files(base_dir: Path, status_path: Path) -> dict[str, Path]:
    """Find ledger files for the active run."""
    runtime_dir = base_dir / ".web_runtime"
    if status_path.parent == runtime_dir:
        run_id = status_path.name.removesuffix("_status.json")
        return {
            "CONVICTION_ONLY": runtime_dir / f"{run_id}_trades_conviction.jsonl",
            "MISPRICING_ONLY": runtime_dir / f"{run_id}_trades_mispricing.jsonl",
            "BOTH": runtime_dir / f"{run_id}_trades_both.jsonl",
        }
    return {
        "CONVICTION_ONLY": runtime_dir / "external_trades_conviction.jsonl",
        "MISPRICING_ONLY": runtime_dir / "external_trades_mispricing.jsonl",
        "BOTH": runtime_dir / "external_trades_both.jsonl",
    }


def extract_track_summary(ledger_path: Path) -> dict:
    """Extract summary metrics from a track ledger."""
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        return {
            "bankroll_current": None,
            "trade_count": 0,
            "win_rate": None,
            "latest_signal": None,
            "resolved_pnl_summary": None,
        }

    records = load_jsonl(ledger_path)
    trades = [r for r in records if r.get("type") == "trade"]
    resolutions = {r.get("order_id"): r for r in records if r.get("type") == "resolution"}
    candidates = [r for r in records if r.get("type") == "candidate"]

    trade_count = len(trades)
    resolved_count = 0
    wins = 0
    gross_pnl = 0.0

    for trade in trades:
        order_id = trade.get("order_id")
        if order_id in resolutions:
            resolved_count += 1
            pnl = float(resolutions[order_id].get("pnl", 0.0) or 0.0)
            gross_pnl += pnl
            if pnl > 0:
                wins += 1

    win_rate = wins / resolved_count if resolved_count else None

    latest_signal = None
    for candidate in reversed(candidates):
        if candidate.get("direction") in {"UP", "DOWN"}:
            latest_signal = {
                "direction": candidate.get("direction"),
                "confidence": candidate.get("confidence") or candidate.get("direction_probability"),
                "ts": candidate.get("ts"),
            }
            break

    resolved_pnl_summary = None
    if resolved_count > 0:
        resolved_pnl_summary = {
            "resolved_count": resolved_count,
            "gross_pnl": gross_pnl,
            "avg_pnl": gross_pnl / resolved_count,
        }

    return {
        "bankroll_current": None,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "latest_signal": latest_signal,
        "resolved_pnl_summary": resolved_pnl_summary,
    }


def render_markdown(summary: dict) -> str:
    """Render the summary as Markdown."""
    lines = ["# Active Run Signoff Report\n"]
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")

    # Run identity
    lines.append("## Run Identity\n")
    lines.append(f"- **PID**: {summary.get('pid')}\n")
    lines.append(f"- **Mode**: {summary.get('mode')}\n")
    lines.append(f"- **Active Track**: {summary.get('active_track')}\n")
    lines.append(f"- **Generated Timestamp**: {summary.get('generated_ts')}\n")
    lines.append(f"- **Status Age**: {summary.get('status_age_seconds', 0):.1f}s\n")
    lines.append(f"- **Running**: {'Yes' if summary.get('running') else 'No'}\n")

    # Feed health
    lines.append("\n## Feed Health\n")
    btc = summary.get("btc_feed", {})
    lines.append(f"- **BTC Healthy Sources**: {btc.get('healthy_source_count', 0)}\n")
    lines.append(f"- **BTC Healthy Source Names**: {', '.join(btc.get('healthy_sources', []))}\n")
    lines.append(f"- **BTC Quorum Met**: {'Yes' if btc.get('quorum_met') else 'No'}\n")
    lines.append(f"- **BTC Divergence**: {btc.get('divergence_bps', 0):.1f} bps\n")
    lines.append(f"- **BTC Stale**: {'Yes' if btc.get('stale') else 'No'}\n")

    poly = summary.get("polymarket_feed", {})
    lines.append(f"- **Polymarket Book Stale**: {'Yes' if poly.get('stale') else 'No'}\n")
    lines.append(f"- **Polymarket Book Idle**: {'Yes' if poly.get('idle') else 'No'}\n")
    lines.append(f"- **Polymarket Book Age**: {poly.get('age_seconds', 0):.1f}s\n")

    # Track summary
    lines.append("\n## Track Summary\n")
    for track_name in ["CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"]:
        track = summary.get("tracks", {}).get(track_name, {})
        lines.append(f"\n### {track_name}\n")
        lines.append(f"- **Trade Count**: {track.get('trade_count', 0)}\n")
        if track.get("win_rate") is not None:
            lines.append(f"- **Win Rate**: {track['win_rate']:.1%}\n")
        if track.get("latest_signal"):
            sig = track["latest_signal"]
            lines.append(
                f"- **Latest Signal**: {sig.get('direction')} "
                f"(confidence: {sig.get('confidence', 0):.2f})\n"
            )
        if track.get("resolved_pnl_summary"):
            pnl = track["resolved_pnl_summary"]
            lines.append(
                f"- **Resolved PnL**: ${pnl.get('gross_pnl', 0):.2f} across "
                f"{pnl.get('resolved_count', 0)} trades\n"
            )

    # Launch checks
    lines.append("\n## Launch Checks\n")
    checks = summary.get("launch_checks", {})
    for check_name, result in checks.items():
        status = "✓ PASS" if result else "✗ FAIL"
        lines.append(f"- **{check_name}**: {status}\n")

    # Conclusion
    lines.append("\n## Conclusion\n")
    verdict = summary.get("verdict", "unknown")
    lines.append(f"**Verdict**: {verdict}\n")

    return "".join(lines)


def render_json(summary: dict) -> str:
    """Render the summary as JSON."""
    return json.dumps(summary, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["markdown", "json", "both"], default="markdown")
    parser.add_argument("--base-dir", default=Path.cwd(), type=Path)
    args = parser.parse_args()

    base_dir = args.base_dir.resolve()
    status_path = find_runtime_status(base_dir)
    if not status_path:
        print("No active runtime status found", file=sys.stderr)
        sys.exit(1)

    status = load_status(status_path)
    pid = get_lock_holder_pid(base_dir)
    running = pid is not None and is_pid_running(pid) if pid else False

    generated_ts = status.get("generated_ts")
    status_age_seconds = (time.time() - float(generated_ts)) if generated_ts else None

    freshness = status.get("freshness", {})
    btc_feed = freshness.get("btc_feed", {})
    polymarket_feed = freshness.get("polymarket_feed", {})

    ledger_files = find_ledger_files(base_dir, status_path)
    tracks = {}
    for track_name, ledger_path in ledger_files.items():
        tracks[track_name] = extract_track_summary(ledger_path)

    # Launch checks
    launch_checks = {
        "Runtime status fresh": status_age_seconds is not None and status_age_seconds < 10,
        "BTC quorum met": btc_feed.get("quorum_met", False),
        "BTC divergence acceptable": (btc_feed.get("divergence_bps") or 0) < 10,
        "Polymarket book fresh or idle": (
            not polymarket_feed.get("stale", True) or polymarket_feed.get("idle", False)
        ),
        "No stale BTC trading condition": not btc_feed.get("stale", True),
        "No stale Polymarket trading condition": not polymarket_feed.get("stale", True),
    }

    # Determine verdict
    all_pass = all(launch_checks.values())
    if all_pass:
        verdict = "usable shadow evidence"
    elif status_age_seconds and status_age_seconds < 60 and sum(launch_checks.values()) >= 4:
        verdict = "monitor longer"
    else:
        verdict = "no-go"

    summary = {
        "pid": pid,
        "mode": status.get("mode"),
        "active_track": status.get("active_track"),
        "generated_ts": generated_ts,
        "status_age_seconds": status_age_seconds,
        "running": running,
        "btc_feed": btc_feed,
        "polymarket_feed": polymarket_feed,
        "tracks": tracks,
        "launch_checks": launch_checks,
        "verdict": verdict,
    }

    if args.format in ["markdown", "both"]:
        print(render_markdown(summary))
    if args.format in ["json", "both"]:
        if args.format == "both":
            print("\n---\n")
        print(render_json(summary))


if __name__ == "__main__":
    main()
