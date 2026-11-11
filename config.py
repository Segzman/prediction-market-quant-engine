from __future__ import annotations

import logging
import os
from typing import Final

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as e:
        raise ValueError(f"Invalid float value for {name}: {value!r}") from e


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"Invalid int value for {name}: {value!r}") from e


# Required
POLYMARKET_PRIVATE_KEY: Final[str] = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_API_KEY: Final[str] = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET: Final[str] = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE: Final[str] = os.getenv("POLYMARKET_PASSPHRASE", "")
POLYMARKET_FUNDER: Final[str] = os.getenv("POLYMARKET_FUNDER", "")
POLYMARKET_SIGNATURE_TYPE: Final[int] = _get_int("POLYMARKET_SIGNATURE_TYPE", 0)

# Optional
DRY_RUN: Final[bool] = _get_bool("DRY_RUN", True)
LOG_LEVEL: Final[str] = os.getenv("LOG_LEVEL", "INFO").upper()
# Paths
DATA_DIR: Final[str] = os.path.abspath(os.getenv("DATA_DIR", "data"))
LEDGER_FILE: Final[str] = os.getenv("LEDGER_FILE", "trades.jsonl")

# Model track configuration
ACTIVE_MODEL_TRACK: Final[str] = os.getenv("ACTIVE_MODEL_TRACK", "CONVICTION_ONLY").upper()
SHADOW_MULTI_TRACK_EXECUTION: Final[bool] = _get_bool("SHADOW_MULTI_TRACK_EXECUTION", True)
LEDGER_FILE_CONVICTION: Final[str] = os.getenv("LEDGER_FILE_CONVICTION", "trades_conviction.jsonl")
LEDGER_FILE_MISPRICING: Final[str] = os.getenv("LEDGER_FILE_MISPRICING", "trades_mispricing.jsonl")
LEDGER_FILE_BOTH: Final[str] = os.getenv("LEDGER_FILE_BOTH", "trades_both.jsonl")

BOT_LOCK_FILE: Final[str] = os.getenv("BOT_LOCK_FILE", ".bot.lock")
ALERTS_FILE: Final[str] = os.getenv("ALERTS_FILE", "alerts.jsonl")
ALERT_WEBHOOK_URL: Final[str] = os.getenv("ALERT_WEBHOOK_URL", "")
ALERT_ORDER_FAILURE_THRESHOLD: Final[int] = _get_int("ALERT_ORDER_FAILURE_THRESHOLD", 3)
ALERT_ORDER_FAILURE_WINDOW_SECONDS: Final[float] = _get_float(
    "ALERT_ORDER_FAILURE_WINDOW_SECONDS", 120.0
)
ALERT_RISK_BREACH_THROTTLE_SECONDS: Final[float] = _get_float(
    "ALERT_RISK_BREACH_THROTTLE_SECONDS", 120.0
)

# Signal thresholds
MOMENTUM_THRESHOLD: Final[float] = _get_float("MOMENTUM_THRESHOLD", 0.0005)
EDGE_THRESHOLD: Final[float] = _get_float("EDGE_THRESHOLD", 0.06)
# DISABLED: Don't force low-quality trades
FORCE_INTERVAL_TRADES: Final[bool] = _get_bool("FORCE_INTERVAL_TRADES", False)
HEDGE_ENABLED: Final[bool] = _get_bool("HEDGE_ENABLED", True)
HEDGE_FRACTION: Final[float] = _get_float("HEDGE_FRACTION", 0.50)
HEDGE_BANKROLL_RESERVE_FRACTION: Final[float] = _get_float(
    "HEDGE_BANKROLL_RESERVE_FRACTION", 0.10
)
LIVE_FORCE_INTERVAL_TRADES: Final[bool] = _get_bool(
    "LIVE_FORCE_INTERVAL_TRADES", False
)
LIVE_HEDGE_ENABLED: Final[bool] = _get_bool("LIVE_HEDGE_ENABLED", True)  # Enable hedging in live
LIVE_DRY_RUN: Final[bool] = _get_bool("LIVE_DRY_RUN", False)  # Run live mode without actual trades

HEDGE_USE_LOSS_CAP: Final[bool] = _get_bool("HEDGE_USE_LOSS_CAP", True)
HEDGE_MAX_LOSS_PCT: Final[float] = _get_float("HEDGE_MAX_LOSS_PCT", 0.02)

# Exit targets (early exits for profit/loss management)
EXIT_TAKE_PROFIT_PCT: Final[float] = _get_float("EXIT_TAKE_PROFIT_PCT", 0.15)  # 15% gain = exit (trade 1)
# Tiered TP1: bank part early at +6%, runner to TP2
# Fee-aware sweep: 0.06 beats 0.08/0.10 on both seeds.
EXIT_TP1_PCT: Final[float] = _get_float("EXIT_TP1_PCT", 0.06)
# Fraction of size banked at TP1
EXIT_TP1_FRACTION: Final[float] = _get_float("EXIT_TP1_FRACTION", 0.5)
# Trailing ratchet: SL follows highest price down to -6%; 0 = disabled
# Sim shows trails chop winners; enable only on live evidence.
EXIT_TRAIL_PCT: Final[float] = _get_float("EXIT_TRAIL_PCT", 0.0)
# Tracks that skip every early exit (TP/SL/break-even/time) and hold to settlement.
# Paper A/B test: live logs showed early exits cutting trades that won at settlement.
HOLD_TO_SETTLEMENT_TRACKS: Final[frozenset[str]] = frozenset(
    t.strip().upper() for t in os.getenv("HOLD_TO_SETTLEMENT_TRACKS", "").split(",") if t.strip()
)
# Exit immediately if price drops >8% in the velocity window
MOMENTUM_SL_VELOCITY_PCT: Final[float] = _get_float("MOMENTUM_SL_VELOCITY_PCT", 0.08)
# Seconds to measure velocity over
MOMENTUM_SL_VELOCITY_WINDOW: Final[float] = _get_float("MOMENTUM_SL_VELOCITY_WINDOW", 3.0)
# Aggressive TP when >100s remaining, grab profit before reversal
EARLY_WINDOW_TP_PCT: Final[float] = _get_float("EARLY_WINDOW_TP_PCT", 0.08)
# Patient TP when <100s remaining, hold for bigger gain as the window closes soon
LATE_WINDOW_TP_PCT: Final[float] = _get_float("LATE_WINDOW_TP_PCT", 0.20)
# Switch from aggressive to patient mode below this seconds_remaining
LATE_WINDOW_THRESHOLD_SECONDS: Final[int] = _get_int("LATE_WINDOW_THRESHOLD_SECONDS", 100)
# 12% loss = exit (joint entry+exit sweep, seeds 7+8)
EXIT_STOP_LOSS_PCT: Final[float] = _get_float("EXIT_STOP_LOSS_PCT", 0.12)
HIGH_CONVICTION_ENTRY_THRESHOLD: Final[float] = _get_float("HIGH_CONVICTION_ENTRY_THRESHOLD", 0.70)
# Exit if conviction drops below this after entry
CONVICTION_DECAY_FLOOR: Final[float] = _get_float("CONVICTION_DECAY_FLOOR", 0.60)
# Must stay below floor for this long before exiting
CONVICTION_DECAY_SECONDS: Final[float] = _get_float("CONVICTION_DECAY_SECONDS", 5.0)
# Block high-conviction entries until this many seconds into the window
# (avoid early pump fakes)
HIGH_CONVICTION_MIN_ELAPSED_SECONDS: Final[int] = _get_int(
    "HIGH_CONVICTION_MIN_ELAPSED_SECONDS", 50
)
# 12% SL, catches slow grinds (velocity SL handles fast crashes)
HIGH_CONVICTION_SL_PCT: Final[float] = _get_float("HIGH_CONVICTION_SL_PCT", 0.12)
# Base time exit for high-conviction entries (joint sweep: 20s beats 15s)
HIGH_CONVICTION_TIME_EXIT: Final[int] = _get_int("HIGH_CONVICTION_TIME_EXIT", 20)
# Extend hold by this many seconds each time price is still rising at time exit
HIGH_CONVICTION_TIME_EXTENSION_SECONDS: Final[int] = _get_int(
    "HIGH_CONVICTION_TIME_EXTENSION_SECONDS", 5
)
# Low-conviction: exit at 5s if profitable (green)
EXIT_TIME_SECONDS: Final[int] = _get_int("EXIT_TIME_SECONDS", 5)
# Low-conviction: hard exit at 15s regardless of P&L
LOW_CONVICTION_HARD_TIME_EXIT: Final[int] = _get_int("LOW_CONVICTION_HARD_TIME_EXIT", 15)
# Exit if signal reverses
EXIT_REVERSE_SIGNAL: Final[bool] = _get_bool("EXIT_REVERSE_SIGNAL", True)
# SL must be breached for N seconds before exit (borderline breaches only)
# Clears if price recovers.
SL_CONFIRMATION_SECONDS: Final[float] = _get_float("SL_CONFIRMATION_SECONDS", 2.0)
# If price is this far BELOW stop_loss_price (as fraction of SL price),
# skip confirmation and exit immediately.
SL_INSTANT_BREACH_PCT: Final[float] = _get_float("SL_INSTANT_BREACH_PCT", 0.015)
# Don't exit (TP/SL/TIME) before this many seconds; hard-crash SL always fires
MIN_HOLD_SECONDS: Final[float] = _get_float("MIN_HOLD_SECONDS", 0.0)
# Momentum-aware SL suppression: extends the confirmation window when momentum strongly supports the trade
MOMENTUM_SL_SUPPRESS_ENABLED: Final[bool] = _get_bool("MOMENTUM_SL_SUPPRESS_ENABLED", True)
# Suppression score needed to extend window (Kaufman ER threshold)
MOMENTUM_SL_SUPPRESS_THRESHOLD: Final[float] = _get_float(
    "MOMENTUM_SL_SUPPRESS_THRESHOLD", 0.65
)
# Hard cap on suppression window (~0.5x 20s autocorrelation half-life from Cont et al.)
MOMENTUM_SL_MAX_WINDOW_SECONDS: Final[float] = _get_float(
    "MOMENTUM_SL_MAX_WINDOW_SECONDS", 10.0
)
HEDGE_COOLDOWN_SECONDS: Final[int] = _get_int("HEDGE_COOLDOWN_SECONDS", 60)

# Entry timing
ENTRY_WINDOW_START: Final[int] = _get_int("ENTRY_WINDOW_START", 295)
ENTRY_WINDOW_END: Final[int] = _get_int("ENTRY_WINDOW_END", 60)  # Don't enter with <60s remaining
SNIPE_START_SECONDS: Final[int] = _get_int("SNIPE_START_SECONDS", 10)
SNIPE_HARD_DEADLINE_SECONDS: Final[int] = _get_int("SNIPE_HARD_DEADLINE_SECONDS", 5)
SNIPE_POLL_SECONDS: Final[float] = _get_float("SNIPE_POLL_SECONDS", 2.0)
SNIPE_MIN_CONFIDENCE: Final[float] = _get_float("SNIPE_MIN_CONFIDENCE", 0.52)
SNIPE_SPIKE_CONFIDENCE_DELTA: Final[float] = _get_float(
    "SNIPE_SPIKE_CONFIDENCE_DELTA", 0.05
)
EARLY_ENTRY_START_SECONDS: Final[int] = _get_int(
    "EARLY_ENTRY_START_SECONDS", _get_int("SNIPE_EARLY_ENTRY_SECONDS", 295)
)
ENTRY_MIN_CONTRACT_PRICE: Final[float] = _get_float("ENTRY_MIN_CONTRACT_PRICE", 0.50)
ENTRY_MAX_CONTRACT_PRICE: Final[float] = _get_float("ENTRY_MAX_CONTRACT_PRICE", 0.62)
LIQUIDITY_MIN_ASK_COUNT: Final[int] = _get_int("LIQUIDITY_MIN_ASK_COUNT", 10)
# Lowered: at extreme prices (0.90+) the losing side naturally has few bids
LIQUIDITY_MIN_BID_COUNT: Final[int] = _get_int("LIQUIDITY_MIN_BID_COUNT", 3)
LIQUIDITY_MAX_SIDE_SPREAD: Final[float] = _get_float(
    "LIQUIDITY_MAX_SIDE_SPREAD", _get_float("LIQUIDITY_MAX_SPREAD", 0.08)
)
LIQUIDITY_MIN_SCORE: Final[float] = _get_float("LIQUIDITY_MIN_SCORE", 0.45)
EARLY_ENTRY_MIN_SCORE: Final[float] = _get_float("EARLY_ENTRY_MIN_SCORE", 0.70)
MIN_EXPECTED_PROFIT_USDC: Final[float] = _get_float(
    "MIN_EXPECTED_PROFIT_USDC", _get_float("MIN_NET_EXPECTED_PROFIT_USDC", 0.03)
)
EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC: Final[float] = _get_float(
    "EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC", 0.06
)
EXPECTED_SLIPPAGE_BPS: Final[float] = _get_float("EXPECTED_SLIPPAGE_BPS", 25.0)
LATE_CONVICTION_START_SECONDS: Final[int] = _get_int(
    "LATE_CONVICTION_START_SECONDS", 295
)
LATE_CONVICTION_MIN_CONTRACT_PRICE: Final[float] = _get_float(
    "LATE_CONVICTION_MIN_CONTRACT_PRICE", 0.79
)
LATE_CONVICTION_MAX_CONTRACT_PRICE: Final[float] = _get_float(
    "LATE_CONVICTION_MAX_CONTRACT_PRICE", 0.92
)
LATE_CONVICTION_EDGE_THRESHOLD: Final[float] = _get_float(
    "LATE_CONVICTION_EDGE_THRESHOLD", 0.07  # All losers in last run had edge <0.08
)
LATE_CONVICTION_MIN_POLY_SCORE: Final[float] = _get_float(
    "LATE_CONVICTION_MIN_POLY_SCORE", 0.75  # All losers had conviction <0.75
)
LATE_CONVICTION_LOW_PRICE_MIN_POLY_SCORE: Final[float] = _get_float(
    "LATE_CONVICTION_LOW_PRICE_MIN_POLY_SCORE",
    0.78,  # floor: threshold at window close for low-price entries (0.60-0.69)
)
# Conviction thresholds at window open — decay linearly to the floor as window nears close
# Prevents trigger-happy entries early in window when pump fakes are common
LATE_CONVICTION_OPEN_LOW_PRICE_MIN_POLY_SCORE: Final[float] = _get_float(
    "LATE_CONVICTION_OPEN_LOW_PRICE_MIN_POLY_SCORE", 0.90  # low-price (0.60–0.69) threshold at t=295s
)
LATE_CONVICTION_OPEN_MIN_POLY_SCORE: Final[float] = _get_float(
    "LATE_CONVICTION_OPEN_MIN_POLY_SCORE", 0.80  # high-price (0.70+) threshold at t=295s
)
LATE_CONVICTION_STRONG_POLY_SCORE: Final[float] = _get_float(
    "LATE_CONVICTION_STRONG_POLY_SCORE", 0.78
)
# Non-price conviction: sum of momentum + acceleration + market-health components (max 0.38)
# Requires real momentum behind the move, not just a high price sitting still
LATE_CONVICTION_MIN_MOMENTUM_CONVICTION: Final[float] = _get_float(
    "LATE_CONVICTION_MIN_MOMENTUM_CONVICTION", 0.15
)
# Stricter gate for high-price entries (>= 0.79): stronger momentum + BTC must agree
LATE_CONVICTION_HIGH_PRICE_MOMENTUM_CONVICTION: Final[float] = _get_float(
    "LATE_CONVICTION_HIGH_PRICE_MOMENTUM_CONVICTION", 0.20
)
LATE_CONVICTION_HIGH_PRICE_THRESHOLD: Final[float] = _get_float(
    "LATE_CONVICTION_HIGH_PRICE_THRESHOLD", 0.79
)
LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC: Final[float] = _get_float(
    "LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC", 0.02
)
# Day-volatility move gate: late-conviction entries only when the BTC move is
# large relative to the trailing 24h median window move (V24) and the clock is
# late in the window (persistence 96.8% at elapsed 180-240s, 98.8% gated).
# Off by default; flip on for shadow validation (dry-run only).
DAY_VOL_GATE_ENABLED: Final[bool] = _get_bool("DAY_VOL_GATE_ENABLED", False)
DAY_VOL_LOOKBACK_WINDOWS: Final[int] = _get_int("DAY_VOL_LOOKBACK_WINDOWS", 288)
DAY_VOL_MIN_WINDOWS: Final[int] = _get_int("DAY_VOL_MIN_WINDOWS", 24)
DAY_VOL_MOVE_MULTIPLIER: Final[float] = _get_float("DAY_VOL_MOVE_MULTIPLIER", 1.5)
DAY_VOL_MIN_MOVE_BPS: Final[float] = _get_float("DAY_VOL_MIN_MOVE_BPS", 8.0)
DAY_VOL_ELAPSED_MIN_SECONDS: Final[int] = _get_int("DAY_VOL_ELAPSED_MIN_SECONDS", 180)
DAY_VOL_ELAPSED_MAX_SECONDS: Final[int] = _get_int("DAY_VOL_ELAPSED_MAX_SECONDS", 250)
DAY_VOL_MAX_STALE_SECONDS: Final[float] = _get_float("DAY_VOL_MAX_STALE_SECONDS", 360.0)
LATE_CONVICTION_SIZE_MULTIPLIER: Final[float] = _get_float(
    "LATE_CONVICTION_SIZE_MULTIPLIER", 0.70
)
# Jev (TypeSafe System One) pilot: BTC-Polymarket agreement judgment.
# Off by default. When on and TYPESAFE_API_KEY is set, high-price and
# contested late-conviction entries require Jev confirmation; any API
# failure falls back to the deterministic btc_poly_agreement check.
JEV_NOUL_ENABLED: Final[bool] = _get_bool("JEV_NOUL_ENABLED", False)
JEV_NOUL_MIN_PROBABILITY: Final[float] = _get_float(
    "JEV_NOUL_MIN_PROBABILITY", 0.60
)
JEV_API_TIMEOUT_SECONDS: Final[float] = _get_float(
    "JEV_API_TIMEOUT_SECONDS", 5.0
)
JEV_LOG_FILE: Final[str] = os.getenv("JEV_LOG_FILE", "jev_calls.jsonl")
JEV_COST_PER_CALL_USD: Final[float] = _get_float(
    "JEV_COST_PER_CALL_USD", 0.0
)
EARLY_CONVICTION_TRIGGER_PRICE: Final[float] = _get_float(
    "EARLY_CONVICTION_TRIGGER_PRICE", 0.60
)
EARLY_CONVICTION_MIN_SCORE: Final[float] = _get_float(
    "EARLY_CONVICTION_MIN_SCORE", 1.0
)
SNIPE_EARLY_ENTRY_SECONDS: Final[int] = EARLY_ENTRY_START_SECONDS
LIQUIDITY_MAX_SPREAD: Final[float] = LIQUIDITY_MAX_SIDE_SPREAD
MIN_NET_EXPECTED_PROFIT_USDC: Final[float] = MIN_EXPECTED_PROFIT_USDC

# Risk
MAX_BET_USDC: Final[float] = _get_float("MAX_BET_USDC", 5.0)
MIN_BET_USDC: Final[float] = _get_float("MIN_BET_USDC", 2.0)
MAX_DAILY_LOSS_USDC: Final[float] = _get_float("MAX_DAILY_LOSS_USDC", 20.0)
MAX_TRADES_PER_DAY: Final[int] = _get_int("MAX_TRADES_PER_DAY", 100)
# Max concurrent positions per window
MAX_POSITIONS_PER_WINDOW: Final[int] = _get_int("MAX_POSITIONS_PER_WINDOW", 3)
MAX_OPEN_ORDERS: Final[int] = _get_int("MAX_OPEN_ORDERS", MAX_POSITIONS_PER_WINDOW)
MAX_CONCURRENT_POSITIONS: Final[int] = _get_int(
    "MAX_CONCURRENT_POSITIONS", MAX_POSITIONS_PER_WINDOW
)
# Cooldown between entries (all entries are now 0.79+)
COOLDOWN_SECONDS: Final[int] = _get_int("COOLDOWN_SECONDS", 5)
# Unused: min entry is now 0.79, no low-conviction entries
LOW_CONVICTION_COOLDOWN_SECONDS: Final[int] = _get_int("LOW_CONVICTION_COOLDOWN_SECONDS", 10)
CONSECUTIVE_LOSS_PAUSE_SECONDS: Final[int] = _get_int(
    "CONSECUTIVE_LOSS_PAUSE_SECONDS", 60
)
CONSECUTIVE_LOSS_LIMIT: Final[int] = _get_int("CONSECUTIVE_LOSS_LIMIT", 3)

# Sizing
USE_KELLY: Final[bool] = _get_bool("USE_KELLY", False)
KELLY_FRACTION: Final[float] = _get_float("KELLY_FRACTION", 0.5)
STARTING_BANKROLL_USDC: Final[float] = _get_float("STARTING_BANKROLL_USDC", 10.0)
BANKROLL_RISK_FRACTION: Final[float] = _get_float("BANKROLL_RISK_FRACTION", 0.25)
WIN_STREAK_BET_STEP: Final[float] = _get_float("WIN_STREAK_BET_STEP", 0.08)
LOSS_STREAK_BET_STEP: Final[float] = _get_float("LOSS_STREAK_BET_STEP", 0.12)
MAX_UNRESOLVED_EXPOSURE_USDC: Final[float] = _get_float(
    "MAX_UNRESOLVED_EXPOSURE_USDC", STARTING_BANKROLL_USDC
)
MAX_WINDOW_EXPOSURE_USDC: Final[float] = _get_float(
    "MAX_WINDOW_EXPOSURE_USDC", MAX_BET_USDC * MAX_POSITIONS_PER_WINDOW
)
MAX_CANARY_DRAWDOWN_USDC: Final[float] = _get_float(
    "MAX_CANARY_DRAWDOWN_USDC", STARTING_BANKROLL_USDC
)
MIN_HEALTHY_PRICE_FEEDS: Final[int] = _get_int("MIN_HEALTHY_PRICE_FEEDS", 2)
MAX_PRICE_FEED_DIVERGENCE_BPS: Final[float] = _get_float(
    "MAX_PRICE_FEED_DIVERGENCE_BPS", 50.0
)
MODEL_C_CALIBRATION_FILE: Final[str] = os.getenv(
    "MODEL_C_CALIBRATION_FILE", "model_c_calibration.json"
)
MODEL_C_MIN_BUCKET_SAMPLES: Final[int] = _get_int("MODEL_C_MIN_BUCKET_SAMPLES", 10)
RUNTIME_STATUS_FILE: Final[str] = os.getenv("RUNTIME_STATUS_FILE", "runtime_status.json")
# Market tape for backtests/training: one row per status tick. Empty string disables it.
MARKET_TAPE_DIR: Final[str] = os.getenv(
    "MARKET_TAPE_DIR", os.path.join(os.path.dirname(os.path.abspath(RUNTIME_STATUS_FILE)), "tape")
)
RUNTIME_STATUS_UPDATE_SECONDS: Final[float] = _get_float(
    "RUNTIME_STATUS_UPDATE_SECONDS", 2.0
)
UNRESOLVED_SWEEP_INTERVAL_SECONDS: Final[float] = _get_float(
    "UNRESOLVED_SWEEP_INTERVAL_SECONDS", 15.0
)
WINDOW_DURATION_SECONDS: Final[int] = 300  # Fixed 5-minute windows (Polymarket)

# Feed
BINANCE_WS_URL: Final[str] = os.getenv(
    "BINANCE_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@trade"
)
PRICE_STALE_THRESHOLD_SECONDS: Final[int] = _get_int(
    "PRICE_STALE_THRESHOLD_SECONDS", 5
)
POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS: Final[int] = _get_int(
    "POLYMARKET_BOOK_STALE_THRESHOLD_SECONDS", 8
)
ALLOW_TRADING_WITHOUT_BTC_PRICE: Final[bool] = _get_bool(
    "ALLOW_TRADING_WITHOUT_BTC_PRICE", False  # Fail closed by default when BTC feed freshness is lost
)
VOLATILITY_MAX_SAMPLES: Final[int] = _get_int("VOLATILITY_MAX_SAMPLES", 120)
VOLATILITY_MIN_SAMPLES: Final[int] = _get_int("VOLATILITY_MIN_SAMPLES", 10)
POLYMARKET_HISTORY_MAX_SAMPLES: Final[int] = _get_int(
    "POLYMARKET_HISTORY_MAX_SAMPLES", 180
)
POLYMARKET_FEATURE_LOOKBACK_SECONDS: Final[float] = _get_float(
    "POLYMARKET_FEATURE_LOOKBACK_SECONDS", 30.0
)

# Polymarket
POLYMARKET_HOST: Final[str] = os.getenv(
    "POLYMARKET_HOST", "https://clob.polymarket.com"
)

# Missing constants needed by bot.py
DEFAULT_MAX_CORRECTION_AGE: Final[int] = _get_int("DEFAULT_MAX_CORRECTION_AGE", 300)
LIVE_ORDER_SLIPPAGE_BPS: Final[float] = _get_float("LIVE_ORDER_SLIPPAGE_BPS", 100.0)
LATE_CONVICTION_SLIPPAGE_BPS: Final[float] = _get_float(
    "LATE_CONVICTION_SLIPPAGE_BPS", 200.0  # Reduced from 500 for tighter entry
)
LIVE_ORDER_TYPE: Final[str] = os.getenv("LIVE_ORDER_TYPE", "GTC").upper()
LIVE_ALLOW_NEG_RISK: Final[bool] = _get_bool("LIVE_ALLOW_NEG_RISK", False)
LIVE_REQUIRE_EMPTY_OPEN_ORDERS: Final[bool] = _get_bool(
    "LIVE_REQUIRE_EMPTY_OPEN_ORDERS", True
)
LIVE_STARTUP_CANCEL_ALL: Final[bool] = _get_bool("LIVE_STARTUP_CANCEL_ALL", False)
LIVE_MIN_AVAILABLE_USDC: Final[float] = _get_float("LIVE_MIN_AVAILABLE_USDC", 1.0)
LIVE_BALANCE_BUFFER_USDC: Final[float] = _get_float("LIVE_BALANCE_BUFFER_USDC", 1.0)
LIVE_KILL_SWITCH_FILE: Final[str] = os.getenv("LIVE_KILL_SWITCH_FILE", "")
LIVE_RECON_LOOKBACK_SECONDS: Final[int] = _get_int("LIVE_RECON_LOOKBACK_SECONDS", 86400)
# Grace (seconds past a window's end) before a held ambiguous order intent with
# no matching live order is declared never-landed and aborted by inline recon.
INTENT_RECON_ABORT_GRACE_SECONDS: Final[int] = _get_int(
    "INTENT_RECON_ABORT_GRACE_SECONDS", 300
)
MARKET_CACHE_TTL_SECONDS: Final[int] = _get_int("MARKET_CACHE_TTL_SECONDS", 60)
TARGET_ASSET: Final[str] = os.getenv("TARGET_ASSET", "BTC").upper()
ASSET_SERIES_SLUGS: Final[dict[str, tuple[str, ...]]] = {
    "BTC": ("btc-up-or-down-5m",),
    "ETH": ("eth-up-or-down-5m",),
    "POL": ("pol-up-or-down-5m", "polygon-up-or-down-5m"),
    "SOL": ("sol-up-or-down-5m",),
    "XRP": ("xrp-up-or-down-5m",),
}
ASSET_SERIES_IDS: Final[dict[str, str]] = {
    "ETH": "10683",
    "BTC": "10684",
    "XRP": "10685",
    "SOL": "10686",
}
ASSET_MARKET_SLUG_PREFIXES: Final[dict[str, tuple[str, ...]]] = {
    "BTC": ("btc-updown-5m-", "bitcoin-updown-5m-"),
    "ETH": ("eth-updown-5m-", "ethereum-updown-5m-"),
    "POL": ("pol-updown-5m-", "polygon-updown-5m-"),
    "SOL": ("sol-updown-5m-", "solana-updown-5m-"),
    "XRP": ("xrp-updown-5m-",),
}
ASSET_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum"),
    "POL": ("pol", "polygon"),
    "SOL": ("sol", "solana"),
    "XRP": ("xrp", "ripple"),
}
BINANCE_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "POL": "polusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}
BINANCE_REST_SYMBOLS: Final[dict[str, str]] = {
    asset: stream.upper() for asset, stream in BINANCE_SYMBOLS.items()
}
KRAKEN_WS_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XRP": "XRP/USD",
}
KRAKEN_REST_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
}
GEMINI_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "btcusd",
    "ETH": "ethusd",
    "SOL": "solusd",
    "XRP": "xrpusd",
}
BYBIT_WS_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}
BYBIT_REST_SYMBOLS: Final[dict[str, str]] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}
# Price aggregation: "fastest" uses the freshest healthy feed (live
# re-ranks every tick); "median" uses the median of healthy feeds.
PRICE_SOURCE_SELECTION: Final[str] = os.getenv("PRICE_SOURCE_SELECTION", "fastest").lower()


def validate_config(
    *, live_override: bool | None = None, require_trading_credentials: bool = False
) -> None:
    live_mode = (not DRY_RUN) if live_override is None else live_override

    if MAX_BET_USDC <= 0:
        raise ValueError("MAX_BET_USDC must be > 0")
    if MIN_BET_USDC <= 0:
        raise ValueError("MIN_BET_USDC must be > 0")
    if MIN_BET_USDC > MAX_BET_USDC:
        raise ValueError("MIN_BET_USDC must be less than or equal to MAX_BET_USDC")
    if not 0 < EDGE_THRESHOLD < 1:
        raise ValueError("EDGE_THRESHOLD must be between 0 and 1")
    if not 0 <= HEDGE_FRACTION <= 1:
        raise ValueError("HEDGE_FRACTION must be between 0 and 1")
    if not 0 <= HEDGE_BANKROLL_RESERVE_FRACTION < 1:
        raise ValueError("HEDGE_BANKROLL_RESERVE_FRACTION must be between 0 and 1")
    if not 0 <= LIVE_ORDER_SLIPPAGE_BPS <= 5000:
        raise ValueError("LIVE_ORDER_SLIPPAGE_BPS must be between 0 and 5000")
    if not 0 <= LATE_CONVICTION_SLIPPAGE_BPS <= 5000:
        raise ValueError("LATE_CONVICTION_SLIPPAGE_BPS must be between 0 and 5000")
    if LIVE_ORDER_TYPE not in {"GTC", "GTD", "FOK", "FAK"}:
        raise ValueError("LIVE_ORDER_TYPE must be one of GTC, GTD, FOK, FAK")
    if ENTRY_WINDOW_START <= ENTRY_WINDOW_END:
        raise ValueError("ENTRY_WINDOW_START must be greater than ENTRY_WINDOW_END")
    if SNIPE_START_SECONDS <= SNIPE_HARD_DEADLINE_SECONDS:
        raise ValueError("SNIPE_START_SECONDS must be greater than SNIPE_HARD_DEADLINE_SECONDS")
    if EARLY_ENTRY_START_SECONDS <= SNIPE_START_SECONDS:
        raise ValueError("EARLY_ENTRY_START_SECONDS must be greater than SNIPE_START_SECONDS")
    if SNIPE_HARD_DEADLINE_SECONDS < 1:
        raise ValueError("SNIPE_HARD_DEADLINE_SECONDS must be at least 1")
    if SNIPE_POLL_SECONDS <= 0:
        raise ValueError("SNIPE_POLL_SECONDS must be greater than 0")
    if not 0 <= SNIPE_MIN_CONFIDENCE <= 1:
        raise ValueError("SNIPE_MIN_CONFIDENCE must be between 0 and 1")
    if SNIPE_SPIKE_CONFIDENCE_DELTA < 0:
        raise ValueError("SNIPE_SPIKE_CONFIDENCE_DELTA must be >= 0")
    if not 0 < ENTRY_MIN_CONTRACT_PRICE < 1:
        raise ValueError("ENTRY_MIN_CONTRACT_PRICE must be between 0 and 1")
    if not 0 < ENTRY_MAX_CONTRACT_PRICE < 1:
        raise ValueError("ENTRY_MAX_CONTRACT_PRICE must be between 0 and 1")
    if ENTRY_MIN_CONTRACT_PRICE >= ENTRY_MAX_CONTRACT_PRICE:
        raise ValueError("ENTRY_MIN_CONTRACT_PRICE must be less than ENTRY_MAX_CONTRACT_PRICE")
    if LIQUIDITY_MIN_ASK_COUNT < 1:
        raise ValueError("LIQUIDITY_MIN_ASK_COUNT must be at least 1")
    if LIQUIDITY_MIN_BID_COUNT < 0:
        raise ValueError("LIQUIDITY_MIN_BID_COUNT must be >= 0")
    if not 0 < LIQUIDITY_MAX_SIDE_SPREAD < 1:
        raise ValueError("LIQUIDITY_MAX_SIDE_SPREAD must be between 0 and 1")
    if not 0 <= LIQUIDITY_MIN_SCORE <= 1:
        raise ValueError("LIQUIDITY_MIN_SCORE must be between 0 and 1")
    if not 0 <= EARLY_ENTRY_MIN_SCORE <= 1:
        raise ValueError("EARLY_ENTRY_MIN_SCORE must be between 0 and 1")
    if MIN_EXPECTED_PROFIT_USDC < 0:
        raise ValueError("MIN_EXPECTED_PROFIT_USDC must be >= 0")
    if EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC < 0:
        raise ValueError("EARLY_ENTRY_MIN_EXPECTED_PROFIT_USDC must be >= 0")
    if EXPECTED_SLIPPAGE_BPS < 0:
        raise ValueError("EXPECTED_SLIPPAGE_BPS must be >= 0")
    if LATE_CONVICTION_START_SECONDS <= SNIPE_HARD_DEADLINE_SECONDS:
        raise ValueError(
            "LATE_CONVICTION_START_SECONDS must be greater than SNIPE_HARD_DEADLINE_SECONDS"
        )
    if not 0 < LATE_CONVICTION_MIN_CONTRACT_PRICE < 1:
        raise ValueError("LATE_CONVICTION_MIN_CONTRACT_PRICE must be between 0 and 1")
    if not 0 < LATE_CONVICTION_MAX_CONTRACT_PRICE < 1:
        raise ValueError("LATE_CONVICTION_MAX_CONTRACT_PRICE must be between 0 and 1")
    if LATE_CONVICTION_MIN_CONTRACT_PRICE >= LATE_CONVICTION_MAX_CONTRACT_PRICE:
        raise ValueError(
            "LATE_CONVICTION_MIN_CONTRACT_PRICE must be less than LATE_CONVICTION_MAX_CONTRACT_PRICE"
        )
    if LATE_CONVICTION_EDGE_THRESHOLD < 0:
        raise ValueError("LATE_CONVICTION_EDGE_THRESHOLD must be >= 0")
    if not 0 <= LATE_CONVICTION_MIN_POLY_SCORE <= 1:
        raise ValueError("LATE_CONVICTION_MIN_POLY_SCORE must be between 0 and 1")
    if not 0 <= LATE_CONVICTION_STRONG_POLY_SCORE <= 1:
        raise ValueError("LATE_CONVICTION_STRONG_POLY_SCORE must be between 0 and 1")
    if LATE_CONVICTION_STRONG_POLY_SCORE < LATE_CONVICTION_MIN_POLY_SCORE:
        raise ValueError(
            "LATE_CONVICTION_STRONG_POLY_SCORE must be >= LATE_CONVICTION_MIN_POLY_SCORE"
        )
    if LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC < 0:
        raise ValueError("LATE_CONVICTION_MIN_EXPECTED_PROFIT_USDC must be >= 0")
    if not 0 < LATE_CONVICTION_SIZE_MULTIPLIER <= 1:
        raise ValueError("LATE_CONVICTION_SIZE_MULTIPLIER must be between 0 and 1")
    if not 0 < JEV_NOUL_MIN_PROBABILITY < 1:
        raise ValueError("JEV_NOUL_MIN_PROBABILITY must be between 0 and 1")
    if JEV_API_TIMEOUT_SECONDS <= 0:
        raise ValueError("JEV_API_TIMEOUT_SECONDS must be > 0")
    if JEV_COST_PER_CALL_USD < 0:
        raise ValueError("JEV_COST_PER_CALL_USD must be >= 0")
    if not 0 < EARLY_CONVICTION_TRIGGER_PRICE < 1:
        raise ValueError("EARLY_CONVICTION_TRIGGER_PRICE must be between 0 and 1")
    if not 0 <= EARLY_CONVICTION_MIN_SCORE <= 1:
        raise ValueError("EARLY_CONVICTION_MIN_SCORE must be between 0 and 1")
    if not 0 < KELLY_FRACTION <= 1:
        raise ValueError("KELLY_FRACTION must be between 0 and 1")
    if STARTING_BANKROLL_USDC <= 0:
        raise ValueError("STARTING_BANKROLL_USDC must be > 0")
    if not 0 < BANKROLL_RISK_FRACTION <= 1:
        raise ValueError("BANKROLL_RISK_FRACTION must be between 0 and 1")
    if WIN_STREAK_BET_STEP < 0:
        raise ValueError("WIN_STREAK_BET_STEP must be >= 0")
    if LOSS_STREAK_BET_STEP < 0:
        raise ValueError("LOSS_STREAK_BET_STEP must be >= 0")
    if MODEL_C_MIN_BUCKET_SAMPLES < 1:
        raise ValueError("MODEL_C_MIN_BUCKET_SAMPLES must be at least 1")
    if RUNTIME_STATUS_UPDATE_SECONDS <= 0:
        raise ValueError("RUNTIME_STATUS_UPDATE_SECONDS must be greater than 0")
    if UNRESOLVED_SWEEP_INTERVAL_SECONDS <= 0:
        raise ValueError("UNRESOLVED_SWEEP_INTERVAL_SECONDS must be greater than 0")
    if POLYMARKET_HISTORY_MAX_SAMPLES < 3:
        raise ValueError("POLYMARKET_HISTORY_MAX_SAMPLES must be at least 3")
    if POLYMARKET_FEATURE_LOOKBACK_SECONDS <= 0:
        raise ValueError("POLYMARKET_FEATURE_LOOKBACK_SECONDS must be greater than 0")
    if VOLATILITY_MAX_SAMPLES < 2:
        raise ValueError("VOLATILITY_MAX_SAMPLES must be at least 2")
    if not 2 <= VOLATILITY_MIN_SAMPLES <= VOLATILITY_MAX_SAMPLES:
        raise ValueError(
            "VOLATILITY_MIN_SAMPLES must be at least 2 and no more than VOLATILITY_MAX_SAMPLES"
        )
    if LIVE_MIN_AVAILABLE_USDC < 0:
        raise ValueError("LIVE_MIN_AVAILABLE_USDC must be >= 0")
    if LIVE_BALANCE_BUFFER_USDC < 0:
        raise ValueError("LIVE_BALANCE_BUFFER_USDC must be >= 0")
    if LIVE_RECON_LOOKBACK_SECONDS < 0:
        raise ValueError("LIVE_RECON_LOOKBACK_SECONDS must be >= 0")
    if POLYMARKET_SIGNATURE_TYPE not in {0, 1, 2}:
        raise ValueError("POLYMARKET_SIGNATURE_TYPE must be 0, 1, or 2")
    if TARGET_ASSET not in BINANCE_SYMBOLS:
        raise ValueError(
            f"TARGET_ASSET must be one of: {', '.join(sorted(BINANCE_SYMBOLS))}"
        )
    _valid_tracks = {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}
    if ACTIVE_MODEL_TRACK not in _valid_tracks:
        raise ValueError(
            f"ACTIVE_MODEL_TRACK must be one of: {', '.join(sorted(_valid_tracks))}"
        )
    if PRICE_SOURCE_SELECTION not in {"fastest", "median"}:
        raise ValueError("PRICE_SOURCE_SELECTION must be 'fastest' or 'median'")

    have_any_api_creds = any(
        value.strip()
        for value in (
            POLYMARKET_API_KEY,
            POLYMARKET_SECRET,
            POLYMARKET_PASSPHRASE,
        )
    )
    have_all_api_creds = all(
        value.strip()
        for value in (
            POLYMARKET_API_KEY,
            POLYMARKET_SECRET,
            POLYMARKET_PASSPHRASE,
        )
    )
    if have_any_api_creds and not have_all_api_creds:
        raise ValueError(
            "Provide all POLYMARKET_API_KEY, POLYMARKET_SECRET, and "
            "POLYMARKET_PASSPHRASE together, or leave all three unset"
        )

    if require_trading_credentials and not POLYMARKET_PRIVATE_KEY.strip():
        raise ValueError("Missing required config: POLYMARKET_PRIVATE_KEY")

    if live_mode:
        logging.getLogger(__name__).warning(
            "LIVE TRADING MODE - real money at risk"
        )
