#!/usr/bin/env python3
"""Analyze trading performance from trade logs."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import json
import sys
from pathlib import Path


def analyze_trades(trades_file: str):
    """Analyze trades from a JSONL file."""
    trades = []
    resolutions = {}
    
    with open(trades_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get('type') == 'trade':
                    trades.append(data)
                elif data.get('type') == 'resolution':
                    resolutions[data['order_id']] = data
            except json.JSONDecodeError:
                continue
    
    if not trades:
        print(f"No trades found in {trades_file}")
        return
    
    # Calculate stats per model
    models = {}
    for trade in trades:
        model = trade.get('model', 'Unknown')
        if model not in models:
            models[model] = {'trades': [], 'wins': 0, 'losses': 0, 'pnl': 0.0}
        models[model]['trades'].append(trade)
    
    # Match with resolutions
    total_pnl = 0.0
    total_wins = 0
    total_losses = 0
    
    for _order_id, resolution in resolutions.items():
        pnl = resolution.get('pnl', 0)
        total_pnl += pnl
        if pnl > 0:
            total_wins += 1
        else:
            total_losses += 1
    
    total_trades = total_wins + total_losses
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE ANALYSIS: {trades_file}")
    print(f"{'='*70}")
    print(f"\n  Total Trades: {total_trades}")
    print(f"  Wins: {total_wins} | Losses: {total_losses}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Total PnL: ${total_pnl:.2f}")
    
    if total_trades > 0:
        avg_win = sum(r['pnl'] for r in resolutions.values() if r['pnl'] > 0) / max(1, total_wins)
        avg_loss = sum(r['pnl'] for r in resolutions.values() if r['pnl'] <= 0) / max(1, total_losses)
        print(f"  Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}")
    
    # Show recent trades
    print("\n  Last 5 Trades:")
    for i, trade in enumerate(trades[-5:], 1):
        order_id = trade.get('order_id', '')
        res = resolutions.get(order_id, {})
        pnl = res.get('pnl', 0)
        edge = trade.get('edge', 0)
        regime = trade.get('feature_snapshot', {}).get('entry_regime', 'UNKNOWN')
        consistency = trade.get('feature_snapshot', {}).get('directional_consistency', 0)
        print(f"    {i}. {trade.get('model')} {trade.get('direction')} | "
              f"Edge: {edge:.3f} | {regime} | Consistency: {consistency:.2f} | "
              f"PnL: ${pnl:+.2f}")
    
    print("\n  KEY ISSUES:")
    
    # Check for LATE_CONVICTION trades
    late_trades = [
        t for t in trades
        if t.get('feature_snapshot', {}).get('entry_regime') == 'LATE_CONVICTION'
    ]
    if late_trades:
        late_pct = len(late_trades) / len(trades) * 100
        print(f"    - {late_pct:.0f}% of trades are LATE_CONVICTION (forced trades)")
    
    # Check directional consistency
    low_consistency = [
        t for t in trades
        if t.get('feature_snapshot', {}).get('directional_consistency', 1) < 0.55
    ]
    if low_consistency:
        print(f"    - {len(low_consistency)}/{len(trades)} trades have low directional consistency (<0.55)")
    
    # Check for hedging
    no_hedge = [t for t in trades if not t.get('execution_snapshot', {}).get('hedge_amount', 0)]
    if no_hedge:
        print(f"    - No hedging observed on {len(no_hedge)} trades")
    
    print(f"\n{'='*70}\n")

if __name__ == '__main__':
    # Check the most recent run
    runtime_dir = Path('.web_runtime')
    trade_files = sorted(runtime_dir.glob('*_*_trades.jsonl'))
    
    if trade_files:
        # Analyze the 3 most recent runs
        for tf in trade_files[-3:]:
            analyze_trades(str(tf))
    else:
        print("No trade files found")
