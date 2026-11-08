#!/usr/bin/env python3
"""
Hedge Strategy Simulator - $10 Bankroll
Compares different hedge behaviors for early-stage loss minimization.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

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


@dataclass
class TradeResult:
    scenario: str
    primary_bet: float
    hedge_bet: float
    primary_price: float
    hedge_price: float
    actual_outcome: Direction  # What actually happened
    pnl: float
    gross_return: float
    net_after_fees: float
    bankroll_after: float


# Simulation constants
MIN_BET = 1.0
MAX_BET = 5.0
FEE_RATE = 0.02  # 2% taker fee per leg


def calculate_pnl(
    primary_bet: float,
    hedge_bet: float,
    primary_direction: Direction,
    primary_price: float,
    hedge_price: float,
    actual_outcome: Direction,
) -> tuple[float, float, float]:
    """Calculate PnL for a trade. Returns (gross_pnl, fees, net_pnl)."""
    # Primary outcome
    if primary_direction == actual_outcome:
        primary_return = primary_bet * (1 - primary_price) / primary_price
    else:
        primary_return = -primary_bet

    # Hedge outcome (opposite direction)
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
    """Baseline: No hedging at all."""
    return PositionPlan(primary_bet, 0.0, 0.0, False, "NO_HEDGE")


def strategy_fixed_fraction(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
    fraction: float = 0.5,
) -> PositionPlan:
    """Current behavior: Fixed 50% hedge."""
    hedge = min(primary_bet * fraction, bankroll - primary_bet)
    if hedge < MIN_BET:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "HEDGE_TOO_SMALL")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, "FIXED_50%")


def strategy_drawdown_scaled(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
) -> PositionPlan:
    """Option 1: Scale hedge based on bankroll health."""
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
        fraction = base_fraction * 2.5  # 125% - hedge > primary

    hedge = min(primary_bet * fraction, bankroll * 0.9 - primary_bet)
    if hedge < MIN_BET:
        return PositionPlan(primary_bet, 0.0, 0.0, False, f"DD_SCALED_{int(fraction * 100)}%_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, f"DD_SCALED_{int(fraction * 100)}%")


def strategy_loss_cap(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    primary_price: float,
    hedge_price: float,
    trade_count: int = 0,
    max_loss_pct: float = 0.02,  # 2% max loss
) -> PositionPlan:
    """Option 2: Size hedge to cap maximum loss."""
    max_allowed_loss = bankroll * max_loss_pct
    primary_loss_if_wrong = primary_bet
    required_hedge_profit = primary_loss_if_wrong - max_allowed_loss

    if required_hedge_profit <= 0:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "NO_HEDGE_NEEDED")

    hedge_payout_per_dollar = 1 - hedge_price
    if hedge_payout_per_dollar <= 0:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "INVALID_HEDGE_PRICE")

    required_hedge = required_hedge_profit / hedge_payout_per_dollar
    hedge = min(required_hedge, bankroll * 0.9 - primary_bet)

    if hedge < MIN_BET:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "LOSS_CAP_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, "LOSS_CAP_2%")


def strategy_early_stage_preservation(
    primary_bet: float,
    bankroll: float,
    starting_bankroll: float,
    trade_count: int,
    primary_price: float,
    hedge_price: float,
) -> PositionPlan:
    """Option 3: Aggressive hedging in early stage."""
    # Early stage = first 10 trades OR not yet 5% profitable
    is_early = trade_count < 10 or bankroll < starting_bankroll * 1.05

    if not is_early:
        # Fall back to normal 50%
        return strategy_fixed_fraction(
            primary_bet,
            bankroll,
            primary_price=primary_price,
            hedge_price=hedge_price,
            fraction=0.5,
        )

    # Early stage: minimum 75% hedge + loss cap hybrid
    min_hedge = primary_bet * 0.75
    max_loss = bankroll * 0.015  # 1.5% max loss in early stage
    required_hedge_profit = primary_bet - max_loss

    if required_hedge_profit > 0 and hedge_price < 1.0:
        loss_capped_hedge = required_hedge_profit / (1 - hedge_price)
        hedge = max(min_hedge, loss_capped_hedge)
    else:
        hedge = min_hedge

    hedge = min(hedge, bankroll * 0.9 - primary_bet)

    if hedge < MIN_BET:
        return PositionPlan(primary_bet, 0.0, 0.0, False, "EARLY_PRESERVE_SKIP")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, "EARLY_PRESERVE")


# ============== SIMULATION ENGINE ==============


def run_trade_scenario(
    scenario_name: str,
    starting_bankroll: float,
    primary_price: float,  # Price of token we're buying
    hedge_price: float,  # Price of opposite token
    primary_direction: Direction,
    actual_outcome: Direction,
    strategy_fn: Callable,
    trade_count: int = 0,
) -> TradeResult:
    """Run a single trade scenario."""
    bankroll = starting_bankroll
    primary_bet = min(3.0, bankroll * 0.3)  # Bet 30% of bankroll, max $3

    plan = strategy_fn(
        primary_bet=primary_bet,
        bankroll=bankroll,
        starting_bankroll=10.0,
        primary_price=primary_price,
        hedge_price=hedge_price,
        trade_count=trade_count,
    )

    gross, fees, net = calculate_pnl(
        plan.primary_usdc,
        plan.hedge_usdc,
        primary_direction,
        primary_price,
        hedge_price,
        actual_outcome,
    )

    return TradeResult(
        scenario=scenario_name,
        primary_bet=plan.primary_usdc,
        hedge_bet=plan.hedge_usdc,
        primary_price=primary_price,
        hedge_price=hedge_price,
        actual_outcome=actual_outcome,
        pnl=net,
        gross_return=gross,
        net_after_fees=net,
        bankroll_after=bankroll + net,
    )


def run_full_simulation(
    strategy_fn: Callable,
    strategy_name: str,
    trades: list[tuple],
) -> list[TradeResult]:
    """Run a sequence of trades with a strategy."""
    bankroll = 10.0
    results = []

    for i, (primary_price, hedge_price, direction, outcome) in enumerate(trades):
        result = run_trade_scenario(
            scenario_name=strategy_name,
            starting_bankroll=bankroll,
            primary_price=primary_price,
            hedge_price=hedge_price,
            primary_direction=direction,
            actual_outcome=outcome,
            strategy_fn=strategy_fn,
            trade_count=i,
        )
        results.append(result)
        bankroll = result.bankroll_after
        if bankroll < MIN_BET:
            break  # Busted

    return results


def print_results(results: list[TradeResult], title: str):
    """Pretty print simulation results."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")

    if not results:
        print("  No trades executed")
        return

    start_bankroll = 10.0
    end_bankroll = results[-1].bankroll_after
    total_pnl = sum(r.pnl for r in results)
    max_drawdown = min((r.bankroll_after - start_bankroll) / start_bankroll for r in results)

    print(f"  {'Trade':>6} | {'Primary':>7} | {'Hedge':>7} | {'Hedge%':>6} | {'PnL':>8} | {'Bankroll':>9}")
    print(f"  {'-' * 60}")

    for i, r in enumerate(results):
        hedge_pct = (r.hedge_bet / r.primary_bet * 100) if r.primary_bet > 0 else 0
        pnl_str = f"${r.pnl:+.2f}"
        print(
            f"  {i + 1:>6} | ${r.primary_bet:>6.2f} | ${r.hedge_bet:>6.2f} | "
            f"{hedge_pct:>5.0f}% | {pnl_str:>8} | ${r.bankroll_after:>8.2f}"
        )

    print(f"  {'-' * 60}")
    print(f"  Starting: ${start_bankroll:.2f}")
    print(f"  Ending:   ${end_bankroll:.2f}")
    print(f"  Total PnL: ${total_pnl:+.2f} ({total_pnl / start_bankroll * 100:+.1f}%)")
    print(f"  Max Drawdown: {max_drawdown * 100:.1f}%")
    print(f"  Trades: {len(results)}")


# ============== SCENARIOS ==============

# Scenario 1: 50/50 win rate, typical mispricing
SCENARIO_1 = [
    # (primary_price, hedge_price, direction, actual_outcome)
    (0.45, 0.55, Direction.UP, Direction.UP),  # Win - UP was underpriced
    (0.55, 0.45, Direction.DOWN, Direction.UP),  # Loss - DOWN bet, UP won
    (0.48, 0.52, Direction.UP, Direction.DOWN),  # Loss - UP bet, DOWN won
    (0.52, 0.48, Direction.DOWN, Direction.DOWN),  # Win
    (0.44, 0.56, Direction.UP, Direction.UP),  # Win
    (0.58, 0.42, Direction.DOWN, Direction.UP),  # Loss
    (0.46, 0.54, Direction.UP, Direction.DOWN),  # Loss
    (0.54, 0.46, Direction.DOWN, Direction.DOWN),  # Win
    (0.43, 0.57, Direction.UP, Direction.UP),  # Win
    (0.56, 0.44, Direction.DOWN, Direction.UP),  # Loss
]

# Scenario 2: Challenging early streak - 3 losses first
SCENARIO_2 = [
    (0.50, 0.50, Direction.UP, Direction.DOWN),  # Loss
    (0.52, 0.48, Direction.UP, Direction.DOWN),  # Loss
    (0.48, 0.52, Direction.DOWN, Direction.UP),  # Loss
    (0.45, 0.55, Direction.UP, Direction.UP),  # Win - recovery
    (0.47, 0.53, Direction.UP, Direction.UP),  # Win
    (0.55, 0.45, Direction.DOWN, Direction.DOWN),  # Win
    (0.44, 0.56, Direction.UP, Direction.UP),  # Win
    (0.58, 0.42, Direction.DOWN, Direction.UP),  # Loss
    (0.46, 0.54, Direction.UP, Direction.UP),  # Win
    (0.52, 0.48, Direction.DOWN, Direction.DOWN),  # Win
]

# Scenario 3: Expensive tokens (late conviction territory)
SCENARIO_3 = [
    (0.72, 0.28, Direction.UP, Direction.UP),  # Win but expensive
    (0.75, 0.25, Direction.UP, Direction.DOWN),  # Loss on expensive
    (0.68, 0.32, Direction.UP, Direction.UP),  # Win
    (0.70, 0.30, Direction.UP, Direction.DOWN),  # Loss
    (0.74, 0.26, Direction.UP, Direction.UP),  # Win
    (0.30, 0.70, Direction.DOWN, Direction.DOWN),  # Win from other side
    (0.28, 0.72, Direction.DOWN, Direction.UP),  # Loss
    (0.35, 0.65, Direction.DOWN, Direction.DOWN),  # Win
    (0.66, 0.34, Direction.UP, Direction.UP),  # Win
    (0.71, 0.29, Direction.UP, Direction.DOWN),  # Loss
]


def main():
    print("\n" + "=" * 70)
    print("  HEDGE STRATEGY SIMULATION - $10 Bankroll")
    print("=" * 70)

    strategies = [
        (strategy_no_hedge, "NO HEDGE (Baseline)"),
        (strategy_fixed_fraction, "CURRENT: 50% Fixed Hedge"),
        (strategy_drawdown_scaled, "OPTION 1: Drawdown-Scaled Hedge"),
        (strategy_loss_cap, "OPTION 2: 2% Loss Cap Hedge"),
        (strategy_early_stage_preservation, "OPTION 3: Early-Stage Preservation"),
    ]

    print("\n" + "=" * 70)
    print("  SCENARIO 1: 50/50 Win Rate (Typical Mispricing)")
    print("=" * 70)

    for strategy_fn, name in strategies:
        results = run_full_simulation(strategy_fn, name, SCENARIO_1)
        print_results(results, name)

    print("\n" + "=" * 70)
    print("  SCENARIO 2: Bad Luck Streak (3 Losses First)")
    print("  -> Tests survival in early stage")
    print("=" * 70)

    for strategy_fn, name in strategies:
        results = run_full_simulation(strategy_fn, name, SCENARIO_2)
        print_results(results, name)

    print("\n" + "=" * 70)
    print("  SCENARIO 3: Expensive Tokens (Late Conviction)")
    print("  -> Tests when primary bets are > 0.65")
    print("=" * 70)

    for strategy_fn, name in strategies:
        results = run_full_simulation(strategy_fn, name, SCENARIO_3)
        print_results(results, name)

    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print("""
  KEY METRICS TO COMPARE:
  
  1. SURVIVAL: Does the strategy survive all 10 trades without busting?
  
  2. MAX DRAWDOWN: What's the worst point during the sequence?
     (Lower is better for confidence)
  
  3. FINAL BANKROLL: Ending capital after all trades
  
  4. PnL VOLATILITY: How much do individual trades swing?
  
  RECOMMENDATION:
  - For early stage preservation: Option 3 (Early-Stage Preservation)
  - For consistent loss capping: Option 2 (Loss Cap)
  - For adaptive behavior: Option 1 (Drawdown-Scaled)
    """)


if __name__ == "__main__":
    main()
