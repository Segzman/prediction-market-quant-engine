from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.report_performance import (
        build_fee_context,
        build_probability_path_report,
        build_report,
        load_jsonl,
        render_report,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.report_performance import (
        build_fee_context,
        build_probability_path_report,
        build_report,
        load_jsonl,
        render_report,
    )


@dataclass
class RunConfig:
    mode: str = "shadow-live"
    asset: str = "BTC"
    models: tuple[str, ...] = ("A",)
    active_track: str = "BOTH"
    bankroll: float = 10.0
    duration: int | None = None
    debug: bool = False
    day_vol_gate: bool = False


class BotRunManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.runtime_dir = base_dir / ".web_runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.runtime_dir / "manager_state.json"
        self._process: subprocess.Popen[str] | None = None
        self._state_lock = threading.Lock()
        self._report_cache: dict[str, Any] | None = None
        self._report_cache_key: tuple[tuple[str, float, int], ...] | None = None

    def build_command(self, config: RunConfig) -> list[str]:
        mode = config.mode.lower()
        if mode not in {"shadow-live", "demo", "demo-local", "live"}:
            raise ValueError(f"Unsupported mode {config.mode}")
        if mode == "live" and len(config.models) != 1:
            raise ValueError("Live mode only supports one model at a time")
        if config.active_track not in {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}:
            raise ValueError(f"Unsupported active track {config.active_track}")
        if config.bankroll <= 0:
            raise ValueError("Bankroll must be greater than 0")
        if config.duration is not None and config.duration <= 0:
            raise ValueError("Duration must be greater than 0")

        command = [
            sys.executable,
            "bot.py",
            "--asset",
            config.asset.upper(),
            "--bankroll",
            f"{config.bankroll:.2f}",
        ]
        if mode == "shadow-live":
            command.append("--shadow-live")
        elif mode == "demo":
            command.append("--demo")
        elif mode == "demo-local":
            command.append("--demo-local")
        elif mode == "live":
            command.extend(["--live", "--yes-live"])

        command.extend(["--models", ",".join(model.upper() for model in config.models)])
        command.append("--no-dashboard")
        if config.duration is not None:
            command.extend(["--duration", str(config.duration)])
        if config.debug:
            command.append("--debug")
        return command

    def start_run(self, config: RunConfig) -> dict[str, Any]:
        active = self.current_run()
        if active.get("running"):
            raise RuntimeError("A bot run is already active")

        run_id = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        ledger_file_conviction = self.runtime_dir / f"{run_id}_trades_conviction.jsonl"
        ledger_file_mispricing = self.runtime_dir / f"{run_id}_trades_mispricing.jsonl"
        ledger_file_both = self.runtime_dir / f"{run_id}_trades_both.jsonl"
        status_file = self.runtime_dir / f"{run_id}_status.json"
        log_file = self.runtime_dir / f"{run_id}.log"
        command = self.build_command(config)
        env = os.environ.copy()
        env.update(
            {
                "LEDGER_FILE_CONVICTION": str(ledger_file_conviction),
                "LEDGER_FILE_MISPRICING": str(ledger_file_mispricing),
                "LEDGER_FILE_BOTH": str(ledger_file_both),
                "RUNTIME_STATUS_FILE": str(status_file),
                "ACTIVE_MODEL_TRACK": config.active_track,
                "DAY_VOL_GATE_ENABLED": "1" if config.day_vol_gate else "0",
                "PYTHONUNBUFFERED": "1",
            }
        )

        with log_file.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                cwd=self.base_dir,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

        self._process = process
        state = {
            "run_id": run_id,
            "pid": process.pid,
            "started_ts": time.time(),
            "command": command,
            "config": asdict(config),
            "ledger_file_conviction": str(ledger_file_conviction),
            "ledger_file_mispricing": str(ledger_file_mispricing),
            "ledger_file": str(ledger_file_both),
            "status_file": str(status_file),
            "log_file": str(log_file),
        }
        self._write_state(state)
        return self.current_run()

    def stop_run(self, timeout_seconds: float = 10.0) -> dict[str, Any]:
        state = self._read_state()
        if not state:
            return {"running": False, "stopped": False, "reason": "No managed run"}

        pid = int(state["pid"])
        if not self._pid_running(pid):
            state["stopped_ts"] = time.time()
            self._write_state(state)
            return {"running": False, "stopped": False, "reason": "Process already exited"}

        self._terminate_pid(pid, signal.SIGTERM)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not self._pid_running(pid):
                state["stopped_ts"] = time.time()
                self._write_state(state)
                return {"running": False, "stopped": True, "signal": "SIGTERM"}
            time.sleep(0.2)

        self._terminate_pid(pid, signal.SIGKILL)
        state["stopped_ts"] = time.time()
        self._write_state(state)
        return {"running": False, "stopped": True, "signal": "SIGKILL"}

    def current_run(
        self,
        *,
        include_report: bool = True,
        include_logs: bool = True,
        include_enrichment: bool = True,
    ) -> dict[str, Any]:
        state = self._read_state()
        if state is None:
            state = self._discover_active_runtime_state()
            if state is not None:
                self._write_state(state)
        if not state:
            return {
                "configured": False,
                "running": False,
                "report": (
                    {"rows": {}, "markdown": "", "tracks": {}, "comparison": {}}
                    if include_report
                    else {}
                ),
                "logs": [] if include_logs else [],
            }

        pid = int(state["pid"])
        running = self._pid_running(pid)
        if self._process is not None and self._process.pid == pid and self._process.poll() is not None:
            running = False
            state["exit_code"] = self._process.returncode
            self._write_state(state)
        if not running:
            discovered_state = self._discover_active_runtime_state()
            if discovered_state is not None and discovered_state.get("pid") != pid:
                state = discovered_state
                pid = int(state["pid"])
                running = True
                self._write_state(state)

        ledger_paths = {
            "CONVICTION_ONLY": Path(state.get("ledger_file_conviction", state["ledger_file"])),
            "MISPRICING_ONLY": Path(state.get("ledger_file_mispricing", state["ledger_file"])),
            "BOTH": Path(state["ledger_file"]),
        }
        status_payload = self.read_runtime_status(Path(state["status_file"]))
        if include_enrichment:
            status_payload = self._enrich_status_payload(status_payload, ledger_paths)
        report_payload = self.build_report_payload(ledger_paths) if include_report else {}
        return {
            "configured": True,
            "running": running,
            "pid": pid,
            "started_ts": state.get("started_ts"),
            "stopped_ts": state.get("stopped_ts"),
            "exit_code": state.get("exit_code"),
            "command": state.get("command", []),
            "config": state.get("config", {}),
            "paths": {
                "ledger_file": state.get("ledger_file"),
                "ledger_file_conviction": state.get("ledger_file_conviction", state.get("ledger_file")),
                "ledger_file_mispricing": state.get("ledger_file_mispricing", state.get("ledger_file")),
                "status_file": state.get("status_file"),
                "log_file": state.get("log_file"),
            },
            "status": status_payload,
            "report": report_payload,
            "logs": self.tail_logs(Path(state["log_file"])) if include_logs else [],
        }

    def read_runtime_status(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def build_report_payload(self, ledger_paths: dict[str, Path]) -> dict[str, Any]:
        cache_key: list[tuple[str, float, int]] = []
        for path in ledger_paths.values():
            if path.exists():
                stat = path.stat()
                cache_key.append((str(path.resolve()), stat.st_mtime, stat.st_size))
            else:
                cache_key.append((str(path.resolve()), 0.0, 0))
        cache_key_tuple = tuple(sorted(cache_key))
        if self._report_cache is not None and self._report_cache_key == cache_key_tuple:
            return self._report_cache

        combined_records: list[dict[str, Any]] = []
        track_payloads: dict[str, Any] = {}
        combined_seen_paths: set[Path] = set()
        for track_name, ledger_path in ledger_paths.items():
            if not ledger_path.exists() or ledger_path.stat().st_size == 0:
                track_payloads[track_name] = {
                    "path": str(ledger_path),
                    "rows": {},
                    "markdown": "",
                }
                continue
            records = load_jsonl(ledger_path)
            resolved_path = ledger_path.resolve()
            if resolved_path not in combined_seen_paths:
                combined_records.extend(records)
                combined_seen_paths.add(resolved_path)
            track_report = build_report(records)
            track_payloads[track_name] = {
                "path": str(ledger_path),
                "rows": track_report,
                "markdown": render_report(track_report) if track_report else "",
            }
        if not combined_records:
            payload = {
                "rows": {},
                "markdown": "",
                "fee_context": build_fee_context(),
                "tracks": track_payloads,
                "comparison": {},
            }
            self._report_cache = payload
            self._report_cache_key = cache_key_tuple
            return payload
        report = build_report(combined_records)
        payload = {
            "rows": report,
            "markdown": render_report(report) if report else "",
            "fee_context": build_fee_context(),
            "tracks": track_payloads,
            "comparison": build_probability_path_report(combined_records),
        }
        self._report_cache = payload
        self._report_cache_key = cache_key_tuple
        return payload

    def _enrich_status_payload(
        self, status_payload: dict[str, Any], ledger_paths: dict[str, Path]
    ) -> dict[str, Any]:
        if not status_payload:
            return status_payload

        latest_candidates: dict[str, dict[str, Any]] = {}
        latest_signal_candidates: dict[str, dict[str, Any]] = {}
        freshest_candidate: dict[str, Any] | None = None
        trade_regimes: dict[str, dict[tuple[Any, ...], str]] = {}

        for track_name, ledger_path in ledger_paths.items():
            if not ledger_path.exists() or ledger_path.stat().st_size == 0:
                trade_regimes[track_name] = {}
                continue
            try:
                records = load_jsonl(ledger_path)
            except Exception:
                trade_regimes[track_name] = {}
                continue

            candidates = [
                record for record in records if record.get("type") == "candidate"
            ]
            latest_candidate = max(
                candidates,
                key=lambda record: float(record.get("ts", 0.0) or 0.0),
                default=None,
            )
            if latest_candidate is not None:
                latest_candidates[track_name] = latest_candidate
                if freshest_candidate is None or float(latest_candidate.get("ts", 0.0) or 0.0) > float(
                    freshest_candidate.get("ts", 0.0) or 0.0
                ):
                    freshest_candidate = latest_candidate
            latest_signal_candidate = max(
                (
                    record
                    for record in candidates
                    if record.get("direction") in {"UP", "DOWN"}
                ),
                key=lambda record: float(record.get("ts", 0.0) or 0.0),
                default=None,
            )
            if latest_signal_candidate is not None:
                latest_signal_candidates[track_name] = latest_signal_candidate

            accepted_candidates = sorted(
                (
                    record
                    for record in candidates
                    if record.get("would_trade") and record.get("direction") in {"UP", "DOWN"}
                ),
                key=lambda record: float(record.get("ts", 0.0) or 0.0),
            )
            regimes_by_trade: dict[tuple[Any, ...], str] = {}
            for record in records:
                if record.get("type") != "trade":
                    continue
                regime = str(
                    record.get("entry_regime")
                    or (record.get("feature_snapshot") or {}).get("entry_regime")
                    or ""
                )
                if not regime:
                    regime = self._infer_trade_regime(record, accepted_candidates)
                if regime:
                    regimes_by_trade[self._trade_key(record)] = regime
            trade_regimes[track_name] = regimes_by_trade

        if not status_payload.get("engine_scores") and freshest_candidate is not None:
            probabilities = freshest_candidate.get("probabilities") or {}
            status_payload["engine_scores"] = {
                str(model): float(value)
                for model, value in probabilities.items()
                if value is not None
            }

        tracks = status_payload.get("tracks")
        if not isinstance(tracks, list):
            return status_payload

        for track_payload in tracks:
            if not isinstance(track_payload, dict):
                continue
            track_name = str(track_payload.get("name") or "")
            latest_candidate = latest_signal_candidates.get(track_name) or latest_candidates.get(track_name)
            if latest_candidate is not None:
                direction = latest_candidate.get("direction")
                if direction in {"UP", "DOWN"}:
                    confidence = latest_candidate.get("confidence")
                    if confidence is None:
                        confidence = latest_candidate.get("selected_probability")
                    if confidence is None:
                        confidence = latest_candidate.get("direction_probability")
                    track_payload["signal"] = {
                        "direction": direction,
                        "regime": str(
                            latest_candidate.get("entry_regime")
                            or latest_candidate.get("regime_candidate")
                            or ""
                        ),
                        "confidence": float(confidence or 0.0),
                    }

            regimes_by_trade = trade_regimes.get(track_name, {})
            for trade in track_payload.get("all_trades") or []:
                if not isinstance(trade, dict) or trade.get("regime"):
                    continue
                regime = regimes_by_trade.get(self._trade_key(trade))
                if regime:
                    trade["regime"] = regime

        return status_payload

    def _infer_trade_regime(
        self, trade: dict[str, Any], accepted_candidates: list[dict[str, Any]]
    ) -> str:
        trade_ts = float(trade.get("ts", 0.0) or 0.0)
        trade_direction = str(trade.get("direction") or "")
        trade_window = trade.get("window_end_ts")
        best_match: dict[str, Any] | None = None
        best_distance = float("inf")
        for candidate in accepted_candidates:
            if str(candidate.get("direction") or "") != trade_direction:
                continue
            candidate_window = candidate.get("window_end_ts")
            if (
                trade_window is not None
                and candidate_window is not None
                and int(candidate_window) != int(trade_window)
            ):
                continue
            candidate_ts = float(candidate.get("ts", 0.0) or 0.0)
            if candidate_ts > trade_ts + 5:
                continue
            distance = abs(trade_ts - candidate_ts)
            if distance < best_distance:
                best_distance = distance
                best_match = candidate
        if best_match is None:
            return ""
        return str(
            best_match.get("entry_regime")
            or best_match.get("regime_candidate")
            or ""
        )

    def _trade_key(self, trade: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(float(trade.get("ts", 0.0) or 0.0)),
            str(trade.get("direction") or ""),
            round(float(trade.get("price", 0.0) or 0.0), 4),
            round(
                float(trade.get("amount_usdc", trade.get("amount", 0.0)) or 0.0),
                2,
            ),
        )

    def tail_logs(self, path: Path, lines: int = 80) -> list[str]:
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return content[-lines:]

    def _discover_active_runtime_state(self) -> dict[str, Any] | None:
        lock_pid = self._lock_holder_pid()
        if lock_pid is None:
            return None

        freshest: tuple[float, Path] | None = None
        now = time.time()
        for status_path in self.runtime_dir.glob("*_status.json"):
            try:
                payload = self.read_runtime_status(status_path)
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

        if freshest is None:
            runtime_status_path = self.base_dir / "runtime_status.json"
            if not runtime_status_path.exists():
                return None
            try:
                payload = self.read_runtime_status(runtime_status_path)
            except Exception:
                return None
            generated_ts = payload.get("generated_ts")
            freshness = (
                float(generated_ts)
                if isinstance(generated_ts, (int, float))
                else runtime_status_path.stat().st_mtime
            )
            if freshness < now - 30:
                return None
            return {
                "run_id": f"external_{int(freshness)}",
                "pid": lock_pid,
                "started_ts": runtime_status_path.stat().st_mtime,
                "command": [],
                "config": {},
                "ledger_file": str(self.runtime_dir / "external_trades_both.jsonl"),
                "ledger_file_conviction": str(self.runtime_dir / "external_trades_conviction.jsonl"),
                "ledger_file_mispricing": str(self.runtime_dir / "external_trades_mispricing.jsonl"),
                "status_file": str(runtime_status_path),
                "log_file": str(self.runtime_dir / "external.log"),
                "external": True,
            }

        status_path = freshest[1]
        run_id = status_path.name.removesuffix("_status.json")
        log_file = self.runtime_dir / f"{run_id}.log"
        ledger_file = self.runtime_dir / f"{run_id}_trades_both.jsonl"
        ledger_file_conviction = self.runtime_dir / f"{run_id}_trades_conviction.jsonl"
        ledger_file_mispricing = self.runtime_dir / f"{run_id}_trades_mispricing.jsonl"
        return {
            "run_id": run_id,
            "pid": lock_pid,
            "started_ts": log_file.stat().st_mtime if log_file.exists() else freshest[0],
            "command": [],
            "config": {},
            "ledger_file": str(ledger_file),
            "ledger_file_conviction": str(ledger_file_conviction),
            "ledger_file_mispricing": str(ledger_file_mispricing),
            "status_file": str(status_path),
            "log_file": str(log_file),
            "external": True,
        }

    def _pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _lock_holder_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["lsof", str(self.base_dir / ".bot.lock")],
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

    def _terminate_pid(self, pid: int, sig: int) -> None:
        with contextlib.suppress(Exception):
            os.killpg(pid, sig)
            return
        os.kill(pid, sig)

    def _read_state(self) -> dict[str, Any] | None:
        with self._state_lock:
            if not self.state_file.exists():
                return None
            return json.loads(self.state_file.read_text(encoding="utf-8"))

    def build_health_payload(self) -> dict[str, Any]:
        state = self._read_state()
        if state is None:
            state = self._discover_active_runtime_state()
            if state is not None:
                self._write_state(state)
        if not state:
            return {
                "running": False,
                "pid": None,
                "generated_ts": None,
                "status_age_seconds": None,
                "mode": None,
                "active_track": None,
                "btc_price_stale": None,
                "btc_price_quorum_met": None,
                "btc_healthy_source_count": None,
                "btc_healthy_sources": [],
                "btc_price_divergence_bps": None,
                "polymarket_book_stale": None,
                "polymarket_book_idle": None,
                "polymarket_book_age_seconds": None,
            }

        pid = int(state["pid"])
        running = self._pid_running(pid)
        if self._process is not None and self._process.pid == pid and self._process.poll() is not None:
            running = False
        if not running:
            discovered_state = self._discover_active_runtime_state()
            if discovered_state is not None and discovered_state.get("pid") != pid:
                state = discovered_state
                pid = int(state["pid"])
                running = True
                self._write_state(state)

        status_payload = self.read_runtime_status(Path(state["status_file"]))
        generated_ts = status_payload.get("generated_ts")
        now = time.time()
        status_age_seconds = (now - float(generated_ts)) if generated_ts else None

        freshness = status_payload.get("freshness", {})
        btc_feed = freshness.get("btc_feed") or {
            "stale": freshness.get("btc_price_stale"),
            "quorum_met": freshness.get("btc_price_quorum_met"),
            "healthy_source_count": freshness.get("btc_healthy_source_count"),
            "healthy_sources": freshness.get("btc_healthy_sources", []),
            "divergence_bps": freshness.get("btc_price_divergence_bps"),
        }
        polymarket_feed = freshness.get("polymarket_feed") or {
            "stale": freshness.get("polymarket_book_stale"),
            "idle": freshness.get("polymarket_book_idle"),
            "age_seconds": freshness.get("polymarket_book_age_seconds"),
        }

        return {
            "running": running,
            "pid": pid,
            "generated_ts": generated_ts,
            "status_age_seconds": status_age_seconds,
            "mode": state.get("config", {}).get("mode") or status_payload.get("mode"),
            "active_track": state.get("config", {}).get("active_track"),
            "btc_price_stale": btc_feed.get("stale"),
            "btc_price_quorum_met": btc_feed.get("quorum_met"),
            "btc_healthy_source_count": btc_feed.get("healthy_source_count"),
            "btc_healthy_sources": btc_feed.get("healthy_sources", []),
            "btc_price_divergence_bps": btc_feed.get("divergence_bps"),
            "polymarket_book_stale": polymarket_feed.get("stale"),
            "polymarket_book_idle": polymarket_feed.get("idle"),
            "polymarket_book_age_seconds": polymarket_feed.get("age_seconds"),
        }

    def _write_state(self, payload: dict[str, Any]) -> None:
        with self._state_lock:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.runtime_dir),
                prefix="manager_state.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
                temp_path = Path(handle.name)
            temp_path.replace(self.state_file)
