# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T06:17:46Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 31 (31) | 0.4516 (0.2916 to 0.6223) | -0.2363 | -0.0076 | -6.6792 to 5.9290 |
| B · Conviction (brownian+micro) | 42 (42) | 0.4048 (0.2704 to 0.5551) | -4.2839 | -0.1020 | -12.2245 to 4.2066 |
| C · Conviction (calibrated) · hold to settle | 38 (36) | 0.6667 (0.5033 to 0.7979) | 6.1309 | 0.1703 | -7.3985 to 16.9478 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (8.5 h) |
| Last heartbeat | 2026-11-11T06:17:45Z (age 1.0 s) |
| Feeds healthy | yes |
| Windows observed | 104 |
| Paper trades | 111 |
| Settled trades | 109 |
| Win rate | 0.5046 (Wilson 95%: 0.4122 to 0.5967) |
| Net PnL, paper (USDC) | 1.6107 after est. fees (gross 10.7630) |
| Net PnL 95% bootstrap (USDC) | -14.5686 to 17.3616 |
| Brier, model vs market (66 settled) | 0.1175 vs 0.1159 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
