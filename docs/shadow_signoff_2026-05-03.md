# Shadow Signoff Snapshot

Status date: 2026-05-03

This snapshot records the active fresh shadow signoff context after the stale-run cleanup and freshness hardening passes.

## Code Revision

- Commit: `c347424`

## Active Shadow Run

- Run id: `20260501_004515`
- Bot PID: `94329`
- Mode: `shadow-live`
- Asset: `BTC`
- Active track: `CONVICTION_ONLY`
- Model set: `A`

## Dashboard

- URL: `http://127.0.0.1:8789`
- Purpose: launch dashboard and signoff monitor

## Runtime Files

- Status: `.web_runtime/20260501_004515_status.json`
- Log: `.web_runtime/20260501_004515.log`
- Conviction ledger: `.web_runtime/20260501_004515_trades_conviction.jsonl`
- Mispricing ledger: `.web_runtime/20260501_004515_trades_mispricing.jsonl`
- Both ledger: `.web_runtime/20260501_004515_trades_both.jsonl`

## Freshness / Safety Config

- `ALLOW_TRADING_WITHOUT_BTC_PRICE=false`
- `PRICE_STALE_THRESHOLD_SECONDS=5`
- `POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS=8`
- `SHADOW_MULTI_TRACK_EXECUTION=true`
- `ACTIVE_MODEL_TRACK=CONVICTION_ONLY`
- `LIVE_FORCE_INTERVAL_TRADES=false`
- `LIVE_HEDGE_ENABLED=true`
- `MIN_BET_USDC=2.0`
- `MAX_BET_USDC=5.0`

## Lock / Single-Instance State

- Lock file: `.bot.lock`
- Verified holder during snapshot: PID `94329`

## Candidate Launch Bias

Current preferred first live canary remains:

- Asset: `BTC`
- Track: `CONVICTION_ONLY`
- Model: `A`

Reason:

- best practical shadow evidence so far
- less churn than `C/BOTH`
- more meaningful sample than `B`

## Notes

- BTC freshness now fails closed by default.
- Stale/unavailable Polymarket book now fails closed explicitly.
- Dashboard/runtime now expose BTC and Polymarket book freshness.
- Throttled alerts now exist for stale BTC snapshot failure and stale/unavailable Polymarket book conditions.

## Remaining Signoff Work

- observe a full healthy shadow session
- review warnings/alerts over the full session
- manually inspect every trade in the signoff window
- verify no stale-price or stale-book trades are attempted
- record final go / no-go memo before any live canary
