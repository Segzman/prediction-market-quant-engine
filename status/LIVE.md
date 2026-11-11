# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T07:33:10Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 38 (38) | 0.4211 (0.2785 to 0.5781) | -7.2702 | -0.1913 | -19.2143 to 2.7832 |
| B · Conviction (brownian+micro) | 48 (48) | 0.4167 (0.2885 to 0.5572) | -4.7642 | -0.0993 | -13.9563 to 4.2721 |
| C · Conviction (calibrated) · hold to settle | 43 (43) | 0.6279 (0.4786 to 0.7562) | -7.8343 | -0.1822 | -29.9988 to 11.0615 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (9.8 h) |
| Last heartbeat | 2026-11-11T07:33:09Z (age 0.2 s) |
| Feeds healthy | yes |
| Windows observed | 119 |
| Paper trades | 129 |
| Settled trades | 129 |
| Win rate | 0.4884 (Wilson 95%: 0.4037 to 0.5737) |
| Net PnL, paper (USDC) | -19.8688 after est. fees (gross -9.0566) |
| Net PnL 95% bootstrap (USDC) | -44.5298 to 4.2104 |
| Brier, model vs market (80 settled) | 0.1499 vs 0.1421 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
