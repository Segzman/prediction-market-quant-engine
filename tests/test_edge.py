from state.types import Direction
from strategy.edge import calculate_edge, expected_value, is_tradeable


def test_positive_edge_up_direction():
    edge = calculate_edge(0.70, 0.60, Direction.UP)
    assert abs(edge - 0.10) < 0.001


def test_negative_edge_up_direction():
    edge = calculate_edge(0.50, 0.65, Direction.UP)
    assert edge < 0


def test_positive_edge_down_direction():
    edge = calculate_edge(0.30, 0.60, Direction.DOWN)
    assert abs(edge - 0.10) < 0.001


def test_is_tradeable_above_threshold():
    assert is_tradeable(0.05, min_edge=0.04) is True


def test_is_not_tradeable_below_threshold():
    assert is_tradeable(0.03, min_edge=0.04) is False


def test_is_not_tradeable_on_zero_edge():
    assert is_tradeable(0.0) is False


def test_expected_value_positive_edge():
    ev = expected_value(edge=0.10, bet_size=5.0, market_price=0.60)
    assert ev > 0


def test_expected_value_negative_edge():
    ev = expected_value(edge=-0.10, bet_size=5.0, market_price=0.60)
    assert ev < 0

