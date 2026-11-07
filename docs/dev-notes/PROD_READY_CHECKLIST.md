# Production Readiness Checklist

Status date: 2026-04-06

Current recommendation: `SHADOW LIVE ONLY`

This checklist is designed to be executed by multiple parallel agents or owners. Do not enable meaningful real-money trading until every `P0` item is complete and the launch gates at the bottom are signed off.

## Workstreams

### Agent A: Strategy, Data, and Validation

Mission: prove that the strategy has a real edge after fees, slippage, and missed fills.

Exit criteria:
- Positive expectancy on real shadow-live data, net of fees and realistic execution assumptions
- Promotion memo for exactly one model and one asset
- Clear "trade" and "do not trade" rules for live mode

Checklist:
- [ ] Keep forced per-interval trading disabled for live mode (`LIVE_FORCE_INTERVAL_TRADES=False`) and avoid re-enabling it without evidence.
- [ ] Turn off automatic hedge-by-default for live mode unless it is statistically justified net of fees.
- [ ] Define one production model only. Do not launch `A,B,C` together in live mode.
- [ ] Build a real shadow-live dataset with one row per decision: timestamp, asset, market slug, model, open price, live price, order-book snapshot, theoretical probability, market price, fee estimate, chosen side, final outcome, and realized PnL.
- [ ] Add a replay/backtest harness that uses recorded real Polymarket windows and real Binance prices instead of the synthetic simulator.
- [ ] Score the strategy on net PnL, average edge, fill rate, slippage, max drawdown, trade frequency, and confidence intervals.
- [ ] Define a hard promotion gate for live trading, for example: minimum sample size, positive net PnL, acceptable drawdown, and stable behavior across days and volatility regimes.
- [ ] Fix Model C calibration so it uses real stored features instead of an incomplete fallback path.
- [ ] Store calibration features required by `strategy/probability.py`, including `move_pct_bin` and `time_bin`, in the persistent ledger or a dedicated feature store.
- [ ] Version strategy parameters and calibration data so a live run can always be traced back to the exact model build.

### Agent B: Exchange Execution and Reconciliation

Mission: make live execution correct, controlled, and restart-safe.

Exit criteria:
- Startup preflight validates the actual account before trading
- Orders are placed with an execution policy appropriate for short-lived markets
- Restarts do not lose track of exposure, fills, or open orders

Checklist:
- [ ] Add a startup preflight for live mode: fetch and validate wallet balance, allowance, open orders, and current positions before the first trade.
- [ ] Replace the local-only bankroll as the source of truth in live mode with actual exchange or wallet state.
- [ ] Add explicit minimum-balance and allowance checks before every live order.
- [ ] Replace the current `market_price + 0.01` order path with a deliberate execution policy: market/FOK, IOC, or depth-aware limit orders with a slippage cap.
- [ ] Validate tick size, minimum order size, and neg-risk metadata from the market before placing orders.
- [ ] Add client-order-id or another idempotency mechanism so retries cannot duplicate live orders.
- [ ] Add rate-limit-aware retry and backoff behavior for all Polymarket and Binance requests.
- [ ] Add a startup reconciliation step that can import open orders and already-filled trades into runtime state after a crash or restart.
- [ ] Add an optional startup `cancel_all` mode for stale orders left behind by previous runs.
- [ ] Track partial fills explicitly. Do not treat every live order as either "filled" or "cancelled" only.
- [ ] Persist the last known order state durably so the bot can resume correctly after process death.

### Agent C: Risk, Portfolio, and Capital Controls

Mission: make sure the bot cannot blow through capital because of one bug, bad regime, or exchange mismatch.

Exit criteria:
- Live risk controls are based on real exposure and real account state
- All critical risk rules fail closed
- Canary bankroll is small and isolated from primary capital

Checklist:
- [ ] Introduce a separate live config profile with safer defaults than demo mode.
- [ ] Cap maximum capital at risk per trade, per market window, per model, per asset, and for the entire wallet.
- [ ] Compute risk from both realized and unresolved exposure. A trade that has not resolved still consumes risk.
- [ ] Add a hard emergency kill switch that can stop new order creation without redeploying code.
- [ ] Add a drawdown circuit breaker based on actual account equity, not just local session PnL.
- [ ] Add a max-open-orders and max-concurrent-windows rule.
- [ ] Add a no-trade rule when feed lag, market discovery mismatch, or account reconciliation is stale.
- [ ] Use a burner wallet only for the first live canary. Do not use primary funds.
- [ ] Keep one asset only for the first live launch.
- [ ] Keep one model only for the first live launch.
- [ ] Disable hedging in the first live launch unless shadow-live data proves it improves net PnL.

### Agent D: SRE, Observability, and Operations

Mission: make the bot operable, debuggable, and safe to run unattended.

Exit criteria:
- There is a reproducible deploy path
- There are metrics and alerts for every failure mode that matters
- There is a written runbook for outages and recovery

Checklist:
- [ ] Add structured logs with stable fields for market slug, asset, model, order id, order state, decision reason, and error class.
- [ ] Add metrics for price staleness, market discovery failures, signal counts, order attempts, fill rate, cancellation rate, average slippage, unresolved trades, PnL, drawdown, and API errors.
- [ ] Add alerts for stale price feed, missing live market, repeated order failures, repeated cancellations, reconciliation mismatch, drawdown breach, and auth failure.
- [ ] Add a single-instance lock so two processes cannot trade the same wallet at once.
- [ ] Add a supervisor or service wrapper with restart policy and crash reporting.
- [ ] Add a health endpoint or heartbeat file for the running process.
- [ ] Add CI that runs tests on every change before live deployment.
- [ ] Pin dependencies exactly and introduce a lockfile or fully reproducible environment.
- [ ] Add deployment docs for local, staging, and live environments.
- [ ] Add an operator runbook for start, stop, restart, cancel-all, reconcile, rotate keys, and recover after crash.

### Agent E: Security, Governance, and Compliance

Mission: reduce the chance of losing money to bad secrets handling, unsafe releases, or non-technical issues.

Exit criteria:
- Live secrets are not managed ad hoc
- Release and rollback are controlled
- Jurisdiction, tax, and account ownership questions are answered before scaling

Checklist:
- [ ] Move live secrets out of ad hoc `.env` handling into a safer local secret store or secret manager.
- [ ] Ensure only the burner wallet private key is present in the live runtime environment.
- [ ] Add release tagging so each live run can be tied to an exact code revision and config version.
- [ ] Add a rollback procedure that is tested before the first live canary.
- [ ] Record every live config change in a changelog.
- [ ] Review Polymarket account setup, wallet ownership, and funder configuration before first live trade.
- [ ] Review tax, record-keeping, and jurisdiction obligations before scaling any profitable setup.

## Current Repo Blockers

These are the main issues visible in the current codebase.

- [ ] `P0` The repo still defaults to forced interval trading and default hedging for runtime stress tests. That is not a production entry policy.
- [ ] `P0` Live bankroll is tracked locally in memory, but there is no live balance, allowance, open-order, or position reconciliation.
- [ ] `P0` Live order placement currently uses a naive `market_price + 0.01` limit price instead of a production execution policy.
- [ ] `P0` There is no restart-safe order journal or startup reconciliation path for live exposure.
- [ ] `P1` Resolution and PnL bookkeeping depend on a timeout-based Gamma poll path instead of authoritative post-trade account reconciliation.
- [ ] `P1` Model C is not fully wired for calibration because the ledger does not persist the required calibration features.
- [ ] `P1` The local runtime operations layer is only partially built: there is now a browser control plane, a structured alert sink, and a one-process lock, but there is still no CI, no service wrapper, no metrics backend, and no external alert routing configured by default.
- [ ] `P1` Dependencies are only range-pinned in `requirements.txt`, which is weak for production repeatability.

## Multi-Agent Audit Findings

These findings came from parallel audits of execution, models, and operations.

### Execution and Account State

- [ ] `P0` Add live preflight checks for balance, allowance, open orders, and positions before the bot can place its first real order.
- [ ] `P0` Replace the current live order path in `market/orderer.py` that prices every order as `market_price + 0.01`. Short-lived 5-minute markets need a deliberate execution policy with slippage limits.
- [ ] `P0` Add partial-fill handling. The current runtime mostly treats orders as either filled or cancelled, which is too coarse for production accounting.
- [ ] `P0` Add idempotent order submission so retries cannot duplicate orders.
- [ ] `P0` Add startup recovery that reconstructs live exposure from the exchange account and local ledger after crash or restart.
- [ ] `P1` Add a startup `cancel_all` / stale-order cleanup mode for live operations.

### Strategy and Model Validation

- [ ] `P0` Fix Model B so it receives a real rolling volatility input. Right now it falls back to Model A behavior because the live trading loop does not pass volatility.
- [ ] `P0` Fix Model C so it trains on persisted feature data instead of falling back to Model A. The ledger currently does not store the required calibration fields.
- [ ] `P0` Remove the hardcoded `trades.jsonl` dependency from Model C and pass the real runtime data source explicitly.
- [ ] `P1` Build a real replay/backtest dataset from Binance and Polymarket history. The current simulator is useful for plumbing, not for statistical promotion.
- [ ] `P1` Expand the ledger schema so every decision records model inputs, market state, execution quality, latency, and fee assumptions.

### Operations and Safety

- [ ] `P0` Add an emergency kill switch checked during the live loop, not just a startup `YES` prompt.
- [ ] `P0` Add graceful `SIGINT`/`SIGTERM` handling so the bot can stop cleanly, reconcile, and flush state on shutdown.
- [ ] `P0` Add health checks, heartbeats, metrics, and alerts for feed staleness, auth failures, order failures, reconciliation mismatch, and drawdown.
- [ ] `P1` Replace console-only logging with structured logs and durable storage.
- [ ] `P1` Add a reproducible deployment target with single-instance locking and supervised restart behavior.
- [ ] `P1` Move live secrets out of ad hoc `.env` usage before running unattended real-money sessions.

## Launch Gates

### Gate 0: Code Hardening

All of these must be complete before any meaningful real-money run:

- [ ] All `P0` checklist items are complete
- [ ] Live mode has a dedicated safe config profile
- [ ] Startup preflight and reconciliation are implemented
- [ ] Execution policy has slippage caps and idempotency
- [ ] Emergency stop works and has been tested
- [ ] Exact dependency versions are pinned

### Gate 1: Shadow-Live Signoff

- [ ] Run shadow-live continuously for at least 2 weeks on the intended asset and model
- [ ] Capture every signal, order-book decision, and final market outcome
- [ ] Show positive net expectancy after fees
- [ ] Show acceptable drawdown and stable behavior across quiet and volatile sessions
- [ ] Freeze the exact live strategy configuration

### Gate 2: Live Canary

- [ ] Use a burner wallet only
- [ ] Start with one asset, one model, and a very small bankroll
- [ ] Set a hard daily loss cap and a hard total bankroll stop
- [ ] Have live alerts active before the first real order
- [ ] Review every live fill manually for the first several sessions
- [ ] Do not scale capital during the canary period

### Gate 3: Scale-Up

- [ ] Only scale after the canary stays profitable net of fees and slippage
- [ ] Increase capital in small steps, not all at once
- [ ] Re-run risk review before enabling a second asset or a second model
- [ ] Reconfirm that monitoring and reconciliation still work under higher order volume

## Recommended Order of Work

1. Execution and reconciliation
2. Live risk controls
3. Shadow-live data capture and validation
4. Observability and operations

Current state:
- [x] Local web control plane for starting, stopping, and inspecting runs
- [x] Machine-readable runtime heartbeat for external supervision
- [x] Structured local alerts and one-process lock
- [ ] Service supervision, metrics backend, and external alert delivery
5. Security and release process
6. Tiny live canary
7. Scale only after evidence

## Recommended Agent Assignment

If you want to push this toward production quickly, run the work in parallel like this:

- Agent A owns strategy validation, feature logging, replay/backtest, and model promotion criteria.
- Agent B owns account preflight, execution policy, order reconciliation, partial fills, and startup recovery.
- Agent C owns live risk controls, bankroll truth, exposure accounting, kill switch behavior, and canary limits.
- Agent D owns metrics, alerts, structured logs, deployment packaging, process supervision, and runbooks.
- Agent E owns secret handling, release tagging, rollback procedure, and compliance record keeping.

Critical path:
- Agent B first
- Agent C second
- Agent A in parallel with B/C
- Agent D once B/C interfaces are clear
- Agent E before first unattended live run

## Definition of "Prod Ready"

The bot is production ready only when all of the following are true:

- [ ] It can reconcile real account state on startup
- [ ] It places orders with a defined and tested execution policy
- [ ] It can survive restart without losing track of exposure
- [ ] It has positive shadow-live expectancy after fees and realistic fills
- [ ] It has alerts, logs, and a kill switch
- [ ] It is running with a small isolated bankroll and a written rollback plan

## External References

- Polymarket quickstart: <https://docs.polymarket.com/trading/quickstart>
- Polymarket L2 methods and authenticated trading flow: <https://docs.polymarket.com/developers/CLOB/clients/methods-l2>
- Polymarket fees: <https://docs.polymarket.com/trading/fees>
- Polymarket API rate limits: <https://docs.polymarket.com/api-reference/rate-limits>
- Official Python client: <https://github.com/Polymarket/py-clob-client/blob/main/README.md>
