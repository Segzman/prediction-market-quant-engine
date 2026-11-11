# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T03:21:33Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 6 (6) | 0.6667 (0.3000 to 0.9032) | 1.3638 | 0.2273 | needs 20+ settled |
| B · Conviction (brownian+micro) | 8 (8) | 0.6250 (0.3057 to 0.8632) | 1.6167 | 0.2021 | needs 20+ settled |
| C · Conviction (calibrated) | 10 (10) | 0.7000 (0.3968 to 0.8922) | 3.3212 | 0.3321 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (5.6 h) |
| Last heartbeat | 2026-11-11T03:21:32Z (age 1.2 s) |
| Feeds healthy | yes |
| Windows observed | 69 |
| Paper trades | 24 |
| Settled trades | 24 |
| Win rate | 0.6667 (Wilson 95%: 0.4671 to 0.8203) |
| Net PnL, paper (USDC) | 6.3017 after est. fees (gross 7.9907) |
| Net PnL 95% bootstrap (USDC) | 2.4902 to 9.9381 |
| Brier, model vs market (16 settled) | 0.0069 vs 0.0297 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
