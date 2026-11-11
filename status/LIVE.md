# Live paper run

> **Paper trading only. No real money is at risk. This page makes no claim of edge.**

Updated: 2026-11-11T10:34:01Z (every 15 minutes from the paper server)

## By model

Each model trades its own paper book; compare them side by side, not summed.

| Model | Trades (settled) | Win rate (Wilson 95%) | Net PnL (USDC) | Avg / trade | Net 95% bootstrap |
| --- | --- | --- | --- | --- | --- |
| A · Conviction (momentum) | 41 (41) | 0.3902 (0.2566 to 0.5427) | -8.5417 | -0.2083 | -20.7071 to 1.8054 |
| B · Conviction (brownian+micro) | 61 (61) | 0.4426 (0.3251 to 0.5670) | -6.6521 | -0.1091 | -16.9345 to 3.4604 |
| C · Conviction (calibrated) · hold to settle | 47 (46) | 0.6522 (0.5077 to 0.7732) | -5.5753 | -0.1212 | -26.8829 to 14.0057 |

## All models combined

| Metric | Value |
| --- | --- |
| Observed since | 2026-11-10T21:45:06Z (12.8 h) |
| Last heartbeat | 2026-11-11T10:34:00Z (age 0.5 s) |
| Feeds healthy | yes |
| Windows observed | 155 |
| Paper trades | 149 |
| Settled trades | 148 |
| Win rate | 0.4932 (Wilson 95%: 0.4139 to 0.5729) |
| Net PnL, paper (USDC) | -20.7692 after est. fees (gross -8.2329) |
| Net PnL 95% bootstrap (USDC) | -46.9999 to 3.8064 |
| Brier, model vs market (90 settled) | 0.1342 vs 0.1298 |
| JEV calls / latency p50 | 0 / n/a ms |

Paper results only. Fees are an estimate. Small samples do not establish an edge.
