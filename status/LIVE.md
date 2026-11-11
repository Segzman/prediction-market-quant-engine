# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T05:32:37Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 24 (24) | 0.5000 (0.3143 to 0.6857) | 0.8922 | 0.0372 | -5.1020 to 6.6314 |
| B · Conviction (brownian+micro) | 33 (33) | 0.4242 (0.2724 to 0.5919) | -3.1325 | -0.0949 | -10.5611 to 4.4208 |
| C · Conviction (calibrated) · hold to settle | 30 (28) | 0.5714 (0.3907 to 0.7349) | -0.8580 | -0.0306 | -13.9717 to 9.0472 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (7.8 h) |
| Last heartbeat | 2026-11-11T05:32:36Z (age 1.1 s) |
| Feeds healthy | yes |
| Windows observed | 95 |
| Paper trades | 87 |
| Settled trades | 85 |
| Win rate | 0.4941 (Wilson 95%: 0.3904 to 0.5983) |
| Net PnL, paper (USDC) | -3.0983 after est. fees (gross 4.0634) |
| Net PnL 95% bootstrap (USDC) | -18.4705 to 11.3616 |
| Brier, model vs market (53 settled) | 0.1436 vs 0.1360 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
