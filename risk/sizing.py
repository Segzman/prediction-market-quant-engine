from __future__ import annotations

import math
from dataclasses import dataclass

from config import (
    BANKROLL_RISK_FRACTION,
    EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC,
    EARLY_ENTRY_MIN_SCORE,
    HEDGE_BANKROLL_RESERVE_FRACTION,
    HEDGE_MAX_LOSS_PCT,
    HEDGE_USE_LOSS_CAP,
    KELLY_FRACTION,
    LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC,
    LATE_CONVICTION_SIZE_MULTIPLIER,
    LIQUIDITY_MIN_SCORE,
    LOSS_STREAK_BET_STEP,
    MAX_BET_USDC,
    MIN_BET_USDC,
    MIN_EXPECTED_PROFIT_USDC,
    STARTING_BANKROLL_USDC,
    USE_KELLY,
    WIN_STREAK_BET_STEP,
)


@dataclass
class PositionPlan:
    primary_usdc: float
    hedge_usdc: float
    reserve_usdc: float
    hedge_active: bool
    hedge_reason: str = ""


def _setup_tier(
    edge: float,
    market_price: float,
    *,
    entry_regime: str = "MISPRICING",
    liquidity_score: float | None = None,
    net_expected_profit_usdc: float | None = None,
) -> str:
    if liquidity_score is not None and liquidity_score < LIQUIDITY_MIN_SCORE:
        return "WEAK"
    expected_profit_floor = (
        LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC
        if entry_regime == "LATE_CONVICTION"
        else MIN_EXPECTED_PROFIT_USDC
    )
    if (
        net_expected_profit_usdc is not None
        and net_expected_profit_usdc < expected_profit_floor
    ):
        return "WEAK"
    if entry_regime == "LATE_CONVICTION":
        if edge >= 0.03 and market_price <= 0.78:
            return "MEDIUM"
        return "WEAK"
    if (
        edge >= 0.12
        and 0.20 <= market_price <= 0.60
        and (
            liquidity_score is None or liquidity_score >= EARLY_ENTRY_MIN_SCORE
        )
        and (
            net_expected_profit_usdc is None
            or net_expected_profit_usdc >= EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC
        )
    ):
        return "STRONG"
    if edge >= 0.06 and 0.15 <= market_price <= 0.70:
        return "MEDIUM"
    return "WEAK"


def _setup_tier_multiplier(tier: str) -> float:
    if tier == "STRONG":
        return 1.20
    if tier == "MEDIUM":
        return 1.00
    return 0.70


def _entry_regime_multiplier(entry_regime: str) -> float:
    if entry_regime == "LATE_CONVICTION":
        return LATE_CONVICTION_SIZE_MULTIPLIER
    return 1.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def fixed_size(max_bet: float = MAX_BET_USDC, bankroll: float | None = None) -> float:
    if bankroll is None:
        return max_bet
    return max(0.0, min(max_bet, bankroll))


def kelly_size(
    edge: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = KELLY_FRACTION,
    max_bet: float = MAX_BET_USDC,
) -> float:
    if edge <= 0 or market_price <= 0 or market_price >= 1 or bankroll <= 0:
        return 0.0

    true_prob = market_price + edge
    b = (1 - market_price) / market_price
    if b <= 0:
        return 0.0

    kelly_f = (b * true_prob - (1 - true_prob)) / b
    if kelly_f <= 0:
        return 0.0
    bet = bankroll * kelly_fraction * kelly_f
    return min(max_bet, bet)


def adaptive_bankroll_size(
    edge: float,
    market_price: float,
    available_bankroll: float,
    *,
    current_bankroll: float,
    starting_bankroll: float = STARTING_BANKROLL_USDC,
    bankroll_fraction: float = BANKROLL_RISK_FRACTION,
    consecutive_wins: int = 0,
    consecutive_losses: int = 0,
    entry_regime: str = "MISPRICING",
    liquidity_score: float | None = None,
    net_expected_profit_usdc: float | None = None,
    same_side_exposure_usdc: float = 0.0,
    total_open_exposure_usdc: float = 0.0,
    min_bet: float = MIN_BET_USDC,
    max_bet: float = MAX_BET_USDC,
    use_kelly: bool = USE_KELLY,
) -> float:
    if available_bankroll <= 0 or current_bankroll <= 0 or starting_bankroll <= 0:
        return 0.0

    bankroll_cap = min(max_bet, available_bankroll)
    if bankroll_cap <= 0:
        return 0.0

    if use_kelly and edge > 0:
        kelly_bet = kelly_size(
            edge=edge,
            market_price=market_price,
            bankroll=available_bankroll,
            max_bet=bankroll_cap,
        )
        if kelly_bet > 0:
            kelly_cap: float | None = round(kelly_bet, 2)
        else:
            kelly_cap = None
    else:
        kelly_cap = None

    tier = _setup_tier(
        edge,
        market_price,
        entry_regime=entry_regime,
        liquidity_score=liquidity_score,
        net_expected_profit_usdc=net_expected_profit_usdc,
    )
    bankroll_ratio = current_bankroll / max(starting_bankroll, 0.01)
    bankroll_factor = _clamp(math.sqrt(max(bankroll_ratio, 0.0)), 0.5, 1.75)
    streak_factor = _clamp(
        1.0
        + min(max(consecutive_wins, 0), 3) * WIN_STREAK_BET_STEP
        - min(max(consecutive_losses, 0), 4) * LOSS_STREAK_BET_STEP,
        0.35,
        1.35,
    )
    edge_factor = _clamp(1.0 + max(edge, 0.0) * 2.0, 0.85, 1.25)
    tier_factor = _setup_tier_multiplier(tier)
    regime_factor = _entry_regime_multiplier(entry_regime)
    bankroll_base = max(current_bankroll, min_bet)
    same_side_ratio = max(0.0, same_side_exposure_usdc) / bankroll_base
    total_exposure_ratio = max(0.0, total_open_exposure_usdc) / bankroll_base
    same_side_factor = _clamp(1.0 - (same_side_ratio * 0.65), 0.35, 1.0)
    total_exposure_factor = _clamp(1.0 - (max(total_exposure_ratio - 0.10, 0.0) * 0.55), 0.45, 1.0)
    bet = (
        available_bankroll
        * bankroll_fraction
        * bankroll_factor
        * streak_factor
        * edge_factor
        * tier_factor
        * regime_factor
        * same_side_factor
        * total_exposure_factor
    )

    if bankroll_cap < min_bet:
        return round(bankroll_cap, 2)

    bet = _clamp(bet, min_bet, bankroll_cap)
    if kelly_cap is not None:
        # Kelly acts as an upper bound; liquidity, streak, and exposure
        # controls above still apply instead of being bypassed.
        bet = min(bet, kelly_cap)
    return round(bet, 2)


def get_bet_size(
    edge: float,
    market_price: float,
    bankroll: float,
    *,
    current_bankroll: float | None = None,
    starting_bankroll: float = STARTING_BANKROLL_USDC,
    consecutive_wins: int = 0,
    consecutive_losses: int = 0,
    entry_regime: str = "MISPRICING",
    liquidity_score: float | None = None,
    net_expected_profit_usdc: float | None = None,
    same_side_exposure_usdc: float = 0.0,
    total_open_exposure_usdc: float = 0.0,
    use_kelly: bool = USE_KELLY,
) -> float:
    active_bankroll = bankroll if current_bankroll is None else current_bankroll
    return adaptive_bankroll_size(
        edge=edge,
        market_price=market_price,
        available_bankroll=bankroll,
        current_bankroll=active_bankroll,
        starting_bankroll=starting_bankroll,
        consecutive_wins=consecutive_wins,
        consecutive_losses=consecutive_losses,
        entry_regime=entry_regime,
        liquidity_score=liquidity_score,
        net_expected_profit_usdc=net_expected_profit_usdc,
        same_side_exposure_usdc=same_side_exposure_usdc,
        total_open_exposure_usdc=total_open_exposure_usdc,
        use_kelly=use_kelly,
    )


def plan_position_sizes(
    target_primary_bet: float,
    available_bankroll: float,
    *,
    hedge_enabled: bool,
    hedge_fraction: float,
    hedge_price: float | None = None,
    use_loss_cap: bool = HEDGE_USE_LOSS_CAP,
    max_loss_pct: float = HEDGE_MAX_LOSS_PCT,
    min_bet: float = MIN_BET_USDC,
    max_bet: float = MAX_BET_USDC,
    reserve_fraction: float = HEDGE_BANKROLL_RESERVE_FRACTION,
) -> PositionPlan:
    """Plan position sizes with optional 2% loss cap hedging.

    When use_loss_cap is True, the hedge is sized to ensure the maximum
    loss on the combined position does not exceed max_loss_pct of bankroll.
    Minimum hedge size is always enforced (default $1).
    """
    if available_bankroll <= 0:
        return PositionPlan(0.0, 0.0, 0.0, False, "HEDGE_SKIPPED_NO_BANKROLL")

    # Need at least min_bet for primary + min_bet for hedge
    if hedge_enabled and available_bankroll < (min_bet * 2):
        primary = round(min(target_primary_bet, min(max_bet, available_bankroll)), 2)
        if primary > 0 and available_bankroll >= min_bet:
            primary = round(_clamp(primary, min_bet, min(max_bet, available_bankroll)), 2)
        return PositionPlan(primary, 0.0, 0.0, False, "HEDGE_SKIPPED_BANKROLL_TOO_SMALL")

    reserve = round(max(0.0, available_bankroll * reserve_fraction), 2)
    usable_bankroll = max(0.0, available_bankroll - reserve)
    primary_cap = min(max_bet, usable_bankroll)
    if primary_cap < min_bet:
        usable_bankroll = available_bankroll
        reserve = round(max(0.0, available_bankroll - usable_bankroll), 2)
        primary_cap = min(max_bet, usable_bankroll)
    if primary_cap < min_bet:
        return PositionPlan(
            round(primary_cap, 2),
            0.0,
            reserve,
            False,
            "HEDGE_SKIPPED_PRIMARY_BELOW_MIN",
        )

    primary = min(target_primary_bet, primary_cap)
    primary = round(_clamp(primary, min_bet, primary_cap), 2)
    if not hedge_enabled or hedge_fraction <= 0:
        return PositionPlan(primary, 0.0, reserve, False, "HEDGE_DISABLED")

    remaining = round(usable_bankroll - primary, 2)

    # Calculate hedge size using 2% loss cap if enabled and hedge_price available
    if use_loss_cap and hedge_price is not None and hedge_price > 0 and hedge_price < 1.0:
        max_allowed_loss = available_bankroll * max_loss_pct
        primary_loss_if_wrong = primary
        required_hedge_profit = primary_loss_if_wrong - max_allowed_loss

        if required_hedge_profit > 0:
            hedge_payout_per_dollar = 1.0 - hedge_price
            if hedge_payout_per_dollar > 0:
                loss_cap_hedge = required_hedge_profit / hedge_payout_per_dollar
                # Use the larger of: loss_cap required hedge or fraction-based hedge
                fraction_hedge = primary * hedge_fraction
                desired_hedge = max(loss_cap_hedge, fraction_hedge)
            else:
                desired_hedge = primary * hedge_fraction
        else:
            # No hedge needed - primary loss already within limit
            return PositionPlan(primary, 0.0, reserve, False, "LOSS_CAP_NO_HEDGE_NEEDED")
    else:
        # Fall back to fraction-based hedge
        desired_hedge = primary * hedge_fraction

    # Ensure minimum hedge size
    desired_hedge = max(min_bet, desired_hedge)
    desired_hedge = round(desired_hedge, 2)

    # Check if we can afford the hedge
    if desired_hedge > remaining:
        # Scale down proportionally to fit within bankroll
        scaled_total = usable_bankroll
        scaled_primary = scaled_total / (1 + (desired_hedge / primary))
        scaled_hedge = scaled_total - scaled_primary

        if scaled_primary >= min_bet and scaled_hedge >= min_bet:
            return PositionPlan(
                primary_usdc=round(min(max_bet, scaled_primary), 2),
                hedge_usdc=round(scaled_hedge, 2),
                reserve_usdc=reserve,
                hedge_active=True,
                hedge_reason="HEDGE_SCALED_TO_FIT",
            )
        else:
            # Can't fit minimum hedge, skip it
            return PositionPlan(primary, 0.0, reserve, False, "HEDGE_SKIPPED_BANKROLL_TOO_SMALL")

    # Can afford the hedge
    return PositionPlan(primary, desired_hedge, reserve, True, "HEDGE_PLANNED")
