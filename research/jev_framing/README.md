# JEV question-framing experiment

Question framing as an experiment: does the wording of the question change the JEV answer, do the
deterministic checks hold their weight against it, and what do latency and calls cost. This is a
small pilot on about 440 decision states from one trading period. Treat the numbers as a
direction, not a verdict.

## Result summary

| framing | dev Brier (60 states) | holdout Brier (139 states) | holdout acc@0.60 | holdout Brier diff vs baseline, 95% CI |
|---|---|---|---|---|
| r0_production (baseline, same wording as `strategy/jev.py`) | 0.255 | 0.217 | 0.633 | n/a |
| r2a_negated (asks for contradiction, inverts) | 0.196 | 0.197 | 0.734 | -0.020 [-0.073, +0.035] |
| r2c_neutral_wording (no "corroborate" framing) | 0.206 | 0.189 | 0.691 | -0.027 [-0.068, +0.015] |
| deterministic check (0/1 from `btc_confirmed`) | 0.200 | 0.425 | 0.576 | n/a |

Latency p50 about 145 to 160 ms, p95 about 230 to 250 ms per call on holdout (single-question calls).
Answer variance on identical states is small (mean range 0.02 to 0.03). Full tables: `LOG.md`.

**Bottom line:** the production wording is the weakest of the framings tested, and a
symmetric or neutral wording improves Brier on both dev and holdout. The holdout gain is not
statistically separable from zero with 18 windows. The deterministic check did not hold up
out of sample on this window set (holdout accuracy 0.58), but its 0/1 output is a poor probability
and this comparison is not a fair test of its ranking power.

## Method

**Data** (`dataset.py`). Two read-only trade logs (`trades_both.jsonl`, `trades_conviction.jsonl`,
located via `JEV_SOURCE_DIR`) are read for candidate decision records. They are deduplicated on
(window end, timestamp). The output `data/states.jsonl` holds numeric fields only: no order,
wallet, token, or market ids.

- Labels: `agree` is true when the strongest side matches the BTC move over the window
  (close above open for UP, below for DOWN). The close is the last candidate of the window
  (at most 15 s before end). This is a proxy for the settlement outcome, not the
  settlement itself. The `resolved_up` field in the `trades_both.jsonl` resolution records is always null.
- Features: `seconds_remaining`, `strongest_side`, `up/down_price`, `btc_confirmed` (deterministic
  check) are logged. The rest are **reconstructed** from the candidate series, not copied
  from the bot: `tick_move_pct` (percent move from window open), `directional_consistency`
  (share of BTC ticks on the same side as the current move), `acceleration_pct` (change in tick move over 30 s),
  and up/down price momentum (relative contract price change over 30 s). States without a 30 s
  lookback are skipped and counted.
- Sampling: one state per 30 s bucket per window (about 10 per window), 441 states in 57 windows.
- Split: chronological by window. First 70% of windows are dev (302 states, 39 windows), last 30% holdout
  (139 states, 18 windows).

**Harness** (`run.py`). A framing is a JSON file in `framings/` with `name`, `hypothesis`,
`question_type`, `answer_mode` (`agreement`, `p_up`, or `negated`, which maps the API number to
P(side corroborated)), `state_fields`, and `instructions` (template with `{strongest_side}` and
`{direction_word}`). The request has the same shape as `strategy/jev.py`: one `noul` question
named `btc_poly_agreement`, model `jev-latest`, endpoint `https://api.typesafe.ai/v1/systemone`.

- Cache: `cache/responses.jsonl`, keyed by a hash of the exact payload and a repeat index. It stores only
  the probability and latency. Errors are not cached.
- Budget: `JEV_MAX_CALLS` (default 1500) across the whole experiment, counted in `cache/budget.json`.
  Attempted calls count, cache hits do not. When the budget runs out, remaining states are marked
  `budget` and are not scored.
- Concurrency 4, timeout 10 s, latency recorded per call.
- Metrics: Brier, log loss (probabilities clipped to [0.001, 0.999]), accuracy at 0.5 and at the
  production 0.60 threshold, confirm rate and precision at 0.60, agreement with the deterministic
  check, 5-bin calibration table, latency p50/p95, error rate, call count.

**Agent loop.** Round 0 is the production framing. Each later round proposed 2 to 3 framings with
explicit hypotheses, ran them on a fixed dev sample of 60 states (evenly spaced over the 302 dev
states), and logged the metrics and decision in `LOG.md`. Four search rounds (0 to 3) were run.
Round 4 was not run because the spread between the top framings was inside the noise of a 60-state
sample. The two holdout candidates were fixed in the log before the holdout run. Each holdout framing
ran once on all 139 holdout states.

**Variance.** 20 dev states were asked 3 times each under different repeat indices, for the baseline and
the best neutral framing.

## Conclusions

- Wording matters. On the 60 dev states, the negated question differs from the production wording by more
  than 0.2 on 32% of states (mean absolute difference 0.135); the neutral wording does so on 15% (mean 0.116).
  Adding the contract price moves answers much less (mean 0.084, 2% of states above 0.2).
- Adding the strongest contract price helped on dev (r1c vs baseline: Brier 0.217 vs 0.255, same wording).
  It was not run on holdout, so this is not checked out of sample.
- Settlement-probability wording (`p_up`) beat the baseline on dev (0.231 vs 0.255) but did not beat the
  agreement wording with the price (0.217), nor the negated wording (0.196).
- The gains from combining changes did not add up on dev (round 3).
- Latency is about 150 ms p50 for a single question, which is cheap next to a 5-minute window.
  Cost was measured in calls only (1129 live calls); this run did not record billing data, so no
  per-call cost is reported.

## Limitations

- Small sample: 139 holdout states come from only 18 windows, and dev search used 60 states from 38 windows.
  The confidence intervals cross zero for both holdout comparisons.
- Labels are a proxy (close vs open from the logs), not the Polymarket settlement.
- Features are reconstructed from the candidate series, so they approximate the live bot's values.
- Model non-determinism: measured on 20 states with 3 answers each. Spread is small, but the
  sample is small.
- The deterministic check was scored as a 0/1 probability, which is harsh. A calibrated version
  of it would need its own evaluation.
- The dev search evaluated 60 of 302 dev states to fit the budget. Running all dev states
  would tighten the dev estimates; it was not run.

## Reproduce

```bash
export JEV_SOURCE_DIR=<directory with trades_both.jsonl and trades_conviction.jsonl>
python research/jev_framing/dataset.py                    # writes data/states.jsonl
export TYPESAFE_API_KEY=...                                # or keychain item typesafe-api, account sekun
python research/jev_framing/run.py run dev research/jev_framing/framings/r0_production.json
python research/jev_framing/run.py run dev research/jev_framing/framings/r2a_negated.json
python research/jev_framing/run.py run holdout research/jev_framing/framings/r0_production.json research/jev_framing/framings/r2c_neutral_wording.json
python research/jev_framing/run.py compare holdout r0_production r2c_neutral_wording
python research/jev_framing/run.py table
```

Set `JEV_MAX_CALLS` to override the budget. Delete `cache/` to rerun all calls live. The
cache directory is gitignored because it is local state. The API key is only read at runtime.
Tests: `python -m pytest -q tests/test_jev_framing.py` (no network).

Files: `dataset.py`, `run.py`, `LOG.md` (round-by-round log), `framings/*.json`,
`results/dev|holdout/*.json` (metrics and per-state probabilities), `results/variance_*.json`,
`data/states.jsonl`.
