# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T05:17:33Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 21 (21) | 0.5714 (0.3655 to 0.7553) | 2.8897 | 0.1376 | -2.7271 to 8.0168 |
| B · Conviction (brownian+micro) | 28 (28) | 0.4643 (0.2953 to 0.6419) | -1.1620 | -0.0415 | -8.2793 to 5.9145 |
| C · Conviction (calibrated) · hold to settle | 27 (27) | 0.5556 (0.3731 to 0.7241) | -1.6110 | -0.0597 | -14.1284 to 8.3970 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (7.5 h) |
| Last heartbeat | 2026-11-11T05:17:33Z (age 0.5 s) |
| Feeds healthy | yes |
| Windows observed | 92 |
| Paper trades | 76 |
| Settled trades | 76 |
| Win rate | 0.5263 (Wilson 95%: 0.4155 to 0.6346) |
| Net PnL, paper (USDC) | 0.1167 after est. fees (gross 6.2557) |
| Net PnL 95% bootstrap (USDC) | -15.1343 to 13.8021 |
| Brier, model vs market (51 settled) | 0.1490 vs 0.1402 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
