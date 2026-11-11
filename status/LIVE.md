# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T09:33:42Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 40 (40) | 0.4000 (0.2635 to 0.5540) | -7.8980 | -0.1975 | -19.5770 to 2.4657 |
| B · Conviction (brownian+micro) | 59 (59) | 0.4407 (0.3215 to 0.5671) | -6.3326 | -0.1073 | -16.5093 to 3.6873 |
| C · Conviction (calibrated) · hold to settle | 44 (44) | 0.6364 (0.4887 to 0.7622) | -7.0337 | -0.1599 | -28.8343 to 11.9247 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (11.8 h) |
| Last heartbeat | 2026-11-11T09:33:41Z (age 1.6 s) |
| Feeds healthy | yes |
| Windows observed | 143 |
| Paper trades | 143 |
| Settled trades | 143 |
| Win rate | 0.4895 (Wilson 95%: 0.4089 to 0.5706) |
| Net PnL, paper (USDC) | -21.2643 after est. fees (gross -9.1629) |
| Net PnL 95% bootstrap (USDC) | -46.3996 to 3.6077 |
| Brier, model vs market (87 settled) | 0.1386 vs 0.1335 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
