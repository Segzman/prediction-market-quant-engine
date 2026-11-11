# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T07:48:13Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 38 (38) | 0.4211 (0.2785 to 0.5781) | -7.2702 | -0.1913 | -19.2143 to 2.7832 |
| B · Conviction (brownian+micro) | 51 (50) | 0.4200 (0.2938 to 0.5577) | -5.5778 | -0.1116 | -14.9783 to 3.4721 |
| C · Conviction (calibrated) · hold to settle | 43 (43) | 0.6279 (0.4786 to 0.7562) | -7.8343 | -0.1822 | -29.9988 to 11.0615 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (10.1 h) |
| Last heartbeat | 2026-11-11T07:48:12Z (age 1.8 s) |
| Feeds healthy | yes |
| Windows observed | 122 |
| Paper trades | 132 |
| Settled trades | 131 |
| Win rate | 0.4885 (Wilson 95%: 0.4045 to 0.5732) |
| Net PnL, paper (USDC) | -20.6824 after est. fees (gross -9.6833) |
| Net PnL 95% bootstrap (USDC) | -45.7397 to 3.5833 |
| Brier, model vs market (81 settled) | 0.1481 vs 0.1408 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
