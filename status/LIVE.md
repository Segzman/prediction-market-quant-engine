# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T10:49:06Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 61 (61) | 0.4426 (0.3251 to 0.5670) | -6.6521 | -0.1091 | -16.9345 to 3.4604 |
| C · Conviction (calibrated) · hold to settle | 48 (47) | 0.6596 (0.5167 to 0.7783) | -4.8224 | -0.1026 | -25.9813 to 14.2627 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (13.1 h) |
| Last heartbeat | 2026-11-11T10:49:04Z (age 1.5 s) |
| Feeds healthy | yes |
| Windows observed | 158 |
| Paper trades | 150 |
| Settled trades | 149 |
| Win rate | 0.4966 (Wilson 95%: 0.4175 to 0.5760) |
| Net PnL, paper (USDC) | -20.0162 after est. fees (gross -7.4329) |
| Net PnL 95% bootstrap (USDC) | -46.7046 to 4.6629 |
| Brier, model vs market (91 settled) | 0.1328 vs 0.1287 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
