# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T14:20:56Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 42 (42) | 0.3810 (0.2500 to 0.5319) | -8.7552 | -0.2085 | -20.8609 to 1.4618 |
| B · Conviction (brownian+micro) | 62 (62) | 0.4355 (0.3194 to 0.5591) | -6.7462 | -0.1088 | -17.2327 to 3.4779 |
| C · Conviction (calibrated) · hold to settle | 51 (51) | 0.6471 (0.5099 to 0.7637) | -11.3333 | -0.2222 | -35.1086 to 10.3645 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (16.6 h) |
| Last heartbeat | 2026-11-11T14:20:55Z (age 1.0 s) |
| Feeds healthy | yes |
| Windows observed | 201 |
| Paper trades | 155 |
| Settled trades | 155 |
| Win rate | 0.4903 (Wilson 95%: 0.4128 to 0.5683) |
| Net PnL, paper (USDC) | -26.8347 after est. fees (gross -13.8329) |
| Net PnL 95% bootstrap (USDC) | -56.0300 to 0.0784 |
| Brier, model vs market (95 settled) | 0.1446 vs 0.1376 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
