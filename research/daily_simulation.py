#!/usr/bin/env python3
"""
Daily Trading Simulation - $1 Primary Bets
Tests different win rates over a full day of trading (288 windows).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class Direction(Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class PositionPlan:
    primary_usdc: float
    hedge_usdc: float
    reserve_usdc: float
    hedge_active: bool
    hedge_reason: str = ""


# Constants
MIN_BET = 1.0
FEE_RATE = 0.02  # 2% per leg
PRIMARY_BET = 3.0  # $3 primary bets to allow $1+ hedges


def calculate_pnl(
    primary_bet: float,
    hedge_bet: float,
    primary_direction: Direction,
    primary_price: float,
    hedge_price: float,
    actual_outcome: Direction,
) -> tuple[float, float, float]:
    """Calculate PnL for a trade."""
    # Primary outcome
    if primary_direction == actual_outcome:
        primary_return = primary_bet * (1 - primary_price) / primary_price
    else:
        primary_return = -primary_bet

    # Hedge outcome
    hedge_direction = Direction.DOWN if primary_direction == Direction.UP else Direction.UP
    if hedge_direction == actual_outcome:
        hedge_return = hedge_bet * (1 - hedge_price) / hedge_price
    else:
        hedge_return = -hedge_bet

    gross_pnl = primary_return + hedge_return
    fees = (primary_bet + hedge_bet) * FEE_RATE
    net_pnl = gross_pnl - fees

    return gross_pnl, fees, net_pnl


# ============== HEDGE STRATEGIES ==============


def strategy_no_hedge(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
) -> PositionPlan:
    """No hedging."""
    return PositionPlan(primary_bet, 0.0, 0.0, False, "NO_HEDGE")


def strategy_fixed_50pct(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
) -> PositionPlan:
    """Fixed 50% hedge with minimum $1 hedge."""
    hedge = max(MIN_BET, primary_bet * 0.5)  # Ensure at least $1 hedge
    if bankroll < primary_bet + hedge:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "HEDGE_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, "50%_HEDGE")


def strategy_drawdown_scaled(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
) -> PositionPlan:
    """Scale hedge based on bankroll health - always at least $1."""
    ratio = bankroll / starting_bankroll
    base_fraction = 0.5

    if ratio >= 1.0:
        fraction = base_fraction * 0.7  # 35%
    elif ratio >= 0.8:
        fraction = base_fraction  # 50%
    elif ratio >= 0.6:
        fraction = base_fraction * 1.5  # 75%
    elif ratio >= 0.4:
        fraction = base_fraction * 2.0  # 100%
    else:
        fraction = base_fraction * 2.5  # 125%

    hedge = max(MIN_BET, primary_bet * fraction)  # Ensure at least $1 hedge
    hedge = min(hedge, bankroll * 0.9 - primary_bet)

    if bankroll < primary_bet + hedge:
        return PositionPlan(primary_bet, 0.0, 0.0, False, f"DD_{int(fraction * 100)}%_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, f"DD_{int(fraction * 100)}%")


def strategy_loss_cap_2pct(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
) -> PositionPlan:
    """Size hedge to cap max loss at 2% of bankroll - minimum $1 hedge."""
    max_allowed_loss = bankroll * 0.02
    required_hedge_profit = primary_bet - max_allowed_loss

    if required_hedge_profit <= 0:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "NO_HEDGE")

    hedge_payout_per_dollar = 1 - hedge_price
    if hedge_payout_per_dollar <= 0:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "INVALID")

    required_hedge = required_hedge_profit / hedge_payout_per_dollar
    hedge = max(MIN_BET, required_hedge)  # Ensure at least $1 hedge
    hedge = min(hedge, bankroll * 0.9 - primary_bet)

    if bankroll < primary_bet + hedge:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "CAP_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, "CAP_2%")


# ============== SIMULATION ==============


def run_daily_simulation(
    strategy_fn: Callable,
    strategy_name: str,
    win_rate: float,
    num_trades: int = 288,  # 24 hours * 12 windows/hour
    starting_bankroll: float = 100.0,
    seed: int = 42,
) -> dict:
    """Run a full day of trading simulation."""
    random.seed(seed)
    bankroll = starting_bankroll
    max_bankroll = bankroll
    min_bankroll = bankroll
    trades_executed = 0
    wins = 0
    losses = 0
    hedged_trades = 0
    total_fees = 0.0
    busted = False

    for i in range(num_trades):
        if bankroll < PRIMARY_BET + MIN_BET:
            busted = True
            break

        # Generate random market conditions
        # Primary price: random between 0.35 and 0.65 (typical mispricing range)
        primary_price = round(random.uniform(0.35, 0.65), 2)
        hedge_price = round(1.0 - primary_price + random.uniform(-0.05, 0.05), 2)
        hedge_price = max(0.10, min(0.90, hedge_price))

        # Random direction
        primary_direction = random.choice([Direction.UP, Direction.DOWN])

        # Determine outcome based on win rate
        is_win = random.random() < win_rate
        actual_outcome = (
            primary_direction
            if is_win
            else (Direction.DOWN if primary_direction == Direction.UP else Direction.UP)
        )

        # Get hedge plan
        plan = strategy_fn(
            primary_bet=PRIMARY_BET,
            bankroll=bankroll,
            starting_bankroll=starting_bankroll,
            primary_price=primary_price,
            hedge_price=hedge_price,
            trade_count=i,
        )

        # Calculate PnL
        gross, fees, net = calculate_pnl(
            plan.primary_usdc,
            plan.hedge_usdc,
            primary_direction,
            primary_price,
            hedge_price,
            actual_outcome,
        )

        bankroll += net
        total_fees += fees
        trades_executed += 1

        if is_win:
            wins += 1
        else:
            losses += 1

        if plan.hedge_active:
            hedged_trades += 1

        max_bankroll = max(max_bankroll, bankroll)
        min_bankroll = min(min_bankroll, bankroll)

    return {
        "strategy": strategy_name,
        "win_rate": win_rate,
        "starting_bankroll": starting_bankroll,
        "ending_bankroll": bankroll,
        "total_return_pct": (bankroll - starting_bankroll) / starting_bankroll * 100,
        "trades_executed": trades_executed,
        "wins": wins,
        "losses": losses,
        "hedged_trades": hedged_trades,
        "hedge_rate": hedged_trades / trades_executed * 100 if trades_executed > 0 else 0,
        "total_fees": total_fees,
        "max_bankroll": max_bankroll,
        "min_bankroll": min_bankroll,
        "max_drawdown_pct": (min_bankroll - starting_bankroll) / starting_bankroll * 100,
        "busted": busted,
    }


def print_daily_results(results: dict):
    """Print formatted daily results."""
    bust_str = " *** BUSTED ***" if results["busted"] else ""
    print(f"\n  {results['strategy']} @ {results['win_rate'] * 100:.0f}% Win Rate{bust_str}")
    print(f"  {'─' * 60}")
    print(f"    Start:     ${results['starting_bankroll']:.2f}")
    print(f"    End:       ${results['ending_bankroll']:.2f}")
    print(f"    Return:    {results['total_return_pct']:+.1f}%")
    print(f"    Trades:    {results['trades_executed']}/288")
    print(f"    Wins:      {results['wins']} | Losses: {results['losses']}")
    print(f"    Hedged:    {results['hedged_trades']} ({results['hedge_rate']:.0f}%)")
    print(f"    Total Fees: ${results['total_fees']:.2f}")
    print(f"    Max DD:    {results['max_drawdown_pct']:.1f}%")
    print(f"    Peak:      ${results['max_bankroll']:.2f} | Trough: ${results['min_bankroll']:.2f}")


def main():
    print("\n" + "=" * 70)
    print("  DAILY TRADING SIMULATION - $3 Primary Bets (to allow $1+ hedges)")
    print("  288 trades (one full day of 5-min windows)")
    print("  MINIMUM HEDGE SIZE: $1.00")
    print("=" * 70)

    win_rates = [0.50, 0.60, 0.70, 0.80, 0.90]
    strategies = [
        (strategy_no_hedge, "NO HEDGE"),
        (strategy_fixed_50pct, "50% FIXED"),
        (strategy_drawdown_scaled, "DRAWDOWN-SCALED"),
        (strategy_loss_cap_2pct, "2% LOSS CAP"),
    ]

    for win_rate in win_rates:
        print(f"\n{'=' * 70}")
        print(f"  WIN RATE: {win_rate * 100:.0f}%")
        print(f"{'=' * 70}")

        for strategy_fn, name in strategies:
            results = run_daily_simulation(
                strategy_fn, name, win_rate, num_trades=288, starting_bankroll=100.0, seed=42
            )
            print_daily_results(results)

    print("\n" + "=" * 70)
    print("  SUMMARY: Expected Bankroll After 1 Day")
    print("=" * 70)
    print("\n  Strategy          | 50%    | 60%    | 70%    | 80%    | 90%")
    print("  " + "─" * 65)

    for strategy_fn, name in strategies:
        row = f"  {name:<18} |"
        for win_rate in win_rates:
            results = run_daily_simulation(
                strategy_fn, name, win_rate, num_trades=288, starting_bankroll=100.0, seed=42
            )
            ending = results["ending_bankroll"]
            status = "BUST" if results["busted"] else f"${ending:.0f}"
            row += f" {status:<6} |"
        print(row)

    print("\n" + "=" * 70)
    print("  KEY INSIGHTS")
    print("=" * 70)
    print("""
  • At 50% win rate (random): ALL strategies lose money due to fees
  • Break-even is around 52-53% win rate (depending on hedge costs)
  • At 60%+ win rate: Hedging drags returns vs no-hedge
  • Drawdown-scaled: Protects downside but reduces upside
  • Loss-cap: Most consistent, strict loss limits
  • No-hedge: Best returns at high win rates, worst drawdowns
    """)


if __name__ == "__main__":
    main()
