# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-12T00:10:14Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 43 (43) | 0.3953 (0.2637 to 0.5442) | -8.2001 | -0.1907 | -20.5942 to 2.0074 |
| B · Conviction (brownian+micro) | 63 (63) | 0.4286 (0.3140 to 0.5514) | -7.4840 | -0.1188 | -17.9153 to 3.2724 |
| C · Conviction (calibrated) · hold to settle | 51 (51) | 0.6471 (0.5099 to 0.7637) | -11.3333 | -0.2222 | -35.1086 to 10.3645 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (26.4 h) |
| Last heartbeat | 2026-11-12T00:10:14Z (age 0.1 s) |
| Feeds healthy | yes |
| Windows observed | 298 |
| Paper trades | 157 |
| Settled trades | 157 |
| Win rate | 0.4904 (Wilson 95%: 0.4134 to 0.5679) |
| Net PnL, paper (USDC) | -27.0174 after est. fees (gross -13.8285) |
| Net PnL 95% bootstrap (USDC) | -56.8192 to -0.2174 |
| Brier, model vs market (96 settled) | 0.1432 vs 0.1364 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
