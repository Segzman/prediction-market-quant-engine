# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T08:18:22Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 38 (38) | 0.4211 (0.2785 to 0.5781) | -7.2702 | -0.1913 | -19.2143 to 2.7832 |
| B · Conviction (brownian+micro) | 52 (52) | 0.4231 (0.2987 to 0.5581) | -6.1135 | -0.1176 | -15.7681 to 3.4825 |
| C · Conviction (calibrated) · hold to settle | 43 (43) | 0.6279 (0.4786 to 0.7562) | -7.8343 | -0.1822 | -29.9988 to 11.0615 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (10.6 h) |
| Last heartbeat | 2026-11-11T08:18:20Z (age 1.3 s) |
| Feeds healthy | yes |
| Windows observed | 128 |
| Paper trades | 133 |
| Settled trades | 133 |
| Win rate | 0.4887 (Wilson 95%: 0.4053 to 0.5728) |
| Net PnL, paper (USDC) | -21.2180 after est. fees (gross -10.0021) |
| Net PnL 95% bootstrap (USDC) | -46.0078 to 3.1702 |
| Brier, model vs market (82 settled) | 0.1464 vs 0.1395 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
