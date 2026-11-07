from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state.window import WindowState
from strategy.signal import evaluate


SECONDS_PER_WINDOW = 300
WINDOWS_PER_HOUR = 12
ENTRY_EVAL_SECONDS = range(210, 286, 5)
MODELS = ("A", "B", "C")


@dataclass
class ModelStats:
    windows: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_5usd: float = 0.0
    total_edge: float = 0.0
    total_fill_price: float = 0.0

    @property
    def avg_edge(self) -> float:
        return self.total_edge / self.trades if self.trades else 0.0

    @property
    def avg_fill_price(self) -> float:
        return self.total_fill_price / self.trades if self.trades else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def trades_per_hour(self) -> float:
        return self.trades / (self.windows / WINDOWS_PER_HOUR) if self.windows else 0.0

    @property
    def pnl_per_hour_5usd(self) -> float:
        return self.total_pnl_5usd / (self.windows / WINDOWS_PER_HOUR) if self.windows else 0.0


def simulate_price_path(rng: random.Random, open_price: float = 100.0) -> list[float]:
    latent_trend = rng.gauss(0.0, 1.8e-5)
    noise_sigma = 1.1e-4
    price = open_price
    path = [price]
    for _ in range(SECONDS_PER_WINDOW):
        step_return = latent_trend + rng.gauss(0.0, noise_sigma)
        price *= math.exp(step_return)
        path.append(price)
    return path


def market_asks(
    open_price: float,
    current_price: float,
    seconds_remaining: int,
    rng: random.Random,
) -> tuple[float, float]:
    move_pct = (current_price - open_price) / open_price
    time_factor = 1 - (seconds_remaining / SECONDS_PER_WINDOW)

    # Synthetic market: partially prices momentum, but underreacts and carries noise.
    midpoint = 0.5 + (move_pct / 0.0005) * time_factor * 0.05 + rng.gauss(0.0, 0.012)
    midpoint = min(0.94, max(0.06, midpoint))
    spread = 0.01
    up_ask = min(0.95, max(0.05, midpoint + spread))
    down_ask = min(0.95, max(0.05, (1 - midpoint) + spread))
    return up_ask, down_ask


def rolling_volatility(path: list[float], elapsed_seconds: int) -> float:
    start = max(1, elapsed_seconds - 30)
    returns = [
        math.log(path[i] / path[i - 1])
        for i in range(start, elapsed_seconds + 1)
        if path[i - 1] > 0 and path[i] > 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns)


def estimate_fee_usdc(stake: float, fill_price: float) -> float:
    """Crypto taker estimate per leg. Tuning is always net of fees."""
    if stake <= 0 or fill_price <= 0:
        return 0.0
    return stake * 0.072 * fill_price * (1 - fill_price)


def resolve_trade_pnl(stake: float, fill_price: float, won: bool) -> float:
    fees = estimate_fee_usdc(stake, fill_price) * 2  # entry + exit legs
    if won:
        return stake * (1 / fill_price - 1) - fees
    return -stake - fees


def simulate_model_on_window(
    path: list[float],
    model: str,
    rng: random.Random,
    *,
    stake: float = 5.0,
) -> tuple[bool, float, float] | None:
    window = WindowState(
        start_ts=0,
        end_ts=SECONDS_PER_WINDOW,
        market_id="sim-window",
        up_token_id="up",
        down_token_id="down",
        open_price=path[0],
    )

    for elapsed in ENTRY_EVAL_SECONDS:
        seconds_remaining = SECONDS_PER_WINDOW - elapsed
        current_price = path[elapsed]
        up_ask, down_ask = market_asks(path[0], current_price, seconds_remaining, rng)
        volatility = rolling_volatility(path, elapsed)
        signal = evaluate(
            window,
            current_price,
            seconds_remaining,
            up_ask,
            down_ask,
            volatility=volatility,
            model=model,
        )
        if signal is None:
            continue

        resolved_up = path[-1] >= path[0]
        won = (
            (signal.direction.value == "UP" and resolved_up)
            or (signal.direction.value == "DOWN" and not resolved_up)
        )
        pnl = resolve_trade_pnl(stake, signal.market_price, won)
        return won, pnl, signal.market_price, signal.edge

    return None


def run_simulation(hours: int, sessions: int, seed: int) -> dict[str, ModelStats]:
    rng = random.Random(seed)
    stats = {model: ModelStats() for model in MODELS}

    for _ in range(sessions):
        for _ in range(hours * WINDOWS_PER_HOUR):
            path = simulate_price_path(rng)
            for model in MODELS:
                stats[model].windows += 1
                result = simulate_model_on_window(path, model, rng)
                if result is None:
                    continue

                won, pnl, fill_price, edge = result
                model_stats = stats[model]
                model_stats.trades += 1
                model_stats.total_pnl_5usd += pnl
                model_stats.total_fill_price += fill_price
                model_stats.total_edge += edge
                if won:
                    model_stats.wins += 1
                else:
                    model_stats.losses += 1

    return stats


def daily_pnl(trades_per_hour: float, win_rate: float, stake: float, avg_fill_price: float) -> float:
    trades_per_day = trades_per_hour * 24
    win_profit = stake * (1 / avg_fill_price - 1)
    loss_amount = stake
    ev_per_trade = win_rate * win_profit - (1 - win_rate) * loss_amount
    return trades_per_day * ev_per_trade


def render_summary(stats: dict[str, ModelStats], *, sessions: int, hours: int) -> None:
    print(f"Simulated {sessions} independent {hours}-hour sessions ({sessions * hours * WINDOWS_PER_HOUR} windows)")
    print()
    print("| Model | Trades/Hour | Win Rate | Avg Fill | Avg Edge | PnL/Hour ($5) |")
    print("|---|---:|---:|---:|---:|---:|")
    for model in MODELS:
        item = stats[model]
        label = model
        if model == "C":
            label = "C (falls back to A without calibration data)"
        print(
            f"| {label} | {item.trades_per_hour:.2f} | {item.win_rate:.1%} | "
            f"{item.avg_fill_price:.3f} | {item.avg_edge:.3f} | {item.pnl_per_hour_5usd:.2f} |"
        )


def render_daily_table(stats: dict[str, ModelStats], *, stake: float) -> None:
    rates = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    print()
    print(f"Daily PnL assuming ${stake:.0f} per trade and the simulated trade frequency for each model")
    print()
    print("| Win Rate | A / day | B / day | C / day |")
    print("|---|---:|---:|---:|")
    for rate in rates:
        values = []
        for model in MODELS:
            item = stats[model]
            if item.avg_fill_price == 0:
                values.append("n/a")
                continue
            pnl = daily_pnl(item.trades_per_hour, rate, stake, item.avg_fill_price)
            values.append(f"${pnl:,.2f}")
        print(f"| {rate:.0%} | {values[0]} | {values[1]} | {values[2]} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--sessions", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--daily-stake", type=float, default=10.0)
    args = parser.parse_args()

    stats = run_simulation(args.hours, args.sessions, args.seed)
    render_summary(stats, sessions=args.sessions, hours=args.hours)
    render_daily_table(stats, stake=args.daily_stake)


if __name__ == "__main__":
    main()
