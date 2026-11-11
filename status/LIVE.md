# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T03:36:37Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 7 (7) | 0.5714 (0.2505 to 0.8418) | 1.2559 | 0.1794 | needs 20+ settled |
| B · Conviction (brownian+micro) | 11 (11) | 0.4545 (0.2127 to 0.7199) | -1.6513 | -0.1501 | needs 20+ settled |
| C · Conviction (calibrated) | 13 (13) | 0.5385 (0.2914 to 0.7679) | -0.7899 | -0.0608 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (5.9 h) |
| Last heartbeat | 2026-11-11T03:36:37Z (age 0.1 s) |
| Feeds healthy | yes |
| Windows observed | 72 |
| Paper trades | 31 |
| Settled trades | 31 |
| Win rate | 0.5161 (Wilson 95%: 0.3484 to 0.6803) |
| Net PnL, paper (USDC) | -1.1853 after est. fees (gross 1.4353) |
| Net PnL 95% bootstrap (USDC) | -10.8499 to 6.7146 |
| Brier, model vs market (19 settled) | 0.1332 vs 0.1260 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
