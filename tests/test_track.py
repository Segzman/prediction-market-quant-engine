# tests/test_track.py
import io
import logging

from state.track import TRACK_COLORS, TRACK_CSS_COLORS, ModelTrack, TrackFormatter


def test_track_colors_defined():
    assert "CONVICTION_ONLY" in TRACK_COLORS
    assert "MISPRICING_ONLY" in TRACK_COLORS
    assert "BOTH" in TRACK_COLORS


def test_track_css_colors_defined():
    assert TRACK_CSS_COLORS["CONVICTION_ONLY"] == "mediumpurple"
    assert TRACK_CSS_COLORS["MISPRICING_ONLY"] == "goldenrod"
    assert TRACK_CSS_COLORS["BOTH"] == "mediumseagreen"


def test_model_track_has_pnl_history():
    from unittest.mock import MagicMock
    track = ModelTrack(
        name="BOTH",
        regime_filter={"MISPRICING", "LATE_CONVICTION"},
        model="A",
        state=MagicMock(),
        stats=MagicMock(),
        ledger=MagicMock(),
        guard=MagicMock(),
        orderer=MagicMock(),
        finder=MagicMock(),
        ansi_color=TRACK_COLORS["BOTH"],
        css_color=TRACK_CSS_COLORS["BOTH"],
        active=True,
    )
    assert track.pnl_history == [0.0]


def test_track_formatter_strips_ansi_when_no_tty():
    formatter = TrackFormatter(track_name="CONVICTION_ONLY", ansi_color="\033[95m")
    handler = logging.StreamHandler(io.StringIO())
    logger = logging.getLogger("test.conviction")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    record = logger.makeRecord("test", logging.INFO, "", 0, "hello", (), None)
    formatted = formatter.format(record)
    # StringIO is not a tty — ANSI must be stripped
    assert "\033[" not in formatted
    assert "CONVICTION" in formatted
    assert "hello" in formatted


def test_track_formatter_force_color(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    formatter = TrackFormatter(track_name="BOTH", ansi_color="\033[92m")
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    formatted = formatter.format(record)
    assert "\033[92m" in formatted


def test_day_vol_v24_needs_minimum_windows():
    import pytest

    from state.session import BotState

    state = BotState()
    assert state.day_vol_v24_bps() is None
    for _ in range(23):
        state.note_finished_window(82000.0, 82050.0)
    assert state.day_vol_v24_bps() is None
    state.note_finished_window(82000.0, 82050.0)
    assert state.day_vol_v24_bps() == pytest.approx(6.097, abs=0.01)
