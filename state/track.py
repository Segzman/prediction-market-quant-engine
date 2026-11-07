from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot import RuntimeStats
    from market.finder import MarketFinder
    from market.orderer import Orderer
    from persistence.ledger import Ledger
    from risk.guard import DailyGuard
    from state.session import BotState

TRACK_COLORS: dict[str, str] = {
    "CONVICTION_ONLY": "\033[95m",   # bright magenta
    "MISPRICING_ONLY": "\033[33m",   # yellow
    "BOTH":            "\033[92m",   # bright green
}

TRACK_CSS_COLORS: dict[str, str] = {
    "CONVICTION_ONLY": "mediumpurple",
    "MISPRICING_ONLY": "goldenrod",
    "BOTH":            "mediumseagreen",
}

_RESET = "\033[0m"


class TrackFormatter(logging.Formatter):
    """Logging formatter that prepends a colored track prefix to every line.

    ANSI codes are stripped unless the root logger has a TTY stream handler, or FORCE_COLOR=1.
    """

    def __init__(self, track_name: str, ansi_color: str, **kwargs):
        super().__init__(**kwargs)
        self._track_name = track_name
        self._ansi_color = ansi_color
        # Fixed-width label: e.g. "[ CONVICTION ]  "
        label_inner = track_name.replace("_ONLY", "").replace("_", " ")
        # Truncate/pad to 10 chars for alignment
        label_inner = label_inner[:10].ljust(10)
        self._label = f"[ {label_inner}]  "

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        force_color = os.getenv("FORCE_COLOR", "").strip() in {"1", "true", "yes"}
        # Check per-record whether the handler stream is a tty
        use_color = force_color
        if not use_color:
            for handler in logging.getLogger().handlers:
                if hasattr(handler, "stream") and callable(
                    getattr(handler.stream, "isatty", None)
                ):
                    if handler.stream.isatty():
                        use_color = True
                        break
        if use_color:
            return f"{self._ansi_color}{self._label}{_RESET}{msg}"
        return f"{self._label}{msg}"


@dataclass
class ModelTrack:
    name: Literal["CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"]
    regime_filter: set[str]                # {"MISPRICING"} | {"LATE_CONVICTION"} | both
    model: Literal["A", "B", "C"]          # primary probability engine
    state: BotState
    stats: RuntimeStats                    # per-track RuntimeStats
    ledger: Ledger
    guard: DailyGuard
    orderer: Orderer
    finder: MarketFinder
    ansi_color: str
    css_color: str
    active: bool
    pnl_history: list[float] = field(default_factory=lambda: [0.0])
