"""Exit-parameter sweep on synthetic windows (fast, comparative only).

Entry via the real strategy.evaluate(); the hold is then emulated tick
by tick against the same synthetic ask generator. Compares TP1/TP2/
trail/SL variants on identical paths. Not calibrated to live slippage.
"""

from __future__ import annotations

import argparse
import itertools
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state.window import WindowState
from strategy.signal import evaluate
from scripts.simulate_models import (
    ENTRY_EVAL_SECONDS,
    SECONDS_PER_WINDOW,
    market_asks,
    rolling_volatility,
    simulate_price_path,
)


@dataclass
class ExitConfig:
    tp1_pct: float = 0.08
    tp1_frac: float = 0.5
    tp2_pct: float = 0.15
    sl_pct: float = 0.10
    trail_pct: float = 0.0
    time_exit: int = 15


def estimate_fee(bet_size: float, market_price: float) -> float:
    """Crypto taker estimate per leg; mirrors estimate_trade_fee_usdc."""
    if bet_size <= 0 or market_price <= 0:
        return 0.0
    return bet_size * 0.072 * market_price * (1 - market_price)


def emulate_hold(
    path: list[float],
    entry_elapsed: int,
    direction: str,
    entry_price: float,
    stake: float,
    cfg: ExitConfig,
    rng: random.Random,
) -> float:
    remaining = stake
    realized = -estimate_fee(stake, entry_price)
    tp1_price = entry_price * (1 + cfg.tp1_pct)
    tp2_price = entry_price * (1 + cfg.tp2_pct)
    sl_price = entry_price * (1 - cfg.sl_pct)
    tp1_taken = cfg.tp1_frac <= 0 or tp2_price <= tp1_price
    high = entry_price
    entry_ts = entry_elapsed
    for tick in range(entry_elapsed + 1, SECONDS_PER_WINDOW):
        up_ask, down_ask = market_asks(path[0], path[tick], SECONDS_PER_WINDOW - tick, rng)
        token = up_ask if direction == "UP" else down_ask
        high = max(high, token)
        eff_sl = sl_price
        if cfg.trail_pct > 0 and high > entry_price:
            eff_sl = max(eff_sl, high * (1 - cfg.trail_pct))
        if not tp1_taken and token >= tp1_price:
            part = remaining * cfg.tp1_frac
            realized += part * (token / entry_price - 1) - estimate_fee(part, token)
            remaining -= part
            tp1_taken = True
        if token >= tp2_price or token <= eff_sl or (tick - entry_ts) >= cfg.time_exit:
            realized += remaining * (token / entry_price - 1) - estimate_fee(remaining, token)
            return realized
    up_ask, down_ask = market_asks(path[0], path[-1], 0, rng)
    token = up_ask if direction == "UP" else down_ask
    realized += remaining * (token / entry_price - 1) - estimate_fee(remaining, token)
    return realized


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x]


def run_sweep(
    hours: int,
    sessions: int,
    seed: int,
    stake: float,
    *,
    tp1_list: list[float],
    sl_list: list[float],
    texit_list: list[int],
    trail_list: list[float],
) -> None:
    configs = {}
    for tp1, sl, texit, trail in itertools.product(tp1_list, sl_list, texit_list, trail_list):
        configs[f"tp{tp1:g}_sl{sl:g}_tx{texit}_tr{trail:g}"] = ExitConfig(
            tp1_pct=tp1, tp1_frac=0.5, tp2_pct=0.15,
            sl_pct=sl, trail_pct=trail, time_exit=texit,
        )
    totals: dict[str, list[float]] = {name: [] for name in configs}
    for session in range(sessions):
        rng = random.Random(seed * 1000 + session)
        for _ in range(hours * 12):
            path = simulate_price_path(rng)
            window = WindowState(
                start_ts=0, end_ts=SECONDS_PER_WINDOW, market_id="w",
                up_token_id="up", down_token_id="down", open_price=path[0],
            )
            entry = None
            for elapsed in ENTRY_EVAL_SECONDS:
                secs = SECONDS_PER_WINDOW - elapsed
                up_ask, down_ask = market_asks(path[0], path[elapsed], secs, rng)
                signal = evaluate(
                    window, path[elapsed], secs, up_ask, down_ask,
                    volatility=rolling_volatility(path, elapsed), model="A",
                )
                if signal is not None:
                    entry = (elapsed, signal.direction.value, signal.market_price)
                    break
            if entry is None:
                continue
            elapsed, direction, fill = entry
            for name, cfg in configs.items():
                sim_rng = random.Random(seed * 77 + elapsed)
                totals[name].append(emulate_hold(path, elapsed, direction, fill, stake, cfg, sim_rng))
    print(f"{'config':<10} {'trades':>7} {'avg_pnl':>9} {'total':>10}")
    for name, pnls in totals.items():
        avg = sum(pnls) / len(pnls) if pnls else 0.0
        print(f"{name:<10} {len(pnls):>7d} {avg:>+9.3f} {sum(pnls):>+10.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--stake", type=float, default=5.0)
    parser.add_argument("--tp1", default="0.06")
    parser.add_argument("--sl", default="0.10")
    parser.add_argument("--texit", default="15")
    parser.add_argument("--trail", default="0.0")
    args = parser.parse_args()
    run_sweep(
        args.hours, args.sessions, args.seed, args.stake,
        tp1_list=_floats(args.tp1),
        sl_list=_floats(args.sl),
        texit_list=[int(x) for x in args.texit.split(",") if x],
        trail_list=_floats(args.trail),
    )


if __name__ == "__main__":
    main()
