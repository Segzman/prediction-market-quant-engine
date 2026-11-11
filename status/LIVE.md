# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T11:04:11Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 61 (61) | 0.4426 (0.3251 to 0.5670) | -6.6521 | -0.1091 | -16.9345 to 3.4604 |
| C · Conviction (calibrated) · hold to settle | 50 (49) | 0.6735 (0.5338 to 0.7879) | -3.1734 | -0.0648 | -25.1441 to 16.1586 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (13.3 h) |
| Last heartbeat | 2026-11-11T11:04:10Z (age 0.5 s) |
| Feeds healthy | yes |
| Windows observed | 161 |
| Paper trades | 152 |
| Settled trades | 151 |
| Win rate | 0.5033 (Wilson 95%: 0.4245 to 0.5820) |
| Net PnL, paper (USDC) | -18.3673 after est. fees (gross -5.6829) |
| Net PnL 95% bootstrap (USDC) | -45.6626 to 6.2379 |
| Brier, model vs market (93 settled) | 0.1301 vs 0.1266 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
