# GPT-5.3 Codex Takeover Plan

This file is the handoff plan for a new OpenCode/Codex agent.

Your job is to take over this repo, continue the work end-to-end, and complete the remaining trading-bot improvement tasks with disciplined verification.

## Mission

Improve this BTC 5-minute Polymarket bot so it is:

1. execution-truthful,
2. genuinely comparable across tracks/models,
3. better instrumented for research,
4. more robust in shadow mode,
5. safer to promote toward live trading later.

Do not stop at analysis. Ship the changes in phases, verify them, and keep going unless you hit a real blocker.

## Working Style

- Parallelize aggressively when the work is independent.
- Read files in parallel.
- Use subagents in parallel for exploration/review if available.
- Prefer small, behavior-preserving steps.
- Run the smallest relevant tests after each phase, then run `pytest -q` at phase boundaries.
- Do not revert unrelated user changes.
- Ignore repo noise from `.web_runtime/`, `__pycache__/`, `*.jsonl`, `.venv/`, `source_copy/`, `source_copy.zip` unless directly needed.
- Do not commit unless the user explicitly asks.

## Current Repo State

These changes are already in place and should be treated as the current baseline:

### Already done

- Extracted helper modules from `bot.py`:
  - `ops/live_startup.py`
  - `ops/resolution.py`
  - `strategy/entry_helpers.py`
- Synced config/docs/tests for entry-band and force-trade defaults.
- Added root `.gitignore`.
- Made `pytest` work without `PYTHONPATH=.` via `tests/conftest.py`.
- Added shadow multi-track execution support:
  - `SHADOW_MULTI_TRACK_EXECUTION=True`
  - shadow runs can evaluate `CONVICTION_ONLY`, `MISPRICING_ONLY`, and `BOTH` in parallel.
- Added exchange-minimum order handling:
  - `market/orderer.py` now adjusts to exchange minimum size.
  - `bot.py` pre-checks minimum notional before entry.
- Added adaptive sizing / liquidity relaxation / scaled net-profit-floor logic.

### Current verified status

- Full suite currently passes:

```bash
pytest -q
```

Expected baseline at handoff: `179 passed`.

### Recent behavior observed in logs

- The old "below market minimum 5.00" failure is fixed.
- Shadow bot now uses `ALL_SHADOW` mode for per-track evaluation.
- Current strategy is mechanically working, but recent shadow performance is weak.
- A lot of current signal flow is still concentrated in `LATE_CONVICTION` behavior.

## Big Remaining Problems

These are the highest-value remaining tasks.

### 1. Live execution is still not fully truthful

The bot still treats submitted orders too much like filled orders in the main path.

Main files:

- `bot.py`
- `market/orderer.py`
- `persistence/ledger.py`
- `state/position.py`

What needs to happen:

- Split submit vs fill lifecycle.
- Do not create a real open position until actual fill confirmation is available.
- Use `wait_for_fill()` in the real path.
- Record pending/partial/cancelled/filled transitions explicitly.
- Do not locally mark positions closed unless the exit is confirmed.

### 2. A/B/C evaluation is still not truly fair

Tracks can now all run in shadow mode, but decision flow is still too shared.

Main files:

- `bot.py`
- `strategy/probability.py`
- `strategy/signal.py`

What needs to happen:

- Compute candidate decisions per track/model, not one shared selection that is effectively reused.
- Make sure `CONVICTION_ONLY`, `MISPRICING_ONLY`, and `BOTH` each have their own real entry decision path in shadow mode.
- Preserve live safety: live mode should still allow only one truly active execution track.

### 3. Research logging is not good enough

Right now, trade logging is much better than candidate logging.

Main files:

- `bot.py`
- `persistence/ledger.py`
- possibly add a new `persistence/candidates.py` or similar

What needs to happen:

- Log every evaluated candidate, not just executed trades.
- Include:
  - timestamp
  - track name
  - model A/B/C probabilities
  - selected probability
  - market price(s)
  - liquidity score
  - spread / cost estimates
  - reason code
  - skip reason
  - whether it became a trade
  - eventual outcome label if resolvable later

### 4. Book/microstructure features are too weak

Current logic mostly uses best price, counts, spread, and hand-built conviction features.

Main files:

- `market/client.py`
- `market/orderer.py`
- `state/session.py`
- `strategy/probability.py`
- `strategy/signal.py`

What needs to happen:

- Introduce size-aware features:
  - queue imbalance
  - order-flow imbalance if feasible
  - microprice
  - depth-to-fill target notional
- Prefer real size information over count-only proxies.

### 5. Calibration is still too coarse

`Model C`/probability logic still relies too much on buckets and heuristics.

Main files:

- `strategy/probability.py`
- calibration scripts in `scripts/` if present

What needs to happen:

- Add a better calibration path, ideally beta calibration or an equivalent post-hoc calibrator.
- Segment by regime and time remaining where sensible.
- Evaluate using Brier/log loss, not just PnL.

### 6. Reporting/UI still needs richer per-track evaluation

Main files:

- `webapp/manager.py`
- `webapp/server.py`
- `webapp/static/index.html`
- reporting scripts

What needs to happen:

- Show per-track research metrics, not just the currently easiest ledger/report.
- Add candidate-level and calibration metrics to the dashboard/reporting flow.

## Research Summary To Apply

Use these principles while implementing improvements:

### Limit order book / microstructure

- Queue imbalance and order-flow imbalance are strong short-horizon predictors.
- Best-level depth matters more than simple count heuristics.
- Spread alone is an incomplete cost model.

### Calibration / forecasting

- Well-calibrated probabilities matter as much as raw signal strength.
- Brier score and log loss are the right evaluation metrics.
- Forecast pooling usually beats naive single-model selection.

### Prediction markets

- Market price is informative, but not truth.
- Extreme prices can be biased / distorted.
- Expensive late-conviction entries deserve extra skepticism unless book quality confirms them.

### Risk / sizing

- Inventory-aware sizing matters.
- Same-side concentration and overlapping-window exposure should shrink risk.

## Research Findings To Steer The Models

These are not optional background notes. They should directly shape how the forecasting and selection logic evolves.

### 1. Treat Polymarket price as a prior, not as ground truth

Research implication:

- Prediction-market price contains information, but it is distorted by liquidity, risk transfer, noise flow, and tail biases.
- Extreme prices are especially suspect.

How to steer the models:

- Do not let the market price dominate raw model output by default.
- Build a pooled probability using:
  - model A
  - model B
  - model C
  - market-implied probability
- Use regime-aware weighting.
- In `LATE_CONVICTION`, cap how much market price can move the pooled estimate unless book quality is strong.
- Add explicit high-price / low-price caution bands.

Design rule:

- `market price = informative feature`
- not `market price = truth label`

### 2. Add asymmetric skepticism for extreme-price late entries

Research implication:

- Favorite-longshot style distortion means expensive contracts can be overbought and cheap contracts can behave nonlinearly.

How to steer the models:

- Penalize `LATE_CONVICTION` entries more as price moves toward extremes.
- Scale the penalty with:
  - one-sided book structure
  - weak depth on the strong side
  - parity distortion
  - widened spread
- Require stronger edge or better liquidity confirmation for high-price entries.

Practical starting rule:

- Add a dynamic edge floor or probability haircut once contract price is above the current main band.
- Make the penalty stronger when book quality is weak.

### 3. Replace count heuristics with size-aware microstructure features

Research implication:

- Queue imbalance, order-flow imbalance, and microprice have more short-horizon signal value than simple quote counts.

How to steer the models:

- Promote actual size-at-best and size-near-best into first-class features.
- Compute and log at least:
  - L1 queue imbalance
  - microprice
  - depth-to-fill target size
  - signed size imbalance if feasible
- Let these features influence both:
  - forecast probability
  - entry eligibility / execution risk

Design rule:

- If a signal is directionally strong but depth-to-fill is poor, forecast may still be fine but execution should be more conservative.

### 4. Optimize calibration separately from PnL

Research implication:

- A strategy can make money for the wrong reasons in a short sample.
- Calibration quality is a separate axis from profitability.

How to steer the models:

- Evaluate probabilities with:
  - Brier score
  - log loss
  - calibration/reliability curves
  - sharpness
- Do not accept a new probability model only because recent PnL improved.
- Prefer the model that is better calibrated if PnL is roughly tied.

Design rule:

- Forecast quality decides whether a model is trustworthy.
- PnL decides whether the execution policy is monetizing that quality.

### 5. Pool forecasters instead of forcing single-model dominance

Research implication:

- Heterogeneous probabilistic models are often best combined rather than used in isolation.

How to steer the models:

- Build a combined probability layer in `strategy/probability.py`.
- Prefer logit-space or otherwise well-behaved pooling.
- Allow weights to vary by:
  - time remaining
  - volatility regime
  - liquidity quality
  - price band

Practical rule:

- Track and report standalone A/B/C performance anyway.
- The pooled model should compete against A, B, and C as a real fourth forecast path.

### 6. Make model comparison candidate-based, not trade-based only

Research implication:

- Selection-biased datasets produce misleading conclusions.

How to steer the models:

- Log every candidate across all tracks.
- Compare models on the full opportunity set, not only on trades that passed current filters.
- Build evaluation slices by:
  - regime
  - time bucket
  - price bucket
  - liquidity bucket
  - asset

Design rule:

- A better model is one that scores better over all labeled opportunities, not merely one that happened to align with the current policy.

## Research Findings To Guide Execution

These findings should shape order placement, risk, and position management.

### 1. Execution cost must be depth-aware, not spread-only

Research implication:

- Spread is only a rough proxy for trading cost.
- True cost depends on available size at and through the book.

Execution guidance:

- Extend book handling so entry logic can estimate depth-to-fill target notional.
- Penalize entries when the target notional would walk the book materially.
- Record both:
  - quoted spread cost
  - depth-to-fill cost estimate

Do not rely on best bid/ask alone when the book is thin.

### 2. Tight books with strong imbalance deserve different treatment than thin one-sided books

Research implication:

- Strong directional pressure with real depth can be informative.
- One-sided books with weak refill often create false conviction.

Execution guidance:

- Keep the new liquidity relaxation only for cases with:
  - strong conviction,
  - tight strong-side spread,
  - enough strong-side depth,
  - not catastrophically bad weak-side structure.
- Never relax gates for completely broken books.

### 3. Inventory concentration should directly deform sizing and exits

Research implication:

- Inventory risk is a first-class control variable in finite-horizon trading.

Execution guidance:

- Reduce entry size as same-side exposure rises.
- Reduce entry size when multiple overlapping unresolved positions exist.
- Tighten exit urgency when concentration risk is elevated.
- Consider stronger cooldown or pause after clustered same-side losses.

### 4. Exit logic must optimize truthfulness first, cleverness second

Research implication:

- The biggest real trading failure mode is local state diverging from exchange truth.

Execution guidance:

- Prefer conservative, explicit exit state transitions.
- Never mark a position fully closed without actual sell confirmation.
- Separate `exit submitted` from `exit filled`.
- Build reconciliation hooks that can repair state safely.

### 5. Execution evaluation must include markouts, not just realized PnL

Research implication:

- Short-horizon strategies need immediate post-trade quality diagnostics.

Execution guidance:

- Log markouts after entry and exit where data allows:
  - 1 second
  - 5 seconds
  - 15 seconds
  - next decision tick
- Use these to detect whether the bot is systematically crossing into adverse selection.

### 6. Shadow-mode promotion should use evidence gates

Research implication:

- A few good or bad windows are not enough for promotion decisions.

Execution guidance:

- Define explicit promotion gates before changing live behavior.
- Require at minimum:
  - stable execution truthfulness
  - acceptable reconciliation mismatch count
  - better or neutral drawdown
  - better calibration
  - acceptable net PnL after estimated fees/slippage

## Model Steering Rules The Next Agent Should Follow

When changing model logic, follow these rules:

1. Prefer calibration-safe improvements over aggressive threshold tuning.
2. Do not tune on one recent shadow session.
3. Keep `LATE_CONVICTION` on a short leash until candidate-level data proves it deserves looser treatment.
4. Do not remove existing safety gates without replacement evidence.
5. Add observability first when a change affects selection or sizing.
6. Treat pooled probability as a serious candidate, not a cosmetic metric.
7. Separate forecasting improvements from execution improvements in experiments whenever possible.

## Concrete Experiments To Run

The next agent should implement or enable the infrastructure for these experiments.

### Experiment A: Per-track true comparison

- Compare `CONVICTION_ONLY`, `MISPRICING_ONLY`, `BOTH`, and eventually `POOLED`.
- Metrics:
  - trade count
  - win rate
  - net PnL
  - Brier
  - log loss
  - calibration by bucket

### Experiment B: Extreme-price penalty sweep

- Test additional `LATE_CONVICTION` penalties by price band.
- Measure whether high-price entries improve on net after fees and whether calibration improves.

### Experiment C: Depth-aware execution filter

- Compare current spread/count gating vs depth-to-fill-aware gating.
- Measure fill quality, adverse selection, and skipped-trade quality.

### Experiment D: Inventory-aware sizing

- Compare current sizing vs concentration-aware sizing.
- Measure drawdown, loss streak severity, and capital efficiency.

### Experiment E: Calibration upgrade

- Compare current bucket logic vs beta-calibrated probabilities.
- Use walk-forward splits only.

## Validation Standard For Research-Driven Changes

For any model or execution change, the next agent should try to report:

- what changed,
- which metric improved,
- which metric regressed,
- whether the change is forecast-quality, execution-quality, or risk-quality driven,
- whether the change is safe for shadow only or worthy of live consideration later.

No major trading logic change should be considered complete without both:

- test coverage,
- evidence from logged data or replay/shadow analysis.

## Execution Plan

Complete the work in this order.

## Phase 1: Make Execution Truthful

### Goal

Make order/position state represent reality.

### Tasks

1. Refactor entry lifecycle.
2. Separate `submitted`, `pending`, `filled`, `cancelled`, `failed`, `partial` states.
3. Only create `Position` after fill confirmation.
4. Record actual fill metadata in the normal path.
5. Ensure exits are also fill-confirmed before closing local state.
6. Add defensive desync handling if exchange state cannot confirm the local assumption.

### File targets

- `bot.py`
- `market/orderer.py`
- `persistence/ledger.py`
- `state/position.py`
- `state/session.py`

### Parallelization suggestion

- Agent/workstream A: order lifecycle refactor.
- Agent/workstream B: ledger schema/update path.
- Agent/workstream C: tests for pending/partial/fill/cancel behavior.

### Tests

Add/extend tests for:

- pending order does not create live position immediately
- partial fill creates correct partial exposure
- cancelled order does not become an open position
- exit failure does not silently mark closed
- expiry path does not desync local vs exchange state

Run at minimum:

```bash
pytest -q tests/test_orderer.py tests/test_bot_models.py tests/test_ledger.py
pytest -q
```

### Acceptance criteria

- No code path assumes fill on submit in live mode.
- All fills/exits write explicit fill records.
- Full suite green.

## Phase 2: Make Per-Track Evaluation Real

### Goal

A, B, and C should be fairly comparable in shadow mode.

### Tasks

1. Compute model-specific candidate decisions per track.
2. Remove hidden shared-selection bias.
3. Keep live mode single-track execution only.
4. Ensure runtime status and ledgers clearly separate track behavior.

### File targets

- `bot.py`
- `strategy/probability.py`
- `strategy/signal.py`
- `webapp/manager.py`

### Parallelization suggestion

- Workstream A: decision path refactor in `bot.py`.
- Workstream B: reporting/status payload separation.
- Workstream C: regression tests for track divergence and live single-track safety.

### Tests

Add/extend tests for:

- all three tracks active in shadow mode
- only configured track active in live mode
- `CONVICTION_ONLY` only trades conviction regime
- `MISPRICING_ONLY` only trades mispricing regime
- `BOTH` can trade both
- tracks diverge when models differ

Run at minimum:

```bash
pytest -q tests/test_bot_models.py tests/test_web_manager.py
pytest -q
```

### Acceptance criteria

- Per-track trade counts and decisions are meaningfully distinct when inputs differ.
- Dashboard/status reflects true per-track behavior.

## Phase 3: Add Candidate Logging And Research Metrics

### Goal

Create the data needed to actually learn from this bot.

### Tasks

1. Add a candidate-evaluation log.
2. Log every considered setup, not just actual trades.
3. Record skip reason / rejection reason.
4. Add utilities to join candidates with resolved outcomes.
5. Add evaluation metrics:
   - Brier score
   - log loss
   - calibration error / reliability buckets
   - trade acceptance rate by edge bucket
   - realized vs expected EV

### File targets

- `bot.py`
- `persistence/ledger.py` or new persistence module
- report scripts
- `webapp/manager.py`

### Parallelization suggestion

- Workstream A: candidate persistence.
- Workstream B: resolution join / metrics computation.
- Workstream C: dashboard/report exposure of metrics.

### Tests

Add/extend tests for:

- candidate log writes expected schema
- skipped candidates preserve reason code
- resolved candidates join to outcomes correctly
- metrics code handles empty datasets and mixed regimes

Run at minimum:

```bash
pytest -q tests/test_bot_models.py tests/test_report_performance.py tests/test_ops.py
pytest -q
```

### Acceptance criteria

- You can produce a per-track research summary from recorded data.
- Candidate-level analysis is possible without scraping raw logs.

## Phase 4: Improve Microstructure Features

### Goal

Replace weak count-only signals with size-aware microstructure features.

### Tasks

1. Propagate size/depth information from market data layer.
2. Add queue imbalance.
3. Add microprice.
4. Add depth-to-fill target notional.
5. If feasible, add signed order-flow imbalance from updates.
6. Use these features in probability and entry gating.

### File targets

- `market/client.py`
- `market/orderer.py`
- `state/session.py`
- `strategy/probability.py`
- `strategy/signal.py`

### Parallelization suggestion

- Workstream A: data plumbing for depth/size.
- Workstream B: feature engineering in session state.
- Workstream C: strategy consumption + tests.

### Tests

Add/extend tests for:

- queue imbalance calculation
- microprice calculation
- depth-to-fill computation
- probability/selection behavior changes when size imbalance changes

Run at minimum:

```bash
pytest -q tests/test_session.py tests/test_probability.py tests/test_signal.py tests/test_orderer.py
pytest -q
```

### Acceptance criteria

- New features exist in runtime state and candidate logs.
- Strategy can use them in a controlled way.

## Phase 5: Upgrade Calibration And Probability Combination

### Goal

Turn A/B/C into a better calibrated forecasting stack.

### Tasks

1. Add post-hoc calibration path, ideally beta calibration.
2. Add regime-aware calibration splits.
3. Build pooled probability from A/B/C plus market-implied probability.
4. Gate by calibration quality in evaluation, not just PnL.

### File targets

- `strategy/probability.py`
- calibration/report scripts
- tests

### Parallelization suggestion

- Workstream A: calibration implementation.
- Workstream B: probability pooling logic.
- Workstream C: evaluation scripts/tests.

### Tests

Add/extend tests for:

- calibrator monotonicity / sane bounds
- pooled probability remains in `(0,1)`
- regime-specific calibration fallback behavior
- backward-safe behavior when no calibration data exists

Run at minimum:

```bash
pytest -q tests/test_probability.py tests/test_signal.py
pytest -q
```

### Acceptance criteria

- Calibration metrics are measurable and exposed.
- Probability path is clearly better structured than the current bucket-only approach.

## Phase 6: Improve Reporting And UI

### Goal

Make the dashboard useful for model comparison.

### Tasks

1. Expose per-track reports from all ledgers.
2. Show candidate metrics and calibration metrics in UI/API.
3. Show reasons for skip/reject more clearly.
4. Add cost/slippage/fee and fill-quality metrics.

### File targets

- `webapp/manager.py`
- `webapp/server.py`
- `webapp/static/index.html`

### Tests

Add/extend tests for:

- status payload includes per-track report fields
- UI-facing API returns live-updating per-track metrics
- empty and partially populated runs render safely

Run at minimum:

```bash
pytest -q tests/test_web_manager.py tests/test_bot_models.py
pytest -q
```

### Acceptance criteria

- The dashboard can compare A vs B vs C directly.
- A user can inspect not only PnL, but calibration and decision quality.

## Phase 7: Research-Driven Strategy Tightening

Only do this after Phases 1-6 are in good shape.

### Tasks

1. Add stronger extreme-price skepticism in `LATE_CONVICTION`.
2. Add inventory-aware sizing / concentration-aware risk.
3. Revisit entry and time-exit logic using candidate-level data.
4. Test pooled-probability vs single-model selection.

### Important

Do not blindly tune thresholds off one recent shadow session.
Require at least candidate-log-based walk-forward analysis or a replay dataset.

## Testing Discipline

After every meaningful phase:

```bash
pytest -q
```

Also run focused tests before the full suite.

If you add scripts or metrics code, add tests where practical.

If the dashboard/API contract changes, verify manually too:

```bash
curl -s http://127.0.0.1:8787/api/status
```

If the dashboard server is running, make sure the status payload still updates live and track panels remain stable.

## Deliverables Expected

By the end of takeover work, produce:

1. code changes,
2. tests,
3. a short summary of each completed phase,
4. a list of remaining risks,
5. exact verification results.

## Suggested First Move

Start with Phase 1 and Phase 2 planning/exploration in parallel.

Recommended opening actions:

1. inspect current `submit_trade()` and position lifecycle,
2. inspect current per-track selection flow,
3. write a small implementation todo list,
4. ship the truthful-execution refactor first,
5. then make per-track model comparison real.

## Final Reminder

Do not settle for another analysis-only pass.
Implement, test, and carry the work through.
