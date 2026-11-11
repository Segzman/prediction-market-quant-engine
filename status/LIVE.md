# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T09:03:34Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 39 (39) | 0.4103 (0.2708 to 0.5658) | -7.7558 | -0.1989 | -19.2122 to 2.2662 |
| B · Conviction (brownian+micro) | 55 (54) | 0.4444 (0.3200 to 0.5762) | -4.8243 | -0.0893 | -14.7371 to 5.3333 |
| C · Conviction (calibrated) · hold to settle | 44 (44) | 0.6364 (0.4887 to 0.7622) | -7.0337 | -0.1599 | -28.8343 to 11.9247 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (11.3 h) |
| Last heartbeat | 2026-11-11T09:03:34Z (age 0.4 s) |
| Feeds healthy | yes |
| Windows observed | 137 |
| Paper trades | 138 |
| Settled trades | 137 |
| Win rate | 0.4964 (Wilson 95%: 0.4139 to 0.5790) |
| Net PnL, paper (USDC) | -19.6137 after est. fees (gross -8.1077) |
| Net PnL 95% bootstrap (USDC) | -46.5974 to 4.6945 |
| Brier, model vs market (85 settled) | 0.1416 vs 0.1357 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
