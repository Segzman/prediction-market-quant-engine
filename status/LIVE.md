# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T04:06:45Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 10 (10) | 0.6000 (0.3127 to 0.8318) | 1.7365 | 0.1736 | needs 20+ settled |
| B · Conviction (brownian+micro) | 14 (14) | 0.5000 (0.2680 to 0.7320) | -1.1707 | -0.0836 | needs 20+ settled |
| C · Conviction (calibrated) | 16 (16) | 0.5625 (0.3318 to 0.7690) | -0.3094 | -0.0193 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (6.4 h) |
| Last heartbeat | 2026-11-11T04:06:44Z (age 1.1 s) |
| Feeds healthy | yes |
| Windows observed | 78 |
| Paper trades | 40 |
| Settled trades | 40 |
| Win rate | 0.5500 (Wilson 95%: 0.3983 to 0.6929) |
| Net PnL, paper (USDC) | 0.2564 after est. fees (gross 3.6546) |
| Net PnL 95% bootstrap (USDC) | -9.6075 to 9.2812 |
| Brier, model vs market (25 settled) | 0.1041 vs 0.1049 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
