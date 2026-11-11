# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T08:33:25Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 39 (39) | 0.4103 (0.2708 to 0.5658) | -7.7558 | -0.1989 | -19.2122 to 2.2662 |
| B · Conviction (brownian+micro) | 52 (52) | 0.4231 (0.2987 to 0.5581) | -6.1135 | -0.1176 | -15.7681 to 3.4825 |
| C · Conviction (calibrated) · hold to settle | 44 (44) | 0.6364 (0.4887 to 0.7622) | -7.0337 | -0.1599 | -28.8343 to 11.9247 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (10.8 h) |
| Last heartbeat | 2026-11-11T08:33:25Z (age 0.7 s) |
| Feeds healthy | yes |
| Windows observed | 131 |
| Paper trades | 135 |
| Settled trades | 135 |
| Win rate | 0.4889 (Wilson 95%: 0.4060 to 0.5723) |
| Net PnL, paper (USDC) | -20.9030 after est. fees (gross -9.5021) |
| Net PnL 95% bootstrap (USDC) | -47.2071 to 3.8163 |
| Brier, model vs market (83 settled) | 0.1447 vs 0.1381 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
