#!/usr/bin/env python3
"""
Detailed analysis of Option 1: Drawdown-Scaled Hedge Strategy
Shows exactly what happens in each trade.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from __future__ import annotations

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
class TradeDetail:
    trade_num: int
    bankroll_before: float
    bankroll_ratio: float
    primary_bet: float
    hedge_bet: float
    hedge_pct: float
    primary_price: float
    hedge_price: float
    direction: str
    actual: str
    primary_outcome: str
    hedge_outcome: str
    gross_pnl: float
    fees: float
    net_pnl: float
    bankroll_after: float
    cumulative_pnl: float


# Constants
MIN_BET = 1.0
FEE_RATE = 0.072


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
        label = "35% (bankroll >= 100%)"
    elif ratio >= 0.8:
        fraction = base_fraction  # 50%
        label = "50% (bankroll >= 80%)"
    elif ratio >= 0.6:
        fraction = base_fraction * 1.5  # 75%
        label = "75% (bankroll >= 60%)"
    elif ratio >= 0.4:
        fraction = base_fraction * 2.0  # 100%
        label = "100% (bankroll >= 40%)"
    else:
        fraction = base_fraction * 2.5  # 125%
        label = "125% (bankroll < 40% - CRITICAL)"

    hedge = min(primary_bet * fraction, bankroll * 0.9 - primary_bet)
    if hedge < MIN_BET:
        return PositionPlan(primary_bet, 0.0, 0.0, False, f"HEDGE_SKIP_{label}")
    return PositionPlan(primary_bet, round(hedge, 2), 0.0, True, label)


def calculate_pnl(
    primary_bet: float,
    hedge_bet: float,
    primary_direction: Direction,
    primary_price: float,
    hedge_price: float,
    actual_outcome: Direction,
) -> tuple[float, float, str, str]:
    """Returns (gross_pnl, fees, primary_outcome_desc, hedge_outcome_desc)."""
    # Primary outcome
    if primary_direction == actual_outcome:
        primary_return = primary_bet * (1 - primary_price) / primary_price
        primary_desc = f"WIN +${primary_return:.2f}"
    else:
        primary_return = -primary_bet
        primary_desc = f"LOSS -${primary_bet:.2f}"

    # Hedge outcome
    hedge_direction = Direction.DOWN if primary_direction == Direction.UP else Direction.UP
    if hedge_direction == actual_outcome:
        hedge_return = hedge_bet * (1 - hedge_price) / hedge_price
        hedge_desc = f"WIN +${hedge_return:.2f}"
    else:
        hedge_return = -hedge_bet
        hedge_desc = f"LOSS -${hedge_bet:.2f}"

    gross_pnl = primary_return + hedge_return
    fees = (primary_bet + hedge_bet) * FEE_RATE

    return gross_pnl, fees, primary_desc, hedge_desc


# Scenario 1: 50/50 win rate
SCENARIO_1 = [
    (0.45, 0.55, Direction.UP, Direction.UP,    "UP was underpriced @ 0.45, actually won"),
    (0.55, 0.45, Direction.DOWN, Direction.UP,    "DOWN bet @ 0.55, but UP won - wrong direction"),
    (0.48, 0.52, Direction.UP, Direction.DOWN,  "UP bet @ 0.48, but DOWN won - wrong"),
    (0.52, 0.48, Direction.DOWN, Direction.DOWN,"DOWN bet @ 0.52, DOWN won - correct"),
    (0.44, 0.56, Direction.UP, Direction.UP,   "UP underpriced @ 0.44, UP won"),
    (0.58, 0.42, Direction.DOWN, Direction.UP,  "DOWN bet @ 0.58, UP won - wrong"),
    (0.46, 0.54, Direction.UP, Direction.DOWN,  "UP bet @ 0.46, DOWN won - wrong"),
    (0.54, 0.46, Direction.DOWN, Direction.DOWN,"DOWN bet @ 0.54, DOWN won - correct"),
    (0.43, 0.57, Direction.UP, Direction.UP,    "UP underpriced @ 0.43, UP won"),
    (0.56, 0.44, Direction.DOWN, Direction.UP,  "DOWN bet @ 0.56, UP won - wrong"),
]


def run_detailed_simulation():
    bankroll = 10.0
    starting_bankroll = 10.0
    trades = []

    print("\n" + "=" * 120)
    print("OPTION 1: DRAWDOWN-SCALED HEDGE - DETAILED TRADE ANALYSIS")
    print("=" * 120)
    print("\nSTRATEGY RULES:")
    print("  Bankroll >= 100% → Hedge = 35% of primary")
    print("  Bankroll >=  80% → Hedge = 50% of primary")
    print("  Bankroll >=  60% → Hedge = 75% of primary")
    print("  Bankroll >=  40% → Hedge = 100% of primary")
    print("  Bankroll <   40% → Hedge = 125% of primary (CRITICAL MODE)")
    print("\n" + "=" * 120)

    for i, (primary_price, hedge_price, direction, outcome, description) in enumerate(SCENARIO_1):
        trade_num = i + 1
        bankroll_before = bankroll
        bankroll_ratio = bankroll_before / starting_bankroll

        # Determine bet size (30% of bankroll, max $3)
        primary_bet = min(3.0, bankroll * 0.3)

        # Get hedge plan
        plan = strategy_drawdown_scaled(
            primary_bet=primary_bet,
            bankroll=bankroll,
            starting_bankroll=starting_bankroll,
            primary_price=primary_price,
            hedge_price=hedge_price,
            trade_count=i,
        )

        # Calculate PnL
        gross, fees, primary_desc, hedge_desc = calculate_pnl(
            plan.primary_usdc,
            plan.hedge_usdc,
            direction,
            primary_price,
            hedge_price,
            outcome,
        )
        net = gross - fees
        bankroll += net

        # Calculate hedge percentage
        hedge_pct = (plan.hedge_usdc / plan.primary_usdc * 100) if plan.primary_usdc > 0 else 0

        trades.append(TradeDetail(
            trade_num=trade_num,
            bankroll_before=bankroll_before,
            bankroll_ratio=bankroll_ratio,
            primary_bet=plan.primary_usdc,
            hedge_bet=plan.hedge_usdc,
            hedge_pct=hedge_pct,
            primary_price=primary_price,
            hedge_price=hedge_price,
            direction=direction.value,
            actual=outcome.value,
            primary_outcome=primary_desc,
            hedge_outcome=hedge_desc,
            gross_pnl=gross,
            fees=fees,
            net_pnl=net,
            bankroll_after=bankroll,
            cumulative_pnl=bankroll - starting_bankroll,
        ))

    # Print detailed table
    print("\nDETAILED TRADE TABLE:")
    print("-" * 120)
    header = f"{'#':>3} | {'BR Before':>9} | {'Ratio':>6} | {'Hedge%':>7} | {'Primary':>7} | {'Hedge':>7} | {'Prim Px':>7} | {'Hedge Px':>8} | {'Dir→Actual':>10} | {'Gross':>7} | {'Fees':>6} | {'Net':>7} | {'BR After':>9}"
    print(header)
    print("-" * 120)

    for t in trades:
        ratio_pct = f"{t.bankroll_ratio*100:.0f}%"
        hedge_pct_str = f"{t.hedge_pct:.0f}%"
        gross_str = f"${t.gross_pnl:+.2f}"
        fees_str = f"${t.fees:.2f}"
        net_str = f"${t.net_pnl:+.2f}"

        row = f"{t.trade_num:>3} | ${t.bankroll_before:>8.2f} | {ratio_pct:>6} | {hedge_pct_str:>7} | ${t.primary_bet:>6.2f} | ${t.hedge_bet:>6.2f} | ${t.primary_price:>6.2f} | ${t.hedge_price:>7.2f} | {t.direction:>3}→{t.actual:>3} | {gross_str:>8} | {fees_str:>6} | {net_str:>8} | ${t.bankroll_after:>8.2f}"
        print(row)

    print("-" * 120)

    # Print narrative breakdown
    print("\n\nNARRATIVE BREAKDOWN:")
    print("=" * 120)

    for t in trades:
        print(f"\n--- Trade {t.trade_num} ---")
        print(f"  Bankroll BEFORE: ${t.bankroll_before:.2f} ({t.bankroll_ratio*100:.0f}% of start)")
        print(f"  → Hedge rule: {t.hedge_pct:.0f}% because ratio triggers that level")
        print(f"  → Bets: ${t.primary_bet:.2f} primary + ${t.hedge_bet:.2f} hedge = ${t.primary_bet + t.hedge_bet:.2f} total")
        print(f"  → Prices: Primary ${t.primary_price:.2f} vs Hedge ${t.hedge_price:.2f}")
        print(f"  → Direction: Bet {t.direction}, Actual was {t.actual}")
        print(f"  → Primary outcome: {t.primary_outcome}")
        print(f"  → Hedge outcome: {t.hedge_outcome}")
        print(f"  → Gross PnL: ${t.gross_pnl:+.2f}")
        print(f"  → Fees (2%): ${t.fees:.2f}")
        print(f"  → NET PnL: ${t.net_pnl:+.2f}")
        print(f"  Bankroll AFTER: ${t.bankroll_after:.2f}")

    # Summary
    print("\n\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    start = 10.0
    end = trades[-1].bankroll_after
    print(f"  Starting Bankroll: ${start:.2f}")
    print(f"  Ending Bankroll:   ${end:.2f}")
    print(f"  Total Return:       ${end - start:+.2f} ({((end - start) / start * 100):+.1f}%)")

    wins = sum(1 for t in trades if t.net_pnl > 0)
    losses = sum(1 for t in trades if t.net_pnl <= 0)
    print(f"  Winning trades: {wins}")
    print(f"  Losing trades:  {losses}")

    max_dd = min(t.cumulative_pnl for t in trades)
    max_dd_trade = [t.trade_num for t in trades if t.cumulative_pnl == max_dd][0]
    print(f"  Worst drawdown: ${max_dd:.2f} (happened at trade {max_dd_trade})")

    avg_hedge = sum(t.hedge_pct for t in trades) / len(trades)
    print(f"  Average hedge %: {avg_hedge:.0f}%")
    print("=" * 120)


if __name__ == "__main__":
    run_detailed_simulation()
