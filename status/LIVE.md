# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T05:47:41Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 27 (26) | 0.4615 (0.2876 to 0.6454) | -0.2701 | -0.0104 | -6.4927 to 5.7479 |
| B · Conviction (brownian+micro) | 37 (35) | 0.4000 (0.2555 to 0.5643) | -4.2948 | -0.1227 | -12.3236 to 3.3401 |
| C · Conviction (calibrated) · hold to settle | 33 (32) | 0.6250 (0.4525 to 0.7707) | 2.6788 | 0.0837 | -10.1587 to 13.2857 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (8.0 h) |
| Last heartbeat | 2026-11-11T05:47:41Z (age 0.1 s) |
| Feeds healthy | yes |
| Windows observed | 98 |
| Paper trades | 97 |
| Settled trades | 93 |
| Win rate | 0.4946 (Wilson 95%: 0.3953 to 0.5944) |
| Net PnL, paper (USDC) | -1.8861 after est. fees (gross 6.0134) |
| Net PnL 95% bootstrap (USDC) | -17.3038 to 13.1209 |
| Brier, model vs market (57 settled) | 0.1343 vs 0.1290 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
