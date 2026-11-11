# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T09:18:38Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 40 (40) | 0.4000 (0.2635 to 0.5540) | -7.8980 | -0.1975 | -19.5770 to 2.4657 |
| B · Conviction (brownian+micro) | 57 (56) | 0.4464 (0.3239 to 0.5759) | -4.9542 | -0.0885 | -14.9213 to 5.1405 |
| C · Conviction (calibrated) · hold to settle | 44 (44) | 0.6364 (0.4887 to 0.7622) | -7.0337 | -0.1599 | -28.8343 to 11.9247 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (11.6 h) |
| Last heartbeat | 2026-11-11T09:18:38Z (age 0.3 s) |
| Feeds healthy | yes |
| Windows observed | 140 |
| Paper trades | 141 |
| Settled trades | 140 |
| Win rate | 0.4929 (Wilson 95%: 0.4113 to 0.5747) |
| Net PnL, paper (USDC) | -19.8859 after est. fees (gross -8.0976) |
| Net PnL 95% bootstrap (USDC) | -45.7367 to 4.2478 |
| Brier, model vs market (86 settled) | 0.1401 vs 0.1346 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
