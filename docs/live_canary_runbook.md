# Live Canary Runbook

Status date: 2026-05-03

This runbook is for the first supervised live canary only.
Scope is intentionally narrow:

- one asset
- one model
- one active track
- one burner wallet
- one operator present for the full session

Current intended first canary:

- Asset: `BTC`
- Track: `CONVICTION_ONLY`
- Model: `A`

## Preconditions

Do not start a live canary unless all of the following are true:

- `LIVE_READINESS_PLAN.md` `P0` blockers are complete
- a fresh shadow signoff run passed
- dashboard is running on localhost and responding
- single-instance lock is working
- alerts file is writable
- live credentials are loaded only for the burner wallet

## Start

1. Confirm no stale bot process is still running.
2. Confirm the dashboard is responding on the intended localhost port.
3. Confirm `.bot.lock` is not held by an unrelated stale process.
4. Confirm kill switch path and emergency stop procedure are known before launch.
5. Start live mode with one asset and one model only.

Reference commands:

```bash
lsof .bot.lock
python3 webapp/server.py --host 127.0.0.1 --port 8789
python3 bot.py --live --asset BTC --models A --yes-live --no-dashboard
```

## During Run

Watch continuously:

- dashboard freshness indicator
- alerts file
- runtime status file
- live log

Required operator checks:

- BTC freshness stays current
- Polymarket book freshness stays current
- no repeated order failures
- no reconciliation mismatch
- no unexplained open exposure
- no unexpected model/track change

Files to watch:

- `runtime_status.json` or the configured runtime status file
- `alerts.jsonl`
- active `.web_runtime/<run_id>.log`
- active ledger JSONL files

## Stop Conditions

Stop immediately if any of the following happens:

- BTC freshness turns stale and remains stale
- Polymarket book turns stale and remains stale
- repeated order cancellations or rejections
- reconciliation mismatch
- unexpected open position or duplicate order
- drawdown limit breach
- auth failure

## Emergency Stop

Preferred order:

1. Trigger the live kill switch file if configured.
2. Stop the bot process cleanly.
3. Cancel stale open orders if required.
4. Reconcile account state against the ledger before any restart.

## Cancel-All / Cleanup

If stale live orders are left behind:

1. stop the bot
2. cancel all open orders
3. verify open orders are zero
4. rerun reconciliation before restart

## Restart

Only restart after:

- confirming open orders are understood
- confirming unresolved trades are reconciled
- confirming alerts are quiet
- confirming freshness is healthy again

Do not restart into live mode blindly after a crash.

## Post-Run Review

After the canary:

- review every submission/fill/cancel/resolution event
- compare ledger truth with dashboard truth
- verify no local position existed without a real fill
- verify no local close happened without a real exit confirmation or reconciled state
- record realized execution quality versus expected price

## Required Evidence Pack

Save:

- code revision
- runtime config snapshot
- dashboard port
- run id
- log file
- ledger files
- alert file excerpt
- short go / no-go conclusion
