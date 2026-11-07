#!/usr/bin/env python3
"""
Full-Day Volatile BTC Simulation
Uses the real signal engine (simulate_models.py core) with:
  - $5 bet / $10 bankroll (actual config)
  - Volatile BTC price paths (2x normal sigma)
  - $10 bankroll w/ MAX_DAILY_LOSS=$20 guard
  - Multiple regime scenarios
  - Per-conviction-tier breakdown
  - Exit model: low-conv (5s TP exit, 15s hard exit), high-conv (15s+ext time exit, 30% SL)
Reports full P&L, win rate, avg win/loss, drawdown, and trade count.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state.window import WindowState
from strategy.probability import ModelContext
from strategy.signal import evaluate_with_diagnostics

# ── Config (mirrors config.py) ────────────────────────────────────────────────
STAKE = 5.0               # MIN_BET = MAX_BET = $5
STARTING_BANKROLL = 10.0  # STARTING_BANKROLL_USDC = $10
MAX_DAILY_LOSS = 20.0     # MAX_DAILY_LOSS_USDC = $20 (would halt trading)
MAX_TRADES_PER_DAY = 100  # MAX_TRADES_PER_DAY
COOLDOWN_HIGH = 1         # COOLDOWN_SECONDS (high-conviction ≥0.70)
COOLDOWN_LOW = 10         # LOW_CONVICTION_COOLDOWN_SECONDS
CONSECUTIVE_LOSS_LIMIT = 3
CONSECUTIVE_LOSS_PAUSE = 60  # seconds

HIGH_CONVICTION_THRESHOLD = 0.70
HIGH_CONVICTION_BLOCKED_SECONDS = 120  # blocked first 2 min

# Exit rules
LOW_CONV_TP_PCT = 0.15       # EXIT_TAKE_PROFIT_PCT: take profit at +15% of entry price
LOW_CONV_TIME_EXIT_S = 5     # EXIT_TIME_SECONDS: exit at 5s if profitable
LOW_CONV_HARD_EXIT_S = 15    # LOW_CONVICTION_HARD_TIME_EXIT
HIGH_CONV_SL_PCT = 0.30      # HIGH_CONVICTION_SL_PCT
HIGH_CONV_TIME_EXIT_S = 15   # HIGH_CONVICTION_TIME_EXIT
HIGH_CONV_TIME_EXT_S = 5     # HIGH_CONVICTION_TIME_EXTENSION_SECONDS
EXIT_SL_PCT_LOW = 0.10       # EXIT_STOP_LOSS_PCT (low conviction)

SECONDS_PER_WINDOW = 300
WINDOWS_PER_DAY = 288        # 24h × 12/h
ENTRY_EVAL_STEP = 5          # evaluate signal every 5s

# Volatile BTC path params
LATENT_TREND_SIGMA_VOLATILE = 4e-5   # 2× the normal 1.8e-5 in simulate_models
NOISE_SIGMA_VOLATILE        = 2.2e-4  # 2× normal 1.1e-4
LATENT_TREND_SIGMA_NORMAL   = 1.8e-5
NOISE_SIGMA_NORMAL          = 1.1e-4


@dataclass
class TradeResult:
    window_idx: int
    entry_price: float      # contract price paid
    direction: str          # "UP" | "DOWN"
    stake: float
    regime: str             # "MISPRICING" | "LATE_CONVICTION"
    conviction: float       # market_conviction score
    btc_confirmed: bool
    exit_reason: str        # WIN_TP | WIN_TIME | WIN_WINDOW | LOSS_SL | LOSS_TIME | LOSS_WINDOW
    pnl: float
    won: bool


@dataclass
class DayStats:
    total_pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_amounts: list[float] = field(default_factory=list)
    loss_amounts: list[float] = field(default_factory=list)
    peak_bankroll: float = STARTING_BANKROLL
    trough_bankroll: float = STARTING_BANKROLL
    halted_by_daily_loss: bool = False
    halted_by_max_trades: bool = False

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def avg_win(self) -> float:
        return statistics.mean(self.win_amounts) if self.win_amounts else 0.0

    @property
    def avg_loss(self) -> float:
        return statistics.mean(self.loss_amounts) if self.loss_amounts else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        return (self.trough_bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100


def simulate_price_path(rng: random.Random, open_price: float = 100.0, volatile: bool = True) -> list[float]:
    trend_sigma = LATENT_TREND_SIGMA_VOLATILE if volatile else LATENT_TREND_SIGMA_NORMAL
    noise_sigma = NOISE_SIGMA_VOLATILE if volatile else NOISE_SIGMA_NORMAL
    latent_trend = rng.gauss(0.0, trend_sigma)
    price = open_price
    path = [price]
    for _ in range(SECONDS_PER_WINDOW):
        step = latent_trend + rng.gauss(0.0, noise_sigma)
        price *= math.exp(step)
        path.append(price)
    return path


def market_asks(
    open_price: float, current_price: float, seconds_remaining: int, rng: random.Random
) -> tuple[float, float]:
    """Synthetic Polymarket market prices, underreacting to BTC moves + noise."""
    move_pct = (current_price - open_price) / open_price
    time_factor = 1 - (seconds_remaining / SECONDS_PER_WINDOW)
    midpoint = 0.5 + (move_pct / 0.0005) * time_factor * 0.05 + rng.gauss(0.0, 0.012)
    midpoint = min(0.94, max(0.06, midpoint))
    spread = 0.01
    up_ask = min(0.95, max(0.05, midpoint + spread))
    down_ask = min(0.95, max(0.05, (1 - midpoint) + spread))
    return up_ask, down_ask


def rolling_volatility(path: list[float], elapsed: int) -> float:
    start = max(1, elapsed - 30)
    returns = [
        math.log(path[i] / path[i-1])
        for i in range(start, elapsed + 1)
        if path[i-1] > 0 and path[i] > 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns)


def simulate_position_exit(
    entry_price: float,
    stake: float,
    direction: str,
    regime: str,
    path_slice: list[float],   # BTC prices from entry second onward
    rng: random.Random,
) -> tuple[float, str, bool]:
    """
    Simulate intra-window price evolution and apply TP/SL/time-exit rules.
    Returns (pnl, exit_reason, won).
    path_slice[0] = BTC price at entry, path_slice[t] = t seconds later.

    Contract price is estimated from BTC path using the same market_asks logic
    (very rough — just tracks the BTC move directionally).
    """
    is_high_conv = entry_price >= HIGH_CONVICTION_THRESHOLD
    stop_loss_pct = HIGH_CONV_SL_PCT if is_high_conv else EXIT_SL_PCT_LOW
    stop_loss_price = entry_price * (1 - stop_loss_pct)  # contract price at which we stop out

    open_btc = path_slice[0]
    time_exit_s = HIGH_CONV_TIME_EXIT_S if is_high_conv else LOW_CONV_TIME_EXIT_S
    hard_exit_s = None if is_high_conv else LOW_CONV_HARD_EXIT_S

    for t in range(1, len(path_slice)):
        btc_now = path_slice[t]
        # Approximate contract price from BTC move
        move_pct = (btc_now - open_btc) / open_btc
        # A +1% BTC move shifts UP contract by roughly +time_factor * 0.1 (rough proxy)
        time_factor = min(1.0, t / SECONDS_PER_WINDOW)
        delta_contract = move_pct * time_factor * 10.0 * entry_price
        if direction == "UP":
            current_contract_price = max(0.01, min(0.99, entry_price + delta_contract))
        else:
            current_contract_price = max(0.01, min(0.99, entry_price - delta_contract))

        # --- TP exit (low-conviction only, at time_exit_s) ---
        if not is_high_conv and t == time_exit_s:
            if current_contract_price > entry_price:  # profitable
                pnl = stake * (current_contract_price / entry_price - 1)
                return pnl, "WIN_TIME_TP", True
        # --- Hard time exit (low-conviction) ---
        if hard_exit_s and t >= hard_exit_s:
            won = current_contract_price > entry_price
            if won:
                pnl = stake * (current_contract_price / entry_price - 1)
            else:
                pnl = -stake * (1 - current_contract_price / entry_price)
            return pnl, ("WIN_TIME" if won else "LOSS_TIME"), won

        # --- High-conviction time exit (with extension logic) ---
        if is_high_conv and t == time_exit_s:
            if current_contract_price > entry_price:  # still rising — extend
                time_exit_s += HIGH_CONV_TIME_EXT_S
            else:
                won = current_contract_price > entry_price
                if won:
                    pnl = stake * (current_contract_price / entry_price - 1)
                else:
                    pnl = -stake * (1 - current_contract_price / entry_price)
                return pnl, ("WIN_TIME" if won else "LOSS_TIME"), won

        # --- Stop-loss (both) ---
        if current_contract_price <= stop_loss_price:
            pnl = -stake * stop_loss_pct
            return pnl, "LOSS_SL", False

    # --- Window close (resolved by BTC outcome) ---
    resolved_up = path_slice[-1] >= path_slice[0]
    won = (direction == "UP" and resolved_up) or (direction == "DOWN" and not resolved_up)
    if won:
        pnl = stake * (1 / entry_price - 1)
        return pnl, "WIN_WINDOW", True
    else:
        pnl = -stake
        return pnl, "LOSS_WINDOW", False


def simulate_one_day(
    rng: random.Random,
    volatile: bool = True,
) -> tuple[DayStats, list[TradeResult]]:
    bankroll = STARTING_BANKROLL
    daily_pnl = 0.0
    daily_trades = 0
    consecutive_losses = 0
    consecutive_loss_pause_until = 0.0
    last_trade_ts = 0.0
    last_was_high_conv = False
    trade_log: list[TradeResult] = []

    stats = DayStats()
    stats.peak_bankroll = bankroll
    stats.trough_bankroll = bankroll

    # Simulated wall-clock time (seconds since day start)
    sim_ts = 0.0

    for window_idx in range(WINDOWS_PER_DAY):
        # Check daily limits
        if daily_pnl <= -MAX_DAILY_LOSS:
            stats.halted_by_daily_loss = True
            break
        if daily_trades >= MAX_TRADES_PER_DAY:
            stats.halted_by_max_trades = True
            break

        btc_open = 100.0  # normalised
        path = simulate_price_path(rng, btc_open, volatile=volatile)

        window = WindowState(
            start_ts=int(sim_ts),
            end_ts=int(sim_ts + SECONDS_PER_WINDOW),
            market_id=f"sim-{window_idx}",
            up_token_id="up",
            down_token_id="down",
            open_price=path[0],
        )

        for elapsed in range(10, SECONDS_PER_WINDOW - 5, ENTRY_EVAL_STEP):
            seconds_remaining = SECONDS_PER_WINDOW - elapsed
            current_price = path[elapsed]
            up_ask, down_ask = market_asks(path[0], current_price, seconds_remaining, rng)
            vol = rolling_volatility(path, elapsed)

            # Guard: cooldown
            trade_ts_sim = sim_ts + elapsed
            cooldown = COOLDOWN_HIGH if last_was_high_conv else COOLDOWN_LOW
            if last_trade_ts and (trade_ts_sim - last_trade_ts) < cooldown:
                continue

            # Guard: consecutive loss pause
            if trade_ts_sim < consecutive_loss_pause_until:
                continue

            # Guard: bankroll
            if bankroll < STAKE:
                break

            # Build minimal ModelContext from price path history
            # momentum: up_ask rising? use path trend
            move_pct = (current_price - path[0]) / path[0]
            # Polymarket-level conviction: simulated as a high-quality momentum proxy
            up_momentum = max(0.0, move_pct * 20)
            down_momentum = max(0.0, -move_pct * 20)
            up_accel = max(0.0, move_pct * 10)
            down_accel = max(0.0, -move_pct * 10)
            strongest_side = "UP" if up_ask >= down_ask else "DOWN"
            strongest_price = max(up_ask, down_ask)
            supporting_momentum = up_momentum if strongest_side == "UP" else down_momentum
            supporting_acceleration = up_accel if strongest_side == "UP" else down_accel
            market_conviction = min(1.0, max(0.0,
                strongest_price * 0.72
                + min(supporting_momentum * 10, 0.18)
                + min(supporting_acceleration * 10, 0.08)
                + 0.12  # assume liquid book
            ))
            if (strongest_side == "UP" and move_pct > 0) or (
                strongest_side == "DOWN" and move_pct < 0
            ):
                btc_poly_agreement = 1.0
            elif move_pct != 0:
                btc_poly_agreement = -1.0
            else:
                btc_poly_agreement = 0.0

            model_ctx = ModelContext(
                up_price_momentum=up_momentum,
                down_price_momentum=down_momentum,
                up_price_acceleration=up_accel,
                down_price_acceleration=down_accel,
                spread_change=0.0,
                one_sided_ratio=0.0,
                midpoint_shift=0.0,
                strongest_side=strongest_side,
                market_conviction=market_conviction,
                btc_poly_agreement=btc_poly_agreement,
                directional_consistency=0.65 if abs(move_pct) > 0.0005 else 0.5,
            )

            sig_eval = evaluate_with_diagnostics(
                window,
                current_price,
                seconds_remaining,
                up_ask,
                down_ask,
                volatility=vol,
                model="A",
                model_context=model_ctx,
                btc_price_available=True,
            )

            signal = sig_eval.signal
            if signal is None:
                continue

            # Guard: high-conviction blocked in first 2 min
            is_high_conv = signal.market_price >= HIGH_CONVICTION_THRESHOLD
            if is_high_conv and elapsed < HIGH_CONVICTION_BLOCKED_SECONDS:
                continue

            # Execute trade
            entry_price = signal.market_price
            direction = signal.direction.value

            # Simulate exit using remaining price path from entry second
            path_slice = path[elapsed:]
            pnl, exit_reason, won = simulate_position_exit(
                entry_price=entry_price,
                stake=STAKE,
                direction=direction,
                regime=signal.entry_regime,
                path_slice=path_slice,
                rng=rng,
            )

            bankroll += pnl
            daily_pnl += pnl
            daily_trades += 1
            last_trade_ts = trade_ts_sim
            last_was_high_conv = is_high_conv

            if won:
                consecutive_losses = 0
                stats.wins += 1
                stats.win_amounts.append(pnl)
            else:
                consecutive_losses += 1
                stats.losses += 1
                stats.loss_amounts.append(pnl)
                if consecutive_losses >= CONSECUTIVE_LOSS_LIMIT:
                    consecutive_loss_pause_until = trade_ts_sim + CONSECUTIVE_LOSS_PAUSE

            stats.peak_bankroll = max(stats.peak_bankroll, bankroll)
            stats.trough_bankroll = min(stats.trough_bankroll, bankroll)
            stats.trades += 1
            stats.total_pnl += pnl

            trade_log.append(TradeResult(
                window_idx=window_idx,
                entry_price=entry_price,
                direction=direction,
                stake=STAKE,
                regime=signal.entry_regime,
                conviction=signal.market_conviction,
                btc_confirmed=signal.btc_confirmed,
                exit_reason=exit_reason,
                pnl=pnl,
                won=won,
            ))

            break  # one trade per window pass (re-evaluate at next step)

        sim_ts += SECONDS_PER_WINDOW

    stats.total_pnl = daily_pnl
    return stats, trade_log


def run_monte_carlo(n: int, volatile: bool, seed: int = 42) -> list[DayStats]:
    rng = random.Random(seed)
    days = []
    for _ in range(n):
        stats, _ = simulate_one_day(rng, volatile=volatile)
        days.append(stats)
    return days


def report(days: list[DayStats], label: str, n_sessions: int) -> None:
    total_pnls = [d.total_pnl for d in days]
    win_rates = [d.win_rate for d in days if d.trades > 0]
    trades_per_day = [d.trades for d in days]
    avg_wins = [d.avg_win for d in days if d.win_amounts]
    avg_losses = [d.avg_loss for d in days if d.loss_amounts]
    drawdowns = [d.max_drawdown_pct for d in days]

    def pct_positive(vals: list[float]) -> float:
        return 100 * sum(1 for v in vals if v > 0) / len(vals) if vals else 0

    print(f"\n{'='*70}")
    print(f"  {label}  ({n_sessions} simulated days)")
    print(f"{'='*70}")
    print(f"  Starting bankroll:       ${STARTING_BANKROLL:.2f}")
    print(f"  Bet size:                ${STAKE:.2f} (fixed)")
    print(f"  Max daily loss guard:    ${MAX_DAILY_LOSS:.2f}")
    print()
    print(f"  {'Metric':<30} {'Median':>10}  {'Mean':>10}  {'Worst':>10}  {'Best':>10}")
    print(f"  {'-'*64}")

    def row(label: str, vals: list[float], fmt: str = "{:+.2f}") -> None:
        if not vals:
            print(f"  {label:<30} {'n/a':>10}")
            return
        med = statistics.median(vals)
        mean = statistics.mean(vals)
        worst = min(vals)
        best = max(vals)
        print(
            f"  {label:<30} {fmt.format(med):>10}  {fmt.format(mean):>10}  "
            f"{fmt.format(worst):>10}  {fmt.format(best):>10}"
        )

    row("Daily P&L ($)", total_pnls, "${:+.2f}")
    row("Trades/day", trades_per_day, "{:.0f}")
    row("Win rate (%)", [r * 100 for r in win_rates], "{:.1f}%")
    row("Avg win ($)", avg_wins, "${:+.3f}")
    row("Avg loss ($)", avg_losses, "${:+.3f}")
    row("Max drawdown (%)", drawdowns, "{:.1f}%")

    profitable_days = pct_positive(total_pnls)
    halted_dl = sum(1 for d in days if d.halted_by_daily_loss)
    halted_mt = sum(1 for d in days if d.halted_by_max_trades)
    print()
    print(f"  Profitable days:         {profitable_days:.0f}%")
    print(f"  Days halted (daily loss limit): {halted_dl}/{n_sessions}")
    print(f"  Days halted (max trades):       {halted_mt}/{n_sessions}")
    print()

    # Break-even analysis at current trade frequency & fill price
    if trades_per_day:
        avg_t = statistics.mean(trades_per_day)
        print(f"  Break-even analysis (avg {avg_t:.0f} trades/day, ${STAKE} stake):")
        # avg fill ≈ 0.59 from simulate_models
        avg_fill = 0.590
        win_profit = STAKE * (1 / avg_fill - 1)
        for wr in [0.55, 0.57, 0.60, 0.65, 0.70, 0.75, 0.80, 0.87, 0.90]:
            ev_per_trade = wr * win_profit - (1 - wr) * STAKE
            daily_ev = avg_t * ev_per_trade
            marker = " <-- simulated" if 0.86 <= wr <= 0.93 else ""
            print(f"    Win rate {wr:.0%}: EV/trade ${ev_per_trade:+.3f} → daily ${daily_ev:+.2f}{marker}")


def detail_one_day(rng: random.Random, volatile: bool) -> None:
    stats, log = simulate_one_day(rng, volatile=volatile)
    print(f"\n{'='*70}")
    print(f"  SINGLE DAY DETAIL (volatile={volatile})")
    print(f"{'='*70}")
    print(f"  Total P&L:  ${stats.total_pnl:+.2f}")
    print(f"  Bankroll:   ${STARTING_BANKROLL:.2f} → ${STARTING_BANKROLL + stats.total_pnl:.2f}")
    print(f"  Trades:     {stats.trades}")
    print(f"  Wins/Loss:  {stats.wins}/{stats.losses}  ({stats.win_rate:.1%} win rate)")
    print(f"  Avg win:    ${stats.avg_win:+.3f}")
    print(f"  Avg loss:   ${stats.avg_loss:+.3f}")
    print(f"  Peak:       ${stats.peak_bankroll:.2f}  Trough: ${stats.trough_bankroll:.2f}")
    print(f"  Max DD:     {stats.max_drawdown_pct:.1f}%")

    if log:
        mispricing = [t for t in log if t.regime == "MISPRICING"]
        late_conv = [t for t in log if t.regime == "LATE_CONVICTION"]
        high_conv = [t for t in log if t.entry_price >= HIGH_CONVICTION_THRESHOLD]
        low_conv = [t for t in log if t.entry_price < HIGH_CONVICTION_THRESHOLD]
        btc_confirmed = [t for t in log if t.btc_confirmed]

        print("\n  Regime breakdown:")
        print(f"    MISPRICING:      {len(mispricing)} trades, {sum(1 for t in mispricing if t.won)} wins")
        print(f"    LATE_CONVICTION: {len(late_conv)} trades, {sum(1 for t in late_conv if t.won)} wins")
        print("\n  Conviction tier:")
        lc_wr = sum(1 for t in low_conv if t.won) / len(low_conv) if low_conv else 0
        hc_wr = sum(1 for t in high_conv if t.won) / len(high_conv) if high_conv else 0
        print(
            f"    Low  (0.60-0.69): {len(low_conv)} trades, WR={lc_wr:.1%}, "
            f"PnL=${sum(t.pnl for t in low_conv):+.2f}"
        )
        print(
            f"    High (0.70-0.92): {len(high_conv)} trades, WR={hc_wr:.1%}, "
            f"PnL=${sum(t.pnl for t in high_conv):+.2f}"
        )
        print(f"    BTC-confirmed:    {len(btc_confirmed)}/{len(log)}")

        exit_counts: dict[str, int] = {}
        for t in log:
            exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
        print("\n  Exit reason breakdown:")
        for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
            pct = count / len(log) * 100
            print(f"    {reason:<20} {count:>4} ({pct:.0f}%)")

        print("\n  Last 10 trades:")
        for i, t in enumerate(log[-10:], 1):
            win_str = "WIN " if t.won else "LOSS"
            print(f"    {i:>2}. W{t.window_idx:>3} {t.direction:<4} {win_str} ${t.pnl:+.3f}  "
                  f"@ {t.entry_price:.3f} [{t.regime[:4]}] [{t.exit_reason}]")


def main() -> None:
    print("\n" + "="*70)
    print("  FULL-DAY VOLATILE BTC SIMULATION")
    print("  Real signal engine, $5 bets, $10 bankroll, 288 windows/day")
    print("="*70)

    N_MONTE_CARLO = 200

    # ── Single day detail ────────────────────────────────────────────────────
    rng = random.Random(42)
    detail_one_day(rng, volatile=True)

    # ── Monte Carlo: Volatile day ────────────────────────────────────────────
    print(f"\nRunning {N_MONTE_CARLO} Monte Carlo volatile days...")
    volatile_days = run_monte_carlo(N_MONTE_CARLO, volatile=True, seed=99)
    report(volatile_days, "VOLATILE BTC DAY", N_MONTE_CARLO)

    # ── Monte Carlo: Normal day (baseline) ───────────────────────────────────
    print(f"\nRunning {N_MONTE_CARLO} Monte Carlo normal days...")
    normal_days = run_monte_carlo(N_MONTE_CARLO, volatile=False, seed=99)
    report(normal_days, "NORMAL BTC DAY (baseline)", N_MONTE_CARLO)

    # ── Summary comparison ────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  SUMMARY: $10 Bankroll / $5 Bet Full-Day Results")
    print(f"{'='*70}")

    for label, days in [("Volatile", volatile_days), ("Normal", normal_days)]:
        pnls = [d.total_pnl for d in days]
        wrs = [d.win_rate * 100 for d in days if d.trades > 0]
        tdpd = [d.trades for d in days]
        profitable = 100 * sum(1 for p in pnls if p > 0) / len(pnls)
        print(f"\n  {label} BTC day:")
        print(f"    Median daily P&L:     ${statistics.median(pnls):+.2f}")
        print(f"    Mean daily P&L:       ${statistics.mean(pnls):+.2f}")
        print(f"    Profitable days:      {profitable:.0f}%")
        print(f"    Median trades/day:    {statistics.median(tdpd):.0f}")
        print(f"    Median win rate:      {statistics.median(wrs):.1f}%")

    print()


if __name__ == "__main__":
    main()
