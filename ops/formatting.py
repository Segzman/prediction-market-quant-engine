from __future__ import annotations


def format_runtime(total_seconds: float) -> str:
    total = max(0, int(total_seconds))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_price(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"${value:.{digits}f}"


def format_prob(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def format_book_snapshot(label: str, snapshot) -> str:
    if snapshot is None:
        return f"{label} a-/b- ask=- bid=-"
    ask = "-" if snapshot.best_ask is None else f"{snapshot.best_ask:.3f}"
    bid = "-" if snapshot.best_bid is None else f"{snapshot.best_bid:.3f}"
    return f"{label} a{snapshot.ask_count}/b{snapshot.bid_count} ask={ask} bid={bid}"


def format_age(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, rem = divmod(int(seconds), 60)
    return f"{minutes}m {rem}s"


def fit_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return f"{text[:width - 3]}..."
