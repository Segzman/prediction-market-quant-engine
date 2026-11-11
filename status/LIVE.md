# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T03:51:41Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 9 (8) | 0.5000 (0.2152 to 0.7848) | 0.5066 | 0.0633 | needs 20+ settled |
| B · Conviction (brownian+micro) | 13 (12) | 0.4167 (0.1933 to 0.6805) | -2.4007 | -0.2001 | needs 20+ settled |
| C · Conviction (calibrated) | 15 (14) | 0.5000 (0.2680 to 0.7320) | -1.5393 | -0.1099 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (6.1 h) |
| Last heartbeat | 2026-11-11T03:51:41Z (age 0.2 s) |
| Feeds healthy | yes |
| Windows observed | 75 |
| Paper trades | 37 |
| Settled trades | 34 |
| Win rate | 0.4706 (Wilson 95%: 0.3145 to 0.6326) |
| Net PnL, paper (USDC) | -3.4334 after est. fees (gross -0.3647) |
| Net PnL 95% bootstrap (USDC) | -12.8577 to 4.7634 |
| Brier, model vs market (19 settled) | 0.1332 vs 0.1260 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
