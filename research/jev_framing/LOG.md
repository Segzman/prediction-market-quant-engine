# JEV question-framing agent loop: log

Dev search set: 60 states, evenly sampled from the 302 dev states (chronological first 70% of windows).
Every framing runs on the same 60 states. Labels: `agree` = the strongest side matched the BTC
window direction (close vs open, proxy). Deterministic check on the same 60 states: Brier 0.200,
log loss 1.382, accuracy 0.80 (0/1 probability from `btc_confirmed`).
Metrics from `run.py run dev`; per-framing JSON in `results/dev/`. Budget counter: `cache/budget.json`.

## Round 0: baseline

- Framing: `framings/r0_production.json` (production wording, 7 production state fields).
- Brier 0.2547, log loss 0.766, acc@0.5 0.667, acc@0.60 0.517, det agreement 0.70, p50 157 ms, p95 461 ms.
- Observations: worse Brier than the deterministic check (0.200). The 0.0-0.2 bin is inverted
  (5 states, observed 0.80). Confirm rate at 0.60 is 57%, precision 0.71.
- Decision: try wording, feature-count, and market-price variants (round 1).

## Round 1: three hypotheses

| framing | hypothesis | Brier | log loss | acc@0.60 | det agree |
|---|---|---|---|---|---|
| r1a_settlement_pup | settlement probability is better calibrated than agreement wording | 0.2313 | 0.656 | 0.533 | 0.70 |
| r1b_fewer_features | fewer features reduce noise from reconstructed momentum terms | 0.2326 | 0.689 | 0.733 | 0.77 |
| r1c_with_market_price | adding the strongest contract price anchors the judgment | 0.2172 | 0.666 | 0.733 | 0.77 |

- Observations: all three beat round 0 on Brier. The market price helped most. Fewer features
  improved accuracy at 0.60 but not Brier.
- Decision: keep the market price, test symmetry and calibration wording (round 2).

## Round 2: three hypotheses

| framing | hypothesis | Brier | log loss | acc@0.60 | det agree |
|---|---|---|---|---|---|
| r2a_negated | asking the contradiction question and inverting tests symmetry | 0.1960 | 0.587 | 0.733 | 0.75 |
| r2b_calibration_anchor | a scale anchor (0.5 = cannot tell) reduces extremes | 0.2131 | 0.649 | 0.767 | 0.77 |
| r2c_neutral_wording | neutral wording shows whether the framing words carry the signal | 0.2058 | 0.607 | 0.733 | 0.77 |

- Observations: r2a is the best dev Brier and log loss, and matches the deterministic check
  (0.200). The negated wording gives a different answer from the direct wording on the same state,
  so the model is wording-sensitive. Caveat: this is one 60-state sample.
- Decision: combine the best changes (round 3).

## Round 3: combinations

| framing | hypothesis | Brier | log loss | acc@0.60 | det agree |
|---|---|---|---|---|---|
| r3a_negated_neutral | gains from negation and neutral wording are additive | 0.2067 | 0.601 | 0.717 | 0.73 |
| r3b_negated_fewer | with symmetric wording, momentum and acceleration add nothing | 0.2133 | 0.637 | 0.733 | 0.75 |
| r3c_pup_market_price | settlement wording benefits from the price anchor too | 0.2067 | 0.605 | 0.683 | 0.73 |

- Observations: no combination beat r2a on Brier or log loss. Differences of about 0.01 Brier
  are within the noise of 60 states (rough standard error about 0.02), so ranks below the top are
  not separable.
- Decision: stop the search after round 3 (4 rounds including round 0, 13 framings in total).
  The planned round 4 was not run because the remaining gains are inside the noise. Holdout
  candidates are fixed here, before any holdout run: r2a_negated (best dev Brier and log loss)
  and r2c_neutral_wording (second-best dev Brier, third-best log loss; it is the only other
  framing close to the top on both metrics). Baseline: r0_production.
