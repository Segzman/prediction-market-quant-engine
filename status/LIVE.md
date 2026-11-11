# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T10:18:56Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 61 (61) | 0.4426 (0.3251 to 0.5670) | -6.6521 | -0.1091 | -16.9345 to 3.4604 |
| C · Conviction (calibrated) · hold to settle | 45 (45) | 0.6444 (0.4984 to 0.7678) | -6.2807 | -0.1396 | -27.6054 to 12.3340 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (12.6 h) |
| Last heartbeat | 2026-11-11T10:18:56Z (age 0.2 s) |
| Feeds healthy | yes |
| Windows observed | 152 |
| Paper trades | 147 |
| Settled trades | 147 |
| Win rate | 0.4898 (Wilson 95%: 0.4103 to 0.5698) |
| Net PnL, paper (USDC) | -21.4746 after est. fees (gross -8.9829) |
| Net PnL 95% bootstrap (USDC) | -47.2741 to 3.4803 |
| Brier, model vs market (89 settled) | 0.1356 vs 0.1310 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
