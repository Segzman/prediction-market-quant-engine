# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T03:06:29Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 4 (4) | 0.7500 (0.3006 to 0.9544) | 1.0790 | 0.2698 | needs 20+ settled |
| B · Conviction (brownian+micro) | 6 (6) | 0.6667 (0.3000 to 0.9032) | 1.3320 | 0.2220 | needs 20+ settled |
| C · Conviction (calibrated) | 6 (6) | 0.8333 (0.4365 to 0.9699) | 2.6312 | 0.4385 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (5.4 h) |
| Last heartbeat | 2026-11-11T03:06:28Z (age 1.2 s) |
| Feeds healthy | yes |
| Windows observed | 66 |
| Paper trades | 16 |
| Settled trades | 16 |
| Win rate | 0.7500 (Wilson 95%: 0.5050 to 0.8982) |
| Net PnL, paper (USDC) | 5.0422 after est. fees (gross 6.0741) |
| Net PnL 95% bootstrap (USDC) | needs 20+ settled trades |
| Brier, model vs market (12 settled) | 0.0071 vs 0.0305 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
