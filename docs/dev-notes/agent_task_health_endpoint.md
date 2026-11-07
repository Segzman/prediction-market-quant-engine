# Agent Task: Lightweight Dashboard Health Endpoint

Status date: 2026-05-13

## Goal

Add a very cheap dashboard endpoint that reports only operational health for the current run.

The point is to let operators and monitors answer:

- is the bot process alive
- is the runtime status fresh
- is BTC feed quorum healthy
- is BTC divergence acceptable
- is the Polymarket book fresh

This must not do heavy ledger reads or full report generation.

## Scope

In scope:

- add `GET /api/health` to the dashboard server
- return a small JSON payload derived from the current runtime status
- expose enough fields for quick monitoring and canary signoff
- keep the endpoint fast even while the bot is running

Out of scope:

- changing bot decision logic
- changing feed logic
- changing execution logic
- full report rendering
- reading large JSONL ledgers on every request

## Constraints

- Do not stop or restart the bot process.
- Avoid touching files outside the dashboard path unless required.
- Prefer reusing the already-loaded runtime status payload.
- Do not add any code path that scans `.web_runtime/*.jsonl` during `/api/health`.

## Files Likely Involved

- [webapp/server.py](webapp/server.py)
- [webapp/manager.py](webapp/manager.py)
- [webapp/static/index.html](webapp/static/index.html) if you decide to surface the endpoint in the UI
- [tests/test_web_manager.py](tests/test_web_manager.py)

## Desired Payload

Return a compact JSON object similar to:

```json
{
  "running": true,
  "pid": 43922,
  "generated_ts": 1778685083.52,
  "status_age_seconds": 0.8,
  "mode": "DRY_RUN",
  "active_track": "ALL_SHADOW",
  "btc_price_stale": false,
  "btc_price_quorum_met": true,
  "btc_healthy_source_count": 3,
  "btc_healthy_sources": ["COINBASE", "GEMINI", "KRAKEN"],
  "btc_price_divergence_bps": 3.0,
  "polymarket_book_stale": false,
  "polymarket_book_idle": false,
  "polymarket_book_age_seconds": 0.7
}
```

Exact shape can differ slightly, but it should stay small and flat enough for scripts.

## Implementation Notes

Preferred approach:

1. Reuse `BotRunManager.current_run(...)` with a no-report, no-logs, no-enrichment path.
2. Derive the health payload from `status` and `status.freshness`.
3. Add a helper on the manager if that keeps the server cleaner.
4. Keep the endpoint tolerant of missing fields.

Do not:

- call `build_report_payload()` for `/api/health`
- call `_enrich_status_payload(...)` for `/api/health`
- tail logs for `/api/health`

## Acceptance Criteria

- `GET /api/health` returns `200`
- response time is clearly faster than full report generation
- response still works while the bot is actively updating `runtime_status.json`
- payload truthfully reflects:
  - bot PID
  - runtime status age
  - BTC quorum
  - BTC divergence
  - Polymarket freshness
- endpoint works both for managed runs and externally attached runs

## Verification

Minimum:

```bash
python3 -m py_compile webapp/server.py webapp/manager.py
pytest -q tests/test_web_manager.py
curl -sS http://127.0.0.1:8791/api/health | jq
```

Also compare `/api/health` output to:

- [runtime_status.json](runtime_status.json)
- [webapp/manager.py](webapp/manager.py)

## Deliverable

Open a concise summary that includes:

- files changed
- sample `/api/health` response
- measured response time
- whether the UI was updated or not

