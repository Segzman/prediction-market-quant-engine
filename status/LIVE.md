# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T04:47:24Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 16 (16) | 0.5625 (0.3318 to 0.7690) | 2.9669 | 0.1854 | needs 20+ settled |
| B · Conviction (brownian+micro) | 23 (23) | 0.4348 (0.2563 to 0.6319) | -1.0847 | -0.0472 | -7.4277 to 4.6568 |
| C · Conviction (calibrated) · hold to settle | 23 (23) | 0.5217 (0.3296 to 0.7076) | -0.1005 | -0.0044 | -8.2431 to 6.4436 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (7.0 h) |
| Last heartbeat | 2026-11-11T04:47:22Z (age 2.0 s) |
| Feeds healthy | yes |
| Windows observed | 86 |
| Paper trades | 62 |
| Settled trades | 62 |
| Win rate | 0.5000 (Wilson 95%: 0.3792 to 0.6208) |
| Net PnL, paper (USDC) | 1.7816 after est. fees (gross 6.8079) |
| Net PnL 95% bootstrap (USDC) | -9.0927 to 11.5756 |
| Brier, model vs market (41 settled) | 0.1640 vs 0.1519 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
