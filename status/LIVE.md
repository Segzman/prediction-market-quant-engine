# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T06:32:53Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 35 (34) | 0.4412 (0.2888 to 0.6055) | -1.9122 | -0.0562 | -9.4336 to 5.4951 |
| B · Conviction (brownian+micro) | 44 (44) | 0.4091 (0.2769 to 0.5559) | -5.3262 | -0.1210 | -13.9916 to 3.8594 |
| C · Conviction (calibrated) · hold to settle | 40 (39) | 0.6410 (0.4842 to 0.7726) | -1.3714 | -0.0352 | -19.6152 to 13.5213 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (8.8 h) |
| Last heartbeat | 2026-11-11T06:32:51Z (age 2.1 s) |
| Feeds healthy | yes |
| Windows observed | 107 |
| Paper trades | 119 |
| Settled trades | 117 |
| Win rate | 0.4957 (Wilson 95%: 0.4067 to 0.5850) |
| Net PnL, paper (USDC) | -8.6098 after est. fees (gross 1.3409) |
| Net PnL 95% bootstrap (USDC) | -30.0497 to 11.2012 |
| Brier, model vs market (71 settled) | 0.1335 vs 0.1290 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
