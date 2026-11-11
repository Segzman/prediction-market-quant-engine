# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T04:36:53Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 15 (15) | 0.6000 (0.3575 to 0.8018) | 3.5946 | 0.2396 | needs 20+ settled |
| B · Conviction (brownian+micro) | 22 (22) | 0.4545 (0.2692 to 0.6534) | -0.4570 | -0.0208 | -6.7535 to 5.2438 |
| C · Conviction (calibrated) | 22 (22) | 0.5455 (0.3466 to 0.7308) | 0.5273 | 0.0240 | -7.8348 to 6.7747 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (6.9 h) |
| Last heartbeat | 2026-11-11T04:36:52Z (age 0.5 s) |
| Feeds healthy | yes |
| Windows observed | 84 |
| Paper trades | 59 |
| Settled trades | 59 |
| Win rate | 0.5254 (Wilson 95%: 0.4004 to 0.6473) |
| Net PnL, paper (USDC) | 3.6649 after est. fees (gross 8.3079) |
| Net PnL 95% bootstrap (USDC) | -6.6497 to 13.2591 |
| Brier, model vs market (41 settled) | 0.1640 vs 0.1519 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
