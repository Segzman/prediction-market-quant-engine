from __future__ import annotations

from config import EDGE_THRESHOLD
from state.types import Direction


def calculate_edge(theo_prob: float, market_price: float, direction: Direction) -> float:
    if direction == Direction.UP:
        return theo_prob - market_price
    return (1 - theo_prob) - market_price


def is_tradeable(edge: float, min_edge: float = EDGE_THRESHOLD) -> bool:
    return edge >= min_edge


def expected_value(edge: float, bet_size: float, market_price: float) -> float:
    if market_price <= 0:
        return 0.0

    # Closed form of the payout math below: true_prob = market_price + edge,
    # so (tp*(1-mp) - (1-tp)*mp) * (bet/mp) reduces to edge*bet/mp.
    return edge * bet_size / market_price


__all__ = ["Direction", "calculate_edge", "expected_value", "is_tradeable"]

