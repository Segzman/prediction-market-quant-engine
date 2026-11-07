import time

from state.window import WindowPhase, WindowState


def make_window(seconds_until_end: int) -> WindowState:
    now = int(time.time())
    end_ts = now + seconds_until_end
    return WindowState(
        start_ts=end_ts - 300,
        end_ts=end_ts,
        market_id="x",
        up_token_id="u",
        down_token_id="d",
    )


def test_phase_waiting_when_far_from_end():
    w = make_window(297)  # > ENTRY_WINDOW_START=295, so still WAITING
    assert w.get_phase(int(time.time())) == WindowPhase.WAITING


def test_phase_entry_window_at_60s():
    w = make_window(60)
    assert w.get_phase(int(time.time())) == WindowPhase.ENTRY_WINDOW


def test_phase_too_late_at_3s():
    w = make_window(3)  # < ENTRY_WINDOW_END=60, so TOO_LATE
    assert w.get_phase(int(time.time())) == WindowPhase.TOO_LATE


def test_phase_expired_at_minus_5s():
    w = make_window(-5)
    assert w.get_phase(int(time.time())) == WindowPhase.EXPIRED


def test_is_in_entry_window_true():
    w = make_window(60)
    assert w.is_in_entry_window(int(time.time())) is True


def test_set_open_price_only_sets_once():
    w = make_window(120)
    w.set_open_price(82000)
    w.set_open_price(83000)
    assert w.open_price == 82000


def test_seconds_remaining_positive():
    w = make_window(60)
    assert w.seconds_remaining(int(time.time())) > 0


def test_seconds_remaining_floors_at_zero():
    w = make_window(-10)
    assert w.seconds_remaining(int(time.time())) == 0
