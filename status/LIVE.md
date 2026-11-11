# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T15:21:17Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 42 (42) | 0.3810 (0.2500 to 0.5319) | -8.7552 | -0.2085 | -20.8609 to 1.4618 |
| B · Conviction (brownian+micro) | 63 (63) | 0.4286 (0.3140 to 0.5514) | -7.4840 | -0.1188 | -17.9153 to 3.2724 |
| C · Conviction (calibrated) · hold to settle | 51 (51) | 0.6471 (0.5099 to 0.7637) | -11.3333 | -0.2222 | -35.1086 to 10.3645 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (17.6 h) |
| Last heartbeat | 2026-11-11T15:21:12Z (age 5.1 s) |
| Feeds healthy | yes |
| Windows observed | 213 |
| Paper trades | 156 |
| Settled trades | 156 |
| Win rate | 0.4872 (Wilson 95%: 0.4100 to 0.5650) |
| Net PnL, paper (USDC) | -27.5725 after est. fees (gross -14.4329) |
| Net PnL 95% bootstrap (USDC) | -56.1804 to -1.0994 |
| Brier, model vs market (95 settled) | 0.1446 vs 0.1376 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
