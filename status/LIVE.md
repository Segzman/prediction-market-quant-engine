# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T06:47:57Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 37 (37) | 0.4324 (0.2867 to 0.5909) | -3.0232 | -0.0817 | -10.5158 to 4.4133 |
| B · Conviction (brownian+micro) | 45 (45) | 0.4000 (0.2702 to 0.5455) | -6.0026 | -0.1334 | -14.8239 to 2.3151 |
| C · Conviction (calibrated) · hold to settle | 42 (41) | 0.6341 (0.4812 to 0.7641) | -4.6744 | -0.1140 | -25.0963 to 12.5999 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (9.1 h) |
| Last heartbeat | 2026-11-11T06:47:55Z (age 1.5 s) |
| Feeds healthy | yes |
| Windows observed | 110 |
| Paper trades | 124 |
| Settled trades | 123 |
| Win rate | 0.4878 (Wilson 95%: 0.4012 to 0.5752) |
| Net PnL, paper (USDC) | -13.7002 after est. fees (gross -3.2106) |
| Net PnL 95% bootstrap (USDC) | -37.0571 to 7.6199 |
| Brier, model vs market (74 settled) | 0.1391 vs 0.1331 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
