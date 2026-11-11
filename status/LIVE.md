# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T07:18:05Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 38 (38) | 0.4211 (0.2785 to 0.5781) | -7.2702 | -0.1913 | -19.2143 to 2.7832 |
| B · Conviction (brownian+micro) | 47 (46) | 0.4130 (0.2829 to 0.5566) | -5.2960 | -0.1151 | -14.0296 to 3.5615 |
| C · Conviction (calibrated) · hold to settle | 42 (42) | 0.6190 (0.4681 to 0.7500) | -8.7783 | -0.2090 | -30.8157 to 10.3661 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (9.6 h) |
| Last heartbeat | 2026-11-11T07:18:05Z (age 0.1 s) |
| Feeds healthy | yes |
| Windows observed | 116 |
| Paper trades | 127 |
| Settled trades | 126 |
| Win rate | 0.4841 (Wilson 95%: 0.3986 to 0.5706) |
| Net PnL, paper (USDC) | -21.3445 after est. fees (gross -10.6960) |
| Net PnL 95% bootstrap (USDC) | -46.5057 to 2.5716 |
| Brier, model vs market (77 settled) | 0.1553 vs 0.1462 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
