# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T06:02:45Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 29 (29) | 0.4483 (0.2841 to 0.6245) | -0.3216 | -0.0111 | -6.1839 to 5.5355 |
| B · Conviction (brownian+micro) | 39 (39) | 0.4103 (0.2708 to 0.5658) | -3.7381 | -0.0958 | -11.8178 to 4.1224 |
| C · Conviction (calibrated) · hold to settle | 35 (34) | 0.6471 (0.4791 to 0.7851) | 4.3279 | 0.1273 | -8.8369 to 14.8812 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (8.3 h) |
| Last heartbeat | 2026-11-11T06:02:43Z (age 2.6 s) |
| Feeds healthy | yes |
| Windows observed | 101 |
| Paper trades | 103 |
| Settled trades | 102 |
| Win rate | 0.5000 (Wilson 95%: 0.4047 to 0.5953) |
| Net PnL, paper (USDC) | 0.2682 after est. fees (gross 8.8218) |
| Net PnL 95% bootstrap (USDC) | -15.6744 to 14.4553 |
| Brier, model vs market (62 settled) | 0.1242 vs 0.1211 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
