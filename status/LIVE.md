# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T08:48:30Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 39 (39) | 0.4103 (0.2708 to 0.5658) | -7.7558 | -0.1989 | -19.2122 to 2.2662 |
| B · Conviction (brownian+micro) | 54 (53) | 0.4340 (0.3095 to 0.5673) | -5.5031 | -0.1038 | -15.0759 to 4.0392 |
| C · Conviction (calibrated) · hold to settle | 44 (44) | 0.6364 (0.4887 to 0.7622) | -7.0337 | -0.1599 | -28.8343 to 11.9247 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (11.1 h) |
| Last heartbeat | 2026-11-11T08:48:30Z (age 0.0 s) |
| Feeds healthy | yes |
| Windows observed | 134 |
| Paper trades | 137 |
| Settled trades | 136 |
| Win rate | 0.4926 (Wilson 95%: 0.4100 to 0.5757) |
| Net PnL, paper (USDC) | -20.2926 after est. fees (gross -8.8447) |
| Net PnL 95% bootstrap (USDC) | -47.1394 to 3.9974 |
| Brier, model vs market (84 settled) | 0.1431 vs 0.1368 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
