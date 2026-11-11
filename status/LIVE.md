# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T13:05:27Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 62 (62) | 0.4355 (0.3194 to 0.5591) | -6.7462 | -0.1088 | -17.2327 to 3.4779 |
| C · Conviction (calibrated) · hold to settle | 51 (51) | 0.6471 (0.5099 to 0.7637) | -11.3333 | -0.2222 | -35.1086 to 10.3645 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (15.3 h) |
| Last heartbeat | 2026-11-11T13:05:26Z (age 0.4 s) |
| Feeds healthy | yes |
| Windows observed | 186 |
| Paper trades | 154 |
| Settled trades | 154 |
| Win rate | 0.4935 (Wilson 95%: 0.4157 to 0.5717) |
| Net PnL, paper (USDC) | -26.6212 after est. fees (gross -13.7329) |
| Net PnL 95% bootstrap (USDC) | -55.8970 to 1.3983 |
| Brier, model vs market (95 settled) | 0.1446 vs 0.1376 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
