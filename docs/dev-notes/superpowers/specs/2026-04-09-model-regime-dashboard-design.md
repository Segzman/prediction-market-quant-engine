# Model Regime Filters + Dashboard Redesign

**Date:** 2026-04-09  
**Status:** Approved (v5 — fourth reviewer pass addressed)

---

## Overview

Add three named model tracks to the Polymarket 5-minute bot, each filtering which trading regime is allowed. Run all three in parallel shadow (dry-run) mode at all times; in live mode one track is designated active for real orders. Rebuild the web dashboard around a three-column panel layout and add structured, color-coded log output.

---

## 1. Model Track Architecture

### Three Regime-Filter Configurations

| Track | `regime_filter` set | Description |
|---|---|---|
| `CONVICTION_ONLY` | `{"LATE_CONVICTION"}` | Trades only when Polymarket book sentiment is strong |
| `MISPRICING_ONLY` | `{"MISPRICING"}` | Trades only when model detects price is wrong vs BTC move |
| `BOTH` | `{"MISPRICING", "LATE_CONVICTION"}` | Both regimes allowed — current behavior |

Valid `regime_filter` member values are the exact strings from `strategy/signal.py`: `"MISPRICING"` and `"LATE_CONVICTION"`.

### ModelTrack Replaces ModelRuntime

`ModelTrack` (new, `state/track.py`) replaces `ModelRuntime` entirely. `ModelRuntime` is deleted. `ModelTrack`:

```python
@dataclass
class ModelTrack:
    name: str                       # "CONVICTION_ONLY" | "MISPRICING_ONLY" | "BOTH"
    regime_filter: set[str]         # {"MISPRICING"} | {"LATE_CONVICTION"} | both
    model: str                      # "A" | "B" | "C" — primary probability engine
    state: BotState                 # per-track: positions, streak, cooldown, trade history
    stats: RuntimeStats             # per-track RuntimeStats (kept; required by select_snipe_signal)
    ledger: Ledger                  # per-track file (see Ledger Files below)
    guard: DailyGuard               # per-track instance — own consecutive_losses counter
    orderer: Orderer                # dry_run=True for shadow tracks, False for active
    finder: MarketFinder            # shared reference (read-only use only)
    ansi_color: str                 # ANSI escape code from TRACK_COLORS
    css_color: str                  # CSS color string for dashboard (e.g. "mediumpurple")
    active: bool                    # True for the one live-trading track only
    pnl_history: list[float] = field(default_factory=lambda: [0.0])
```

`RuntimeStats` is retained per-track. `select_snipe_signal` and `check_position_exits` continue to receive `stats=track.stats` from the calling track. `build_model_runtimes()` is renamed `build_model_tracks()` → `list[ModelTrack]` (always 3 entries).

### Shared Market State vs Per-Track Position State

Two `BotState` roles are distinguished:

- **Shared market state** — one instance owned by the main bot loop, used for `update_polymarket_book()`, `rolling_volatility()`, `tick_trend_features()`, `polymarket_samples`. This is the input to `select_snipe_signal()`. It is read-only from track perspective.
- **Per-track position state** — each `ModelTrack.state` tracks positions, trade history, win streak, consecutive losses, `last_trade_ts`. This is never shared.

`select_snipe_signal(model, state, ...)` is called with the **shared market state** for the track's primary `model` letter. It returns a `SnipeSelection` which is read-only.

### Engine Scores (A, B, C) — One Computation Per Tick

A new helper `compute_all_engine_scores(shared_state: BotState, window: MarketWindow) -> dict[str, float]` is called once per tick before fan-out. It calls `get_probability(model="A", state=shared_state, window=window)`, same for `"B"` and `"C"`, and returns the `theo_prob` value for the UP direction from each call as `{"A": 0.62, "B": 0.68, "C": 0.71}`. It does not call `evaluate_with_diagnostics` (which is per-trade). `window` is the current `MarketWindow` obtained from the shared finder. The returned dict is the same for all three tracks (market data, not track-specific). It is passed as a parameter to `_evaluate_track` and included verbatim in the status payload — it is not stored on `BotState`.

### Main Loop Fan-Out

`trading_loop()` accepts `tracks: list[ModelTrack]` plus a `shared_state: BotState`. Per tick:

1. Update `shared_state` with latest BTC price and Polymarket book.
2. Locate the `BOTH` track (always present). Call `select_snipe_signal(model=both_track.model, state=shared_state, orderer=both_track.orderer, ledger=both_track.ledger, stats=both_track.state.stats)` — uses BOTH track's config for signal generation since BOTH allows all regimes and is the natural superset. The returned `SnipeSelection` is shared across all tracks. `select_snipe_signal` mutates `shared_state.polymarket_samples` (book history) — this side-effect is intentional and shared.
3. Call `compute_all_engine_scores(shared_state, window)` once — `window` is the current `MarketWindow` from the shared finder.
4. Fan out: call `_evaluate_track(track, selection, engine_scores)` for each of the 3 tracks.
5. `_evaluate_track` checks `selection.signal.entry_regime in track.regime_filter`, then proceeds with the track's own `guard`, `orderer`, and `state`.

`unresolved_resolution_loop(tracks: list[ModelTrack])` is called once globally. It iterates over each track and calls `sweep_unresolved_trades_once(track)` for each track independently, using **that track's own `ledger`**. This replaces the existing `asyncio.gather` over a single ledger. `sweep_unresolved_trades_once(track: ModelTrack)` reads `track.ledger.get_unresolved_trades()`, resolves each, and calls `track.state.record_resolution(...)` — no cross-track routing needed since each track's ledger only contains that track's trades.

`Ledger.record_trade()` gains a `track: str = ""` parameter written as `"track": track.name` into every JSON record (for auditability). The `track` value is passed from `_evaluate_track` → `submit_trade` → `ledger.record_trade`. `submit_trade` and `check_position_exits` both receive the `ModelTrack` instance as their first argument so they have access to `track.name`, `track.ledger`, `track.orderer`, `track.state`, and `track.stats` without additional globals.

### Ledger Files

Each track uses a separate ledger file to avoid concurrent write corruption:

| Track | File |
|---|---|
| `CONVICTION_ONLY` | `trades_conviction.jsonl` |
| `MISPRICING_ONLY` | `trades_mispricing.jsonl` |
| `BOTH` | `trades_both.jsonl` (default `LEDGER_FILE`) |

Configurable via `LEDGER_FILE_CONVICTION`, `LEDGER_FILE_MISPRICING`, `LEDGER_FILE_BOTH` env vars with the above defaults.

### Shadow vs Live

- All three tracks run `Orderer(dry_run=True)` by default.
- In live mode (`DRY_RUN=False`), the track where `name == ACTIVE_MODEL_TRACK` gets `Orderer(dry_run=False)`. The other two remain `dry_run=True`.
- `ACTIVE_MODEL_TRACK` config: allowed values `{"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}`, validated in `validate_config()`. Changing this value requires a bot restart.
- If a track has an open position when the bot is restarted with a different `ACTIVE_MODEL_TRACK`, that position's entry was already shadow — its exit remains shadow.

### Cooldown — Per Track, Entries Only

Each `ModelTrack` has its own `DailyGuard` with its own `consecutive_losses` and `last_trade_ts`. A cooldown on one track has zero effect on others.

`DailyGuard.can_trade()` is renamed `DailyGuard.can_enter()`. There are exactly **3 direct callsites** of `guard.can_trade()` in the current `bot.py`. All 3 must be updated to `guard.can_enter()`, plus the method definition in `risk/guard.py` and references in `tests/test_risk.py`.

**Critical:** `check_position_exits()` never calls `DailyGuard`. It is called unconditionally on every tick for any track that has open positions. Exits (TP, SL, TIME_EXIT, WINDOW_CLOSE) always execute regardless of cooldown state on any track.

---

## 2. Dashboard

### IPC — File-Based (Unchanged Architecture)

`bot.py` and `webapp/server.py` run as separate processes. The communication channel remains `runtime_status.json` (file-based, as today). `build_runtime_status()` in `bot.py` writes the multi-track JSON to `runtime_status.json` on every status update tick. `webapp/manager.py` reads `runtime_status.json` to serve `/api/status`. No direct in-memory function calls across processes.

**Old → new key mapping for `runtime_status.json`:** The existing schema (`price`, `market`, `models`, `session`, `risk`) is **completely replaced** by the new multi-track schema defined below. `webapp/manager.py` `current_run()` reads and forwards the file as-is — it does not transform keys. `webapp/index.html` is a complete rewrite and does not depend on old keys.

### `/api/status` Payload Schema

`runtime_status.json` schema changes to the following. `webapp/manager.py` reads and forwards this verbatim from `/api/status`:

```json
{
  "btc_price": 83420.0,
  "btc_direction": "up",
  "window_seconds_remaining": 134,
  "next_window_seconds": 166,
  "mode": "DRY_RUN",
  "active_track": "BOTH",
  "total_pnl": 1.60,
  "engine_scores": {"A": 0.62, "B": 0.68, "C": 0.71},
  "tracks": [
    {
      "name": "CONVICTION_ONLY",
      "css_color": "mediumpurple",
      "status": "shadow",
      "signal": {"direction": "UP", "regime": "LATE_CONVICTION", "confidence": 0.71},
      "open_position": {
        "direction": "UP", "entry_price": 0.420, "current_price": 0.487,
        "unrealized_pnl": 0.52, "held_seconds": 43, "tp": 0.483, "sl": 0.387
      },
      "win_stats": {
        "win_rate": 0.75, "trades": 4,
        "avg_win": 0.54, "avg_loss": 0.42,
        "profit_factor": 2.1
      },
      "recent_trades": [
        {"direction": "UP", "exit_reason": "TAKE_PROFIT", "pnl": 0.52}
      ],
      "pnl_history": [0.0, 0.52, 0.90, 0.48, 1.20]
    }
  ]
}
```

Notes:
- `engine_scores` is at the **top level** (shared market data, same for all tracks).
- `open_position` is `null` when no position is open.
- `signal` is `null` when no signal this tick.
- `total_pnl` = sum of each track's last `pnl_history` value (sum of all shadow session P&L).
- `profit_factor` = `sum(winning_trade_pnl) / abs(sum(losing_trade_pnl))`; `null` if no losses yet.
- `status` values: `"shadow"` / `"live"` / `"cooldown"`.

### P&L Sparkline

`pnl_history: list[float]` lives directly on `ModelTrack` (not on `BotState`), as a dataclass field with `default_factory=lambda: [0.0]`. It is appended after each closed trade in `_evaluate_track`. In-memory only (not persisted across restarts). `build_runtime_status()` reads `track.pnl_history` when building the status payload. Frontend renders as SVG `<polyline>`.

### Dashboard Layout

Three-column panel layout at `http://localhost:8787`. Global status bar at top.

**Global status bar:** BTC spot price + direction, window countdown, time to next window, mode badge (`DRY RUN` or `LIVE · CONVICTION_ONLY`), total combined P&L.

**Per-model panel (top to bottom):**
1. **Header** — track name + badge: `LIVE` / `SHADOW` / `COOLDOWN`
2. **P&L sparkline** — SVG polyline from `pnl_history`; green above zero, red below
3. **Signal state** — `▲ UP` / `▼ DOWN` / `— NO TRADE`, regime, confidence
4. **Engine scores** — horizontal bars for A, B, C with numeric values (shared data, all panels show same values)
5. **Open position** — direction, entry, current price, unrealized P&L, time held, TP/SL; row amber when within 10% of SL, green when within 10% of TP
6. **Win stats** — win rate, trade count, avg win, avg loss, profit factor
7. **Recent trades** — last 5 closed trades: direction, exit reason, P&L

### Implementation

- `webapp/server.py` — `/api/status` reads `runtime_status.json` and returns contents verbatim (no structural change to the endpoint).
- `webapp/manager.py` — `current_run()` reads and returns `runtime_status.json` unchanged in signature. `start_run()` / `build_command()`: remove `LEDGER_FILE` from the subprocess env; add `LEDGER_FILE_CONVICTION`, `LEDGER_FILE_MISPRICING`, `LEDGER_FILE_BOTH` instead. The BOTH track's ledger file serves the role that `LEDGER_FILE` previously served.
- `webapp/index.html` — complete rewrite; polls `/api/status` every 2s; SVG sparklines inline; no external JS/CSS libraries.
- `bot.py` `build_runtime_status(tracks, shared_state, engine_scores)` — rewrites the JSON payload with the multi-track schema defined above.

---

## 3. Log Output

### Color Implementation

`TrackFormatter` (custom `logging.Formatter`) in `state/track.py`. ANSI code stripping is checked **per log record** (not at instantiation) by inspecting the handler's stream: `getattr(handler.stream, 'isatty', lambda: False)()`. This handles subprocess stdout redirection correctly. A `FORCE_COLOR=1` env var overrides and forces ANSI on regardless.

Each `ModelTrack` gets a `logging.Logger` named `f"bot.{track.name.lower()}"` with `TrackFormatter` attached to the root handler. `TRACK_COLORS` and `TRACK_CSS_COLORS` exported from `state/track.py`.

### Colors

| Track | ANSI | CSS |
|---|---|---|
| `CONVICTION_ONLY` | `\033[95m` (bright magenta) | `mediumpurple` |
| `MISPRICING_ONLY` | `\033[33m` (yellow) | `goldenrod` |
| `BOTH` | `\033[92m` (bright green) | `mediumseagreen` |

Reset: `\033[0m`

### Log Format

```
[ CONVICTION ]  SIGNAL ▲ UP  regime=LATE_CONVICTION | conf=0.71 | A:0.62 B:0.68 C:0.71
[ MISPRICING ]  NO TRADE     regime=LATE_CONVICTION blocked by filter
[ BOTH       ]  SIGNAL ▲ UP  regime=LATE_CONVICTION | conf=0.71 | A:0.62 B:0.68 C:0.71

[ CONVICTION ]  ENTER  ▲ UP  $5.00 @ 0.420 | TP=0.483 SL=0.387
[ CONVICTION ]  EXIT   ▲ UP  reason=TAKE_PROFIT | held=43s | pnl=+$0.52 | exit@0.487
[ MISPRICING ]  EXIT   ▲ UP  reason=STOP_LOSS   | held=91s | pnl=-$0.42 | exit@0.395
[ CONVICTION ]  COOLDOWN     3 consecutive losses → pause 3m (entries only, exits unaffected)
[ CONVICTION ]  SKIP         SIGNAL UP blocked — cooldown active (2m 14s remaining)
[ MISPRICING ]  SKIP         edge=0.021 below threshold 0.040
```

Window boundary marker (once per window, not per-track):
```
── 14:35:00 ──── WINDOW 20260409-14:35 | BTC $83,420 | 3m 00s ───────────────────────────
```

### Structured Keywords

| Keyword | Level | Meaning |
|---|---|---|
| `SIGNAL` | INFO | Signal passed regime filter |
| `NO TRADE` | INFO | Signal blocked (reason included) |
| `ENTER` | INFO | Position opened |
| `EXIT` | INFO | Position closed (reason + P&L) |
| `SKIP` | INFO | Entry skipped — reason always included |
| `COOLDOWN` | WARNING | Loss cooldown triggered for this track |
| `SCAN` | DEBUG | Per-tick market snapshot |

No silent skips — every reason a trade is blocked is logged at INFO.

---

## 4. Files Changed

| File | Change |
|---|---|
| `state/track.py` | **New** — `ModelTrack`, `TRACK_COLORS`, `TRACK_CSS_COLORS`, `TrackFormatter` |
| `state/__init__.py` | Export `ModelTrack`, `TRACK_COLORS`, `TRACK_CSS_COLORS`, `TrackFormatter` |
| `bot.py` | `ModelRuntime` deleted; `build_model_tracks()`; `trading_loop(tracks, shared_state)`; `compute_all_engine_scores()`; `build_runtime_status(tracks, shared_state, engine_scores)` updated; `sweep_unresolved_trades_once(track: ModelTrack)` signature updated; `unresolved_resolution_loop(tracks)` iterates per-track; `submit_trade` and `check_position_exits` gain `track: ModelTrack` as first arg; 3 `guard.can_trade()` → `guard.can_enter()` callsites |
| `config.py` | Add `ACTIVE_MODEL_TRACK`, `LEDGER_FILE_CONVICTION`, `LEDGER_FILE_MISPRICING`, `LEDGER_FILE_BOTH`; add all to `validate_config()`. `ACTIVE_MODEL_TRACK` check goes after the `TARGET_ASSET` check at the end of the value-range block, with message `"ACTIVE_MODEL_TRACK must be one of: CONVICTION_ONLY, MISPRICING_ONLY, BOTH"` |
| `risk/guard.py` | Rename `can_trade()` → `can_enter()`; no other logic changes |
| `persistence/ledger.py` | Add `track: str = ""` parameter to `record_trade()` |
| `webapp/server.py` | `/api/status` continues reading `runtime_status.json` — no structural change |
| `webapp/manager.py` | `current_run()` unchanged in signature; `runtime_status.json` now has multi-track schema; `start_run()` / `build_command()` updated to pass `LEDGER_FILE_CONVICTION`, `LEDGER_FILE_MISPRICING`, `LEDGER_FILE_BOTH` env vars to the bot subprocess |
| `webapp/index.html` | Complete rewrite — 3-column panels, SVG sparklines, auto-poll |
| `tests/test_bot_models.py` | Update for `ModelTrack` (replaces `ModelRuntime`) |
| `tests/test_risk.py` | Add: cooldown blocks `can_enter()`, does NOT block exits |

---

## 5. Out of Scope

- Multiple tracks live simultaneously (one active track only; restart required to change)
- Per-track sizing or risk limits (all tracks share `MAX_BET_USDC` / `MIN_BET_USDC`)
- Persisting sparkline history across bot restarts (in-memory session only)
- Mobile layout for dashboard
- Hot-reload of `ACTIVE_MODEL_TRACK` without restart
