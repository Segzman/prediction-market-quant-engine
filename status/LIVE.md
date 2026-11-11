# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T04:21:46Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 13 (12) | 0.5833 (0.3195 to 0.8067) | 2.2968 | 0.1914 | needs 20+ settled |
| B · Conviction (brownian+micro) | 18 (17) | 0.5294 (0.3096 to 0.7383) | -0.0553 | -0.0033 | needs 20+ settled |
| C · Conviction (calibrated) | 19 (18) | 0.6111 (0.3862 to 0.7969) | 0.8539 | 0.0474 | needs 20+ settled |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (6.6 h) |
| Last heartbeat | 2026-11-11T04:21:44Z (age 1.7 s) |
| Feeds healthy | yes |
| Windows observed | 81 |
| Paper trades | 50 |
| Settled trades | 47 |
| Win rate | 0.5745 (Wilson 95%: 0.4328 to 0.7049) |
| Net PnL, paper (USDC) | 3.0954 after est. fees (gross 6.8526) |
| Net PnL 95% bootstrap (USDC) | -7.0143 to 12.5522 |
| Brier, model vs market (32 settled) | 0.0836 vs 0.0890 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
