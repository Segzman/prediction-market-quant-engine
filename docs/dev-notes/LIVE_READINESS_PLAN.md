# Live Readiness Plan

Status date: 2026-05-01

Current recommendation: `NOT READY FOR LIVE`

This file is the short-path launch plan for the current repo state.
Use it with [PROD_READY_CHECKLIST.md](PROD_READY_CHECKLIST.md):

- `PROD_READY_CHECKLIST.md` is the broad production hardening document.
- This file is the practical go-live checklist for the next supervised canary.

## Why It Is Not Ready Yet

The last active shadow run showed two different realities:

- Strategy behavior looked promising, especially for `A` / `CONVICTION_ONLY`.
- Operations were not healthy enough for live promotion.

Observed blockers from the last run:

- The run became stale while the process stayed alive.
- Market data refreshes failed repeatedly for hours.
- Binance and Coinbase reconnect loops dominated the log.
- The current evidence is still dry-run execution, not real fill behavior.
- The active process is not a clean fresh run and should not be treated as launch evidence.

## Launch Rule

Do not run `--live` until every `P0` item in this file is complete and a fresh shadow run passes the signoff gates at the bottom.

## Current Best Candidate

If the bot is promoted later, the first canary should be:

- one asset only
- one model only
- one wallet only
- smallest meaningful bankroll only
- no hedging by default

Current recommended first canary:

- Asset: `BTC`
- Track: `CONVICTION_ONLY`
- Model: `A`

Reason:

- `A` produced the best practical shadow result from the last useful run section.
- `B` had too little sample.
- `C/BOTH` traded more and made less net than `A`.

## P0 Hard Blockers

Every item below must be complete before any real-money canary.

### 1. Fresh-Run Hygiene

- [x] Kill or archive the stale active run and stop treating it as evidence.
- [x] Start a new shadow run with a clean run id and clean ledgers.
- [x] Confirm the dashboard is attached to the new run, not a recovered stale PID.
- [x] Record the exact code revision, config values, and dashboard port used for signoff.

### 2. Feed Stability

- [ ] Prove Binance feed stability for the intended trading window.
- [ ] Prove Coinbase feed stability for the intended trading window.
- [ ] Prove BTC price snapshot refreshes do not fail continuously.
- [x] Add or verify a fail-closed rule: no new trade when price feed freshness is stale.
- [x] Add or verify a fail-closed rule: no new trade when market discovery or book refresh is stale.
- [x] Add a visible freshness field to the dashboard or runtime status if it is not already exposed clearly enough.

Acceptance:

- No repeated feed disconnect loop for the signoff session.
- No long stretch of `All price snapshot refreshes failed` during the signoff session.
- The bot must stop opening new trades when price or book freshness is stale.

### 3. Real Live Execution Proof

- [ ] Re-verify the live submit/fill/cancel lifecycle in current code paths.
- [ ] Re-verify partial fill behavior in the live path, not just dry-run.
- [ ] Re-verify exit order handling does not locally close exposure before exchange confirmation.
- [ ] Re-verify startup reconciliation against real account state before the first live order.
- [ ] Re-verify kill switch behavior during the main live loop.

Acceptance:

- A tiny supervised live canary must show truthful submission, fill, cancel, and resolution events in the ledger.
- No local position can exist without a real fill.
- No local close can happen without a real exit confirmation or explicit reconciled state.

### 4. Strategy Promotion Gate

- [ ] Freeze one production candidate only.
- [ ] Keep live mode single-track only.
- [ ] Keep `A` as the launch candidate unless new shadow evidence clearly overturns it.
- [ ] Define hard reject criteria for promoting `B` or `C`.
- [ ] Keep forced interval trades disabled.
- [ ] Keep first live canary hedge-disabled unless fresh evidence proves it helps net PnL after fees.

Acceptance:

- Promotion memo says exactly which asset, track, model, bankroll, and risk limits will be used.
- No “we might switch models during the session” ambiguity.

### 5. Risk Controls

- [x] Set max risk per trade.
- [x] Set max risk per window.
- [x] Set max unresolved exposure across all open windows.
- [x] Set daily loss cap.
- [x] Set total canary stop-loss.
- [x] Set max open orders.
- [x] Set max concurrent positions.
- [x] Confirm these fail closed in live mode.

Acceptance:

- The bot must refuse to submit a new live order once any hard cap is breached.

### 6. Observability And Ops

- [x] Keep the fixed dashboard path as the launch dashboard.
- [ ] Confirm engine scores, signal regime, confidence, and trade regime are truthful on the launch dashboard.
- [x] Add or verify alerts for stale feed, repeated order failure, reconciliation mismatch, drawdown breach, and auth failure.
- [x] Confirm a single-instance lock is active for the live wallet.
- [x] Write the canary runbook: start, stop, kill switch, cancel-all, reconcile, restart.

Acceptance:

- An operator can tell within seconds whether the bot is healthy, stale, disconnected, or blocked by a safety gate.

## P1 Strongly Recommended Before Unattended Live

These are not required for the very first supervised tiny canary, but they should be done before any unattended or scaled live session.

- [ ] External alert delivery, not local files only
- [ ] Service supervision with restart policy
- [ ] Reproducible dependency lockfile
- [ ] Release tagging and rollback procedure
- [ ] Structured deployment docs for staging and live
- [ ] Secret handling stronger than ad hoc local env usage
- [ ] Candidate-to-outcome research slices by regime, price bucket, liquidity bucket, and time bucket

## Shadow Signoff Session

Run a fresh shadow session and do not reuse the stale one.

Checklist:

- [ ] Use the intended live asset only
- [ ] Use the intended live track only
- [ ] Use the intended live model only
- [ ] Capture runtime status, dashboard screenshots if useful, and ledgers
- [ ] Review all warnings and alert events after the session
- [ ] Review every trade from the session manually

Required pass conditions:

- [ ] Feed remained healthy enough to trade
- [ ] No stale-price trade was opened
- [ ] No reconciliation mismatch occurred
- [ ] Net PnL stayed positive after estimated fees
- [ ] Drawdown stayed inside the canary budget
- [ ] The session still looks good after removing obviously distorted fills or stale periods

## Tiny Live Canary Plan

Only do this after all `P0` items are complete.

### Scope

- [ ] Burner wallet only
- [ ] `BTC` only
- [ ] `A` / `CONVICTION_ONLY` only
- [ ] Smallest meaningful bankroll only
- [ ] One supervised session only
- [ ] Operator present for the whole session

### During The Canary

- [ ] Watch the dashboard continuously
- [ ] Watch alerts continuously
- [ ] Manually inspect every submission and fill event
- [ ] Stop immediately on feed staleness, reconciliation mismatch, repeated cancellations, or unexpected exposure

### Canary Exit Criteria

- [ ] Real orders reconcile correctly
- [ ] No duplicate orders
- [ ] No unexplained open exposure after stop
- [ ] Fill quality is acceptable versus expected price
- [ ] Net result is not invalidated by fees or execution drag

## Concrete Work Plan

### Phase 1. Reset And Stabilize

Goal: stop using stale evidence and produce a clean signoff run.

- [x] Stop the stale active run
- [x] Start a fresh shadow run
- [x] Confirm fresh ledgers and fresh status updates
- [ ] Monitor feed health for a full session

Deliverable:

- One clean shadow run with healthy logs and trustworthy dashboard state

### Phase 2. Feed Hardening

Goal: fail closed when market data is stale.

Files likely involved:

- [bot.py](bot.py)
- [feeds/binance.py](feeds/binance.py)
- [state/session.py](state/session.py)
- [webapp/manager.py](webapp/manager.py)

- [ ] Define freshness thresholds
- [x] Block new entries on stale price data
- [x] Block new entries on stale book data
- [x] Surface freshness state in runtime payload and dashboard

Deliverable:

- The bot visibly refuses to trade stale data

### Phase 3. Live Path Proof

Goal: prove the current live lifecycle works end to end at tiny size.

Files likely involved:

- [bot.py](bot.py)
- [market/orderer.py](market/orderer.py)
- [persistence/ledger.py](persistence/ledger.py)
- [state/position.py](state/position.py)

- [ ] Rehearse startup preflight
- [ ] Rehearse reconciliation
- [ ] Rehearse kill switch
- [ ] Execute one tiny supervised live canary

Deliverable:

- One real live ledger proving truthful order state handling

### Phase 4. Promotion Decision

Goal: decide whether to stay in shadow, repeat canary, or scale slightly.

- [ ] Write a short signoff note
- [ ] Compare expected versus realized execution quality
- [ ] Confirm whether `A` remains the production candidate
- [ ] Decide one of:
  - stay shadow-only
  - repeat tiny canary
  - scale slightly

Deliverable:

- Explicit go / no-go decision with evidence

## Definition Of Ready

The bot is ready for a tiny supervised live canary only when all of the following are true:

- [ ] The active evidence comes from a fresh run, not a stale recovered process
- [ ] Feed stability is acceptable
- [ ] Stale-data guards fail closed
- [ ] One production candidate is frozen
- [x] Live risk caps are set and tested
- [ ] Dashboard truth matches ledger truth
- [ ] Preflight, reconciliation, and kill switch are tested
- [ ] Operator runbook exists

The bot is ready for unattended or scaled live only when:

- [ ] The tiny canary passed
- [ ] Feed and operations stayed stable
- [ ] Real fill quality was acceptable
- [ ] No reconciliation surprises occurred
- [ ] P1 operations work is complete

## Progress Notes

2026-05-01 progress:

- New clean shadow run started: `20260501_004515`
- Dashboard refreshed on `http://127.0.0.1:8789`
- Stale BTC price now fails closed by default
- Runtime status and dashboard now expose BTC and Polymarket book freshness

2026-05-03 progress:

- Dashboard relaunched on `http://127.0.0.1:8789`
- Waiting-window path now reports explicit Polymarket book staleness instead of vague no-signal behavior
- Snipe selection now records `STALE_POLYMARKET_BOOK` / `ORDER_BOOK_UNAVAILABLE` candidate reasons and refuses new entries on stale book state
- Added throttled alerts for stale BTC snapshot failure and stale/unavailable Polymarket book conditions
- Added [docs/shadow_signoff_2026-05-03.md](docs/shadow_signoff_2026-05-03.md) with commit, config, run id, and dashboard metadata
- Added [docs/live_canary_runbook.md](docs/live_canary_runbook.md) for start/stop/kill-switch/cancel-all/reconcile/restart procedures

2026-05-12 progress:

- Added fail-closed canary caps for open orders, concurrent positions, unresolved exposure, per-window exposure, and total drawdown in [config.py](config.py) and [risk/guard.py](risk/guard.py)
- Added operator alerts for repeated order failures, auth failures, and guard-breach drawdown/risk-cap blocks in [bot.py](bot.py)
- Added regression coverage for the new guard caps and alert escalation in [tests/test_risk.py](tests/test_risk.py) and [tests/test_bot_models.py](tests/test_bot_models.py)
- The current shadow run still does not pass feed-stability signoff; its log shows repeated Coinbase/Binance disconnects and price snapshot failure bursts, so `Feed Stability` remains open
- Added [docs/shadow_no_go_2026-05-12.md](docs/shadow_no_go_2026-05-12.md) to record the explicit no-go decision for run `20260501_004515`
- Replaced the old Binance/Coinbase-only reference pricing path with a Kraken/Gemini/Coinbase quorum path in [bot.py](bot.py), [state/session.py](state/session.py), [feeds/kraken.py](feeds/kraken.py), and [feeds/gemini.py](feeds/gemini.py)
- New runtime freshness now exposes healthy-source count, source names, quorum status, and cross-feed divergence
- A fresh shadow session is running with the new quorum logic; current runtime status shows `3` healthy BTC sources, quorum met, and low divergence
