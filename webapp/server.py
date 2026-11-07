from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from webapp.manager import BotRunManager, RunConfig
except ImportError:
    from manager import BotRunManager, RunConfig


class DashboardHandler(BaseHTTPRequestHandler):
    manager: BotRunManager
    static_dir: Path
    read_only: bool = False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._write_json(self._healthz_payload())
            return
        if parsed.path == "/":
            self._serve_file(self.static_dir / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._serve_file(self.static_dir / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._serve_file(self.static_dir / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/api/status":
            payload = self.manager.current_run(
                include_report=False,
                include_logs=False,
                include_enrichment=False,
            )
            payload["read_only"] = self.read_only
            self._write_json(payload)
            return
        if parsed.path == "/api/logs":
            query = parse_qs(parsed.query)
            lines = int(query.get("lines", ["80"])[0])
            status = self.manager.current_run(
                include_report=False,
                include_logs=True,
                include_enrichment=False,
            )
            self._write_json({"logs": status.get("logs", [])[-lines:]})
            return
        if parsed.path == "/api/report":
            status = self.manager.current_run(include_report=True, include_logs=False)
            self._write_json(status.get("report", {"rows": {}, "markdown": ""}))
            return
        if parsed.path == "/api/health":
            self._write_json(self.manager.build_health_payload())
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if self.read_only:
            self._write_json(
                {"error": "dashboard is read-only"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        if parsed.path == "/api/start":
            try:
                payload = self._read_json_body()
                config = self._parse_config(payload)
                result = self.manager.start_run(config)
                self._write_json(result, status=HTTPStatus.ACCEPTED)
            except Exception as exc:
                self._write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/stop":
            result = self.manager.stop_run()
            self._write_json(result)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _parse_config(self, payload: dict) -> RunConfig:
        models_raw = payload.get("models", "A")
        if isinstance(models_raw, str):
            models = tuple(
                model.strip().upper() for model in models_raw.split(",") if model.strip()
            )
        else:
            models = tuple(str(model).strip().upper() for model in models_raw if str(model).strip())
        if not models:
            models = ("A",)

        duration = payload.get("duration")
        duration_value = int(duration) if duration not in ("", None) else None
        active_track = str(payload.get("active_track", "BOTH")).upper()
        if active_track not in {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}:
            raise ValueError(f"Unsupported active_track {active_track}")

        return RunConfig(
            mode=str(payload.get("mode", "shadow-live")),
            asset=str(payload.get("asset", "BTC")).upper(),
            models=models,
            active_track=active_track,
            bankroll=float(payload.get("bankroll", 10.0)),
            duration=duration_value,
            debug=bool(payload.get("debug", False)),
            day_vol_gate=bool(payload.get("day_vol_gate", False)),
        )

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _healthz_payload(self) -> dict:
        health = self.manager.build_health_payload()
        age = health.get("status_age_seconds")
        running = bool(health.get("running"))
        fresh = isinstance(age, (int, float)) and age <= 120
        feeds_ok = not (
            health.get("btc_price_stale") is True
            or health.get("polymarket_book_stale") is True
        )
        mode = str(health.get("mode") or "").upper()
        healthy = running and fresh and feeds_ok
        return {
            "status": "ok" if healthy else "degraded",
            "paper_mode": mode != "LIVE",
            "mode": mode or None,
            "running": running,
            "pid": health.get("pid"),
            "status_age_seconds": round(age, 1) if isinstance(age, (int, float)) else None,
            "btc_price_stale": health.get("btc_price_stale"),
            "btc_price_quorum_met": health.get("btc_price_quorum_met"),
            "btc_healthy_source_count": health.get("btc_healthy_source_count"),
            "polymarket_book_stale": health.get("polymarket_book_stale"),
            "read_only": self.read_only,
            "generated_ts": time.time(),
        }

    def _write_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_handler(manager: BotRunManager, static_dir: Path, read_only: bool = False):
    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.manager = manager
    BoundHandler.static_dir = static_dir
    BoundHandler.read_only = read_only
    return BoundHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable start and stop endpoints",
    )
    args = parser.parse_args()

    base_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else Path(__file__).resolve().parent.parent
    )
    manager = BotRunManager(base_dir)
    static_dir = Path(__file__).resolve().parent / "static"
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(manager, static_dir, read_only=args.read_only),
    )
    print(f"Web dashboard listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
