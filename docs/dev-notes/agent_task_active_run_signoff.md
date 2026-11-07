# Agent Task: Active Run Signoff Report

Status date: 2026-05-13

## Goal

Build a lightweight script or report path that summarizes the currently running shadow session into a launch-check artifact.

This is for answering:

- is the current run operationally healthy
- is feed quorum behaving well
- are stale-data blocks firing when they should
- how are `CONVICTION_ONLY`, `MISPRICING_ONLY`, and `BOTH` performing so far
- is this run usable as shadow signoff evidence

## Scope

In scope:

- read the active runtime status
- summarize the current run using the active ledgers when available
- produce a concise signoff-oriented output
- focus on launch readiness, not general research depth

Out of scope:

- modifying bot logic
- rewriting full research reporting
- changing live execution code
- restarting the bot

## Constraints

- Do not stop the running bot.
- Do not move or rename the active runtime files.
- Keep reads efficient; avoid repeatedly loading giant ledgers in a hot loop.
- A one-shot script is preferred over a continuously polling process.

## Files Likely Involved

- [runtime_status.json](runtime_status.json)
- [scripts/report_performance.py](scripts/report_performance.py)
- [webapp/manager.py](webapp/manager.py)
- [tests/test_report_performance.py](tests/test_report_performance.py)
- maybe a new script under [scripts/](scripts)

## Desired Output

The output can be Markdown, JSON, or both, but it should include:

### Run identity

- current PID
- mode
- active track mode
- generated timestamp
- status age

### Feed health

- BTC healthy source count
- BTC healthy source names
- quorum met or not
- divergence bps
- BTC stale flag
- Polymarket book stale flag
- Polymarket book idle flag
- Polymarket book age

### Track summary

For each of:

- `CONVICTION_ONLY`
- `MISPRICING_ONLY`
- `BOTH`

include:

- bankroll current
- trade count
- win rate
- latest signal if present
- latest resolved PnL summary if easy to extract

### Launch checks

At minimum include pass/fail or yes/no statements for:

- runtime status is fresh
- BTC quorum is met
- BTC divergence is under threshold
- Polymarket book is fresh or explicitly idle
- no stale BTC trading condition is present
- no stale Polymarket trading condition is present

### Conclusion

One short verdict:

- `usable shadow evidence`
- `monitor longer`
- `no-go`

## Implementation Options

Preferred option:

- add a new one-shot script such as `scripts/summarize_active_run.py`

Acceptable option:

- extend an existing reporting script if the new path stays clean and small

Avoid:

- pushing this logic into the bot loop
- making the dashboard depend on this script for normal rendering

## Acceptance Criteria

- the script runs against the currently active session without restarting it
- output is readable in under a minute by an operator
- result clearly distinguishes:
  - healthy feeds
  - stale feeds
  - fresh but low-sample performance
  - operational no-go conditions
- output matches the live `runtime_status.json` fields

## Verification

Minimum:

```bash
python3 -m py_compile scripts/report_performance.py
python3 scripts/summarize_active_run.py
pytest -q tests/test_report_performance.py
```

If a new test file is added, include it in the targeted pytest command.

Cross-check against:

- [runtime_status.json](runtime_status.json)
- the active dashboard at `http://127.0.0.1:8791`

## Deliverable

Return:

- files changed
- example output
- whether it is a one-shot script, reusable helper, or both
- any known limitations, especially around external runs versus manager-owned runs

