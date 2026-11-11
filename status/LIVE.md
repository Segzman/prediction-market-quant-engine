# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T05:02:29Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 19 (19) | 0.5263 (0.3171 to 0.7267) | 1.6499 | 0.0868 | needs 20+ settled |
| B · Conviction (brownian+micro) | 26 (26) | 0.4231 (0.2554 to 0.6105) | -2.4017 | -0.0924 | -8.9689 to 4.1881 |
| C · Conviction (calibrated) · hold to settle | 26 (25) | 0.5600 (0.3707 to 0.7333) | 1.4531 | 0.0581 | -6.6705 to 8.5307 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (7.3 h) |
| Last heartbeat | 2026-11-11T05:02:28Z (age 1.3 s) |
| Feeds healthy | yes |
| Windows observed | 89 |
| Paper trades | 71 |
| Settled trades | 70 |
| Win rate | 0.5000 (Wilson 95%: 0.3860 to 0.6140) |
| Net PnL, paper (USDC) | 0.7013 after est. fees (gross 6.5209) |
| Net PnL 95% bootstrap (USDC) | -10.6609 to 12.3846 |
| Brier, model vs market (45 settled) | 0.1500 vs 0.1408 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
