# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T11:19:15Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 62 (62) | 0.4355 (0.3194 to 0.5591) | -6.7462 | -0.1088 | -17.2327 to 3.4779 |
| C · Conviction (calibrated) · hold to settle | 51 (50) | 0.6600 (0.5215 to 0.7756) | -7.2294 | -0.1446 | -30.3463 to 13.9524 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (13.6 h) |
| Last heartbeat | 2026-11-11T11:19:14Z (age 1.7 s) |
| Feeds healthy | yes |
| Windows observed | 164 |
| Paper trades | 154 |
| Settled trades | 153 |
| Win rate | 0.4967 (Wilson 95%: 0.4186 to 0.5751) |
| Net PnL, paper (USDC) | -22.5174 after est. fees (gross -9.6829) |
| Net PnL 95% bootstrap (USDC) | -51.1488 to 3.8641 |
| Brier, model vs market (94 settled) | 0.1376 vs 0.1320 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
