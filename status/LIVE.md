# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T07:03:01Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 38 (38) | 0.4211 (0.2785 to 0.5781) | -7.2702 | -0.1913 | -19.2143 to 2.7832 |
| B · Conviction (brownian+micro) | 45 (45) | 0.4000 (0.2702 to 0.5455) | -6.0026 | -0.1334 | -14.8239 to 2.3151 |
| C · Conviction (calibrated) · hold to settle | 42 (42) | 0.6190 (0.4681 to 0.7500) | -8.7783 | -0.2090 | -30.8157 to 10.3661 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (9.3 h) |
| Last heartbeat | 2026-11-11T07:03:00Z (age 1.1 s) |
| Feeds healthy | yes |
| Windows observed | 113 |
| Paper trades | 125 |
| Settled trades | 125 |
| Win rate | 0.4800 (Wilson 95%: 0.3943 to 0.5669) |
| Net PnL, paper (USDC) | -22.0511 after est. fees (gross -11.4606) |
| Net PnL 95% bootstrap (USDC) | -47.4388 to 1.8403 |
| Brier, model vs market (76 settled) | 0.1571 vs 0.1476 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
