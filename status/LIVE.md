# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T10:03:52Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 40 (40) | 0.4000 (0.2635 to 0.5540) | -7.8980 | -0.1975 | -19.5770 to 2.4657 |
| B · Conviction (brownian+micro) | 60 (59) | 0.4407 (0.3215 to 0.5671) | -6.3326 | -0.1073 | -16.5093 to 3.6873 |
| C · Conviction (calibrated) · hold to settle | 45 (45) | 0.6444 (0.4984 to 0.7678) | -6.2807 | -0.1396 | -27.6054 to 12.3340 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (12.3 h) |
| Last heartbeat | 2026-11-11T10:03:49Z (age 2.9 s) |
| Feeds healthy | yes |
| Windows observed | 149 |
| Paper trades | 145 |
| Settled trades | 144 |
| Win rate | 0.4931 (Wilson 95%: 0.4126 to 0.5738) |
| Net PnL, paper (USDC) | -20.5114 after est. fees (gross -8.3629) |
| Net PnL 95% bootstrap (USDC) | -47.3593 to 4.4682 |
| Brier, model vs market (88 settled) | 0.1371 vs 0.1323 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
