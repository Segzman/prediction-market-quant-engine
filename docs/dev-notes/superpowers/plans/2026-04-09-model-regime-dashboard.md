# Model Regime Filters + Dashboard Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three model tracks (CONVICTION_ONLY, MISPRICING_ONLY, BOTH) as regime filters on top of existing A/B/C probability engines, with a 3-column dashboard and color-coded structured log output.

**Architecture:** `ModelTrack` replaces `ModelRuntime`. All three tracks share a single `shared_state: BotState` for market data (BTC price, book, volatility) but each has its own `BotState`, `DailyGuard`, `Ledger`, and `Orderer` for position tracking and risk. Signal generation runs once per tick using the BOTH track's config; each track then applies its regime filter independently. IPC between bot and webapp stays file-based via `runtime_status.json`.

**Tech Stack:** Python 3.13, asyncio, `logging` module (custom `TrackFormatter`), vanilla HTML/CSS/JS with inline SVG for sparklines, `dotenv` for config.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `state/track.py` | **Create** | `ModelTrack` dataclass, `TRACK_COLORS`, `TRACK_CSS_COLORS`, `TrackFormatter` |
| `state/__init__.py` | Modify | Export new symbols from `state/track.py` |
| `config.py` | Modify | `ACTIVE_MODEL_TRACK`, `LEDGER_FILE_CONVICTION`, `LEDGER_FILE_MISPRICING`, `LEDGER_FILE_BOTH` + validation |
| `risk/guard.py` | Modify | Rename `can_trade()` → `can_enter()` only |
| `persistence/ledger.py` | Modify | Add `track: str = ""` parameter to `record_trade()` |
| `bot.py` | Modify (large) | Replace `ModelRuntime` with `ModelTrack`; fan-out loop; multi-track status payload |
| `webapp/manager.py` | Modify | Pass per-track ledger env vars to bot subprocess |
| `webapp/index.html` | **Rewrite** | 3-column dashboard with SVG sparklines |
| `tests/test_bot_models.py` | Modify | Update `ModelRuntime` → `ModelTrack` references |
| `tests/test_risk.py` | Modify | Add cooldown-gates-entry-not-exit test; rename `can_trade` → `can_enter` |

---

## Task 1: ModelTrack dataclass + TrackFormatter

**Files:**
- Create: `state/track.py`
- Modify: `state/__init__.py`
- Test: `tests/test_track.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_track.py
import logging
import io
from state.track import ModelTrack, TRACK_COLORS, TRACK_CSS_COLORS, TrackFormatter


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
    handler = logging.StreamHandler(io.StringIO())
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    formatted = formatter.format(record)
    assert "\033[92m" in formatted
```

- [ ] **Step 2: Run tests — expect ImportError (file doesn't exist yet)**

```bash
cd . && python -m pytest tests/test_track.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'state.track'`

- [ ] **Step 3: Create `state/track.py`**

```python
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from market.finder import MarketFinder
    from market.orderer import Orderer
    from persistence.ledger import Ledger
    from risk.guard import DailyGuard
    from state.session import BotState
    from strategy.signal import RuntimeStats  # adjust import if RuntimeStats is elsewhere

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

    ANSI codes are stripped unless sys.stdout.isatty() or FORCE_COLOR=1.
    """

    def __init__(self, track_name: str, ansi_color: str, **kwargs):
        super().__init__(**kwargs)
        self._track_name = track_name
        self._ansi_color = ansi_color
        # Fixed-width label: e.g. "[ CONVICTION ]  "
        label_inner = track_name.replace("_ONLY", "").replace("_", " ")
        # Truncate/pad to 11 chars for alignment
        label_inner = label_inner[:11].ljust(11)
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
    name: str                              # "CONVICTION_ONLY" | "MISPRICING_ONLY" | "BOTH"
    regime_filter: set[str]                # {"MISPRICING"} | {"LATE_CONVICTION"} | both
    model: str                             # "A" | "B" | "C"
    state: BotState
    stats: object                          # RuntimeStats — typed as object to avoid circular import
    ledger: Ledger
    guard: DailyGuard
    orderer: Orderer
    finder: MarketFinder
    ansi_color: str
    css_color: str
    active: bool
    pnl_history: list[float] = field(default_factory=lambda: [0.0])
```

- [ ] **Step 4: Export from `state/__init__.py`**

Add to the bottom of the existing imports in `state/__init__.py`:

```python
from .track import ModelTrack, TRACK_COLORS, TRACK_CSS_COLORS, TrackFormatter
```

And add to `__all__`:
```python
"ModelTrack", "TRACK_COLORS", "TRACK_CSS_COLORS", "TrackFormatter",
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd . && python -m pytest tests/test_track.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add state/track.py state/__init__.py tests/test_track.py
git commit -m "feat: add ModelTrack dataclass and TrackFormatter"
```

---

## Task 2: Config additions

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add new config vars to `config.py`**

After the `ASSET_MARKET_SLUG_PREFIXES` block (around line 242), add:

```python
# Model track configuration
ACTIVE_MODEL_TRACK: Final[str] = os.getenv("ACTIVE_MODEL_TRACK", "BOTH").upper()
LEDGER_FILE_CONVICTION: Final[str] = os.getenv("LEDGER_FILE_CONVICTION", "trades_conviction.jsonl")
LEDGER_FILE_MISPRICING: Final[str] = os.getenv("LEDGER_FILE_MISPRICING", "trades_mispricing.jsonl")
LEDGER_FILE_BOTH: Final[str] = os.getenv("LEDGER_FILE_BOTH", "trades_both.jsonl")
```

- [ ] **Step 2: Add validation to `validate_config()`**

After the `TARGET_ASSET` check (the last check before the credentials block), add:

```python
    _valid_tracks = {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}
    if ACTIVE_MODEL_TRACK not in _valid_tracks:
        raise ValueError(
            f"ACTIVE_MODEL_TRACK must be one of: {', '.join(sorted(_valid_tracks))}"
        )
```

- [ ] **Step 3: Verify config loads without error**

```bash
cd . && python -c "from config import ACTIVE_MODEL_TRACK, LEDGER_FILE_CONVICTION, LEDGER_FILE_MISPRICING, LEDGER_FILE_BOTH; print(ACTIVE_MODEL_TRACK, LEDGER_FILE_CONVICTION)"
```
Expected: `BOTH trades_conviction.jsonl`

- [ ] **Step 4: Verify validation rejects bad value**

```bash
cd . && ACTIVE_MODEL_TRACK=INVALID python -c "from config import validate_config; validate_config()" 2>&1
```
Expected: `ValueError: ACTIVE_MODEL_TRACK must be one of: BOTH, CONVICTION_ONLY, MISPRICING_ONLY`

- [ ] **Step 5: Commit**

```bash
git add config.py
git commit -m "feat: add ACTIVE_MODEL_TRACK and per-track ledger file config"
```

---

## Task 3: Rename DailyGuard.can_trade → can_enter

**Files:**
- Modify: `risk/guard.py`
- Modify: `bot.py` (3 callsites)
- Modify: `tests/test_risk.py`

- [ ] **Step 1: Rename method in `risk/guard.py`**

Change `def can_trade(` to `def can_enter(` at the method definition (the only definition in the file).

- [ ] **Step 2: Update the 3 callsites in `bot.py`**

Find and replace these exact patterns:

**Callsite 1** (around line 1375 in `format_model_risk`):
```python
# Before:
can_trade, reason = guard.can_trade()
return "READY" if can_trade else reason

# After:
can_enter, reason = guard.can_enter()
return "READY" if can_enter else reason
```

**Callsite 2** (around line 1467):
```python
# Before:
can_trade, guard_reason = guard.can_trade()
guard_status = "READY" if can_trade else guard_reason

# After:
can_enter, guard_reason = guard.can_enter()
guard_status = "READY" if can_enter else guard_reason
```

**Callsite 3** (around line 2507):
```python
# Before:
can_trade, reason = guard.can_trade(direction=signal.direction.value if signal else None)
if not can_trade:

# After:
can_enter, reason = guard.can_enter(direction=signal.direction.value if signal else None)
if not can_enter:
```

- [ ] **Step 3: Update `tests/test_risk.py`**

Find all calls to `.can_trade(` and replace with `.can_enter(`. Also add the new test:

```python
def test_cooldown_blocks_entry_not_exit():
    """can_enter() is blocked during cooldown; check_position_exits never calls DailyGuard."""
    from risk.guard import DailyGuard
    from unittest.mock import MagicMock, patch
    import time

    state = MagicMock()
    state.daily_pnl = 0.0
    state.daily_trades = 0
    state.available_bankroll_usdc.return_value = 100.0
    state.consecutive_losses = 3   # triggers pause
    state.last_trade_ts = time.time()  # just traded
    state.is_price_stale.return_value = False
    state.current_window = None

    guard = DailyGuard(state)
    can, reason = guard.can_enter()
    assert can is False
    assert "pause" in reason.lower() or "cooldown" in reason.lower()

    # check_position_exits must not exist on DailyGuard (exits bypass it)
    assert not hasattr(guard, "can_exit")
```

- [ ] **Step 4: Run tests**

```bash
cd . && python -m pytest tests/test_risk.py -v
```
Expected: all tests pass including the new one.

- [ ] **Step 5: Commit**

```bash
git add risk/guard.py bot.py tests/test_risk.py
git commit -m "refactor: rename DailyGuard.can_trade to can_enter"
```

---

## Task 4: Add track param to Ledger.record_trade

**Files:**
- Modify: `persistence/ledger.py`

- [ ] **Step 1: Add `track` parameter to `record_trade()`**

In `persistence/ledger.py`, find the `record_trade` method signature (around line 23) and add `track: str = ""` as the last parameter before `feature_snapshot`. Also write it into the JSON record.

```python
def record_trade(
    self,
    window_end_ts: int,
    direction: str,
    amount_usdc: float,
    price: float,
    edge: float,
    ev: float,
    model: str,
    order_id,
    dry_run: bool,
    market_url: str = "",
    market_slug: str = "",
    asset: str = "",
    open_price=None,
    role: str = "PRIMARY",
    track: str = "",           # <-- ADD THIS
    feature_snapshot=None,
    execution_snapshot=None,
) -> None:
```

In the `record` dict inside the method body, add after `"role": role,`:

```python
    "track": track,
```

- [ ] **Step 2: Verify no tests break**

```bash
cd . && python -m pytest tests/ -v -x 2>&1 | tail -20
```
Expected: all existing tests still pass (new param has a default).

- [ ] **Step 3: Commit**

```bash
git add persistence/ledger.py
git commit -m "feat: add track field to ledger trade records"
```

---

## Task 5: build_model_tracks() factory

**Files:**
- Modify: `bot.py`
- Modify: `tests/test_bot_models.py`

- [ ] **Step 1: Write failing test**

In `tests/test_bot_models.py`, find `test_build_model_runtimes_clones_initial_window` (around line 50). Add a new test alongside it:

```python
def test_build_model_tracks_returns_three_tracks(mock_client):
    from bot import build_model_tracks
    from state.track import ModelTrack
    from config import ACTIVE_MODEL_TRACK

    tracks = build_model_tracks(
        client=mock_client,
        asset="BTC",
        mode_label="DRY_RUN",
        market_source="gamma",
        dry_run=True,
    )
    assert len(tracks) == 3
    names = {t.name for t in tracks}
    assert names == {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}

    # Each track has independent state
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")
    mispricing = next(t for t in tracks if t.name == "MISPRICING_ONLY")
    assert conviction.state is not mispricing.state
    assert conviction.ledger is not mispricing.ledger
    assert conviction.guard is not mispricing.guard

    # Regime filters
    assert conviction.regime_filter == {"LATE_CONVICTION"}
    assert mispricing.regime_filter == {"MISPRICING"}
    assert next(t for t in tracks if t.name == "BOTH").regime_filter == {"MISPRICING", "LATE_CONVICTION"}

    # In dry_run mode, all orderers are dry_run
    for t in tracks:
        assert t.orderer.dry_run is True

    # BOTH track is active when ACTIVE_MODEL_TRACK == "BOTH"
    both = next(t for t in tracks if t.name == "BOTH")
    if ACTIVE_MODEL_TRACK == "BOTH":
        assert both.active is True
        assert conviction.active is False
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_build_model_tracks_returns_three_tracks -v
```
Expected: `ImportError` or `AttributeError` — `build_model_tracks` doesn't exist yet.

- [ ] **Step 3: Add `build_model_tracks()` to `bot.py`**

Find `build_model_runtimes()` (around line 1306). Add `build_model_tracks()` as a new function just after it. Keep `build_model_runtimes()` in place for now — it will be removed in Task 12 once `trading_loop` is fully migrated.

```python
_TRACK_CONFIGS = [
    ("CONVICTION_ONLY", {"LATE_CONVICTION"}, "A"),
    ("MISPRICING_ONLY", {"MISPRICING"},       "B"),
    ("BOTH",            {"MISPRICING", "LATE_CONVICTION"}, "C"),
]

_TRACK_LEDGER_FILES = {
    "CONVICTION_ONLY": LEDGER_FILE_CONVICTION,
    "MISPRICING_ONLY": LEDGER_FILE_MISPRICING,
    "BOTH":            LEDGER_FILE_BOTH,
}


def build_model_tracks(
    *,
    client,
    asset: str,
    mode_label: str,
    market_source: str,
    dry_run: bool,
    initial_price: float = 0.0,
    initial_price_updated_ts: float = 0.0,
    initial_window: WindowState | None = None,
    starting_bankroll: float = STARTING_BANKROLL_USDC,
) -> list[ModelTrack]:
    tracks = []
    for name, regime_filter, model in _TRACK_CONFIGS:
        ledger_path = _TRACK_LEDGER_FILES[name]
        state = BotState(
            btc_price=initial_price,
            btc_price_updated_ts=initial_price_updated_ts,
            current_window=initial_window,
            starting_bankroll=starting_bankroll,
        )
        stats = RuntimeStats(model=model, mode_label=mode_label, asset=asset)
        ledger = Ledger(ledger_path)
        guard = DailyGuard(state)
        finder = MarketFinder(client, asset=asset)
        active = (name == ACTIVE_MODEL_TRACK)
        track_dry_run = dry_run or not active
        orderer = Orderer(client, dry_run=track_dry_run)
        tracks.append(ModelTrack(
            name=name,
            regime_filter=regime_filter,
            model=model,
            state=state,
            stats=stats,
            ledger=ledger,
            guard=guard,
            orderer=orderer,
            finder=finder,
            ansi_color=TRACK_COLORS[name],
            css_color=TRACK_CSS_COLORS[name],
            active=active,
        ))
    return tracks
```

Also add the required imports at the top of `bot.py` (if not already present):

```python
from config import (
    ACTIVE_MODEL_TRACK,
    LEDGER_FILE_CONVICTION,
    LEDGER_FILE_MISPRICING,
    LEDGER_FILE_BOTH,
)
from state.track import ModelTrack, TRACK_COLORS, TRACK_CSS_COLORS
```

- [ ] **Step 4: Run test — expect pass**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_build_model_tracks_returns_three_tracks -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "feat: add build_model_tracks factory with per-track ledgers"
```

---

## Task 6: compute_all_engine_scores()

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot_models.py`

- [ ] **Step 1: Write failing test**

```python
def test_compute_all_engine_scores_returns_dict_for_a_b_c(mock_client):
    from bot import compute_all_engine_scores
    from state.session import BotState
    from state.window import WindowState
    import time

    state = BotState(btc_price=83000.0)
    # Minimal WindowState — enough for get_probability to run
    end_ts = ((int(time.time()) // 300) + 1) * 300
    window = WindowState(
        condition_id="cond-1",
        up_token_id="up-1",
        down_token_id="dn-1",
        end_ts=end_ts,
        open_ts=end_ts - 300,
        market_slug="btc-updown-5m-test",
    )
    scores = compute_all_engine_scores(state, window)
    assert set(scores.keys()) == {"A", "B", "C"}
    for v in scores.values():
        assert 0.0 <= v <= 1.0
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_compute_all_engine_scores_returns_dict_for_a_b_c -v
```
Expected: `ImportError` — function doesn't exist.

- [ ] **Step 3: Add `compute_all_engine_scores()` to `bot.py`**

Place this near `build_model_tracks()` (after line ~1350):

```python
def compute_all_engine_scores(
    shared_state: BotState,
    window: WindowState,
) -> dict[str, float]:
    """Compute probability scores for all three model engines in one pass.

    Returns theo_prob for the UP direction from each model.
    All three tracks share the same scores since they derive from shared market state.
    """
    # open_price comes from the window (BTC price at window open), not from shared_state.
    # WindowState stores this — check the exact field name in state/window.py.
    # Common names: window.open_price, window.open_btc_price, window.reference_price.
    open_price = getattr(window, "open_price", None) or getattr(window, "open_btc_price", None) or shared_state.btc_price
    current_price = shared_state.btc_price
    seconds_remaining = max(1, window.seconds_remaining())
    # rolling_volatility() exists on BotState — check state/session.py for the method name.
    try:
        volatility = shared_state.rolling_volatility()
    except Exception:
        volatility = 0.0

    scores: dict[str, float] = {}
    for model_letter in ("A", "B", "C"):
        try:
            prob = get_probability(
                open_price=open_price,
                current_price=current_price,
                seconds_remaining=seconds_remaining,
                volatility=volatility,
                model=model_letter,
            )
            scores[model_letter] = round(prob, 4)
        except Exception:
            scores[model_letter] = 0.5  # fallback to neutral on error
    return scores
```

Note: `get_probability` is already imported in `bot.py` from `strategy.signal`. Verify the exact field names `btc_open_price`, `rolling_volatility()`, and `has_enough_volatility_samples()` against `state/session.py` — adjust if they differ.

- [ ] **Step 4: Run test — expect pass**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_compute_all_engine_scores_returns_dict_for_a_b_c -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "feat: add compute_all_engine_scores helper"
```

---

## Task 7: submit_trade + check_position_exits accept ModelTrack

**Files:**
- Modify: `bot.py`

The goal is to give both functions a `track: ModelTrack` first argument so they can access `track.ledger`, `track.orderer`, `track.state`, `track.stats`, `track.name` directly instead of receiving them as separate kwargs. Existing callers (from the old `trading_loop`) will break — that is expected and will be fixed in Task 12.

- [ ] **Step 1: Update `submit_trade` signature**

Find `async def submit_trade(` (around line 2092). Add `track: ModelTrack` as the first parameter. Inside the function, replace references to `state`, `orderer`, `ledger`, `stats` with `track.state`, `track.orderer`, `track.ledger`, `track.stats`. Also pass `track=track.name` when calling `ledger.record_trade(...)`.

New signature:
```python
async def submit_trade(
    track: ModelTrack,
    *,
    window: WindowState,
    signal,
    amount_usdc: float,
    role: str,
    asset: str,
    feature_snapshot: dict | None = None,
    execution_snapshot: dict | None = None,
) -> SubmittedTrade | None:
```

Inside the body, replace:
- `state` → `track.state`
- `orderer` → `track.orderer`
- `ledger` → `track.ledger`
- `stats` → `track.stats`

In the `ledger.record_trade(...)` call, add `track=track.name` as a keyword argument.

- [ ] **Step 2: Update `check_position_exits` signature**

Find `async def check_position_exits(` (around line 180). Add `track: ModelTrack` as the first parameter. Replace `orderer`, `state`, `stats`, `ledger` references with `track.*`.

New signature:
```python
async def check_position_exits(
    track: ModelTrack,
    *,
    asset: str,
    window: WindowState,
    up_price: float,
    down_price: float,
) -> list[Position]:
```

Inside the body, replace `orderer` → `track.orderer`, `state` → `track.state`, `stats` → `track.stats`, `ledger` → `track.ledger`.

- [ ] **Step 3: Verify the module still imports cleanly**

```bash
cd . && python -c "import bot; print('ok')"
```
Expected: `ok` (existing callers in old `trading_loop` will now have type mismatches, but Python won't catch these until runtime)

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "refactor: submit_trade and check_position_exits accept ModelTrack"
```

---

## Task 8: _evaluate_track() helper

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot_models.py`

`_evaluate_track` is the per-track fan-out function that applies the regime filter and decides whether to enter or skip.

- [ ] **Step 1: Write failing test**

```python
def test_evaluate_track_skips_wrong_regime(mock_client):
    """CONVICTION_ONLY track must skip MISPRICING signals."""
    from bot import build_model_tracks, _evaluate_track
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    # Create a mock selection whose signal has regime=MISPRICING
    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "MISPRICING"
    signal.confidence = 0.7

    selection = MagicMock()
    selection.signal = signal
    selection.up_price = 0.45
    selection.down_price = 0.55

    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}
    window = MagicMock()
    window.seconds_remaining.return_value = 120

    # Run _evaluate_track — should skip (no entry) because regime doesn't match
    conviction.guard.can_enter = MagicMock(return_value=(True, ""))
    asyncio.get_event_loop().run_until_complete(
        _evaluate_track(conviction, selection, engine_scores, window=window, asset="BTC")
    )
    # orderer.place_bet should NOT have been called
    conviction.orderer.place_bet.assert_not_called()


def test_evaluate_track_enters_matching_regime(mock_client):
    """CONVICTION_ONLY track must enter on LATE_CONVICTION signal when guard allows."""
    from bot import build_model_tracks, _evaluate_track
    from unittest.mock import MagicMock, patch
    import asyncio

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    signal = MagicMock()
    signal.direction.value = "UP"
    signal.entry_regime = "LATE_CONVICTION"
    signal.confidence = 0.72

    selection = MagicMock()
    selection.signal = signal
    selection.up_price = 0.42
    selection.down_price = 0.58

    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}
    window = MagicMock()
    window.seconds_remaining.return_value = 45
    window.has_open_positions.return_value = False

    conviction.guard.can_enter = MagicMock(return_value=(True, ""))

    with patch("bot.submit_trade", new_callable=MagicMock) as mock_submit:
        mock_submit.return_value = None
        asyncio.get_event_loop().run_until_complete(
            _evaluate_track(conviction, selection, engine_scores, window=window, asset="BTC")
        )
    mock_submit.assert_called_once()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_evaluate_track_skips_wrong_regime tests/test_bot_models.py::test_evaluate_track_enters_matching_regime -v
```
Expected: `ImportError` — `_evaluate_track` doesn't exist.

- [ ] **Step 3: Add `_evaluate_track()` to `bot.py`**

Place after `compute_all_engine_scores`:

```python
async def _evaluate_track(
    track: ModelTrack,
    selection: SnipeSelection | None,
    engine_scores: dict[str, float],
    *,
    window: WindowState,
    asset: str,
) -> None:
    """Apply regime filter and independently decide entry/exit for one track."""
    logger = logging.getLogger(f"bot.{track.name.lower()}")
    prefix = track.name.replace("_ONLY", "").replace("_", " ")[:11].ljust(11)

    # --- EXITS: always run, regardless of cooldown ---
    if window.has_open_positions():
        up_book = getattr(window, "up_book", None)
        down_book = getattr(window, "down_book", None)
        up_price = up_book.best_bid if up_book and up_book.best_bid else None
        down_price = down_book.best_bid if down_book and down_book.best_bid else None
        if up_price is not None or down_price is not None:
            closed = await check_position_exits(
                track,
                asset=asset,
                window=window,
                up_price=up_price or 0.0,
                down_price=down_price or 0.0,
            )
            for pos in closed:
                track.pnl_history.append(
                    round(track.pnl_history[-1] + pos.realized_pnl, 4)
                )

    # --- ENTRIES: gated by regime filter + cooldown ---
    if selection is None or selection.signal is None:
        logger.info("NO TRADE     no signal this tick")
        return

    signal = selection.signal
    if signal.entry_regime not in track.regime_filter:
        logger.info(
            "NO TRADE     regime=%s blocked by filter (%s)",
            signal.entry_regime,
            track.name,
        )
        return

    can_enter, reason = track.guard.can_enter(
        direction=signal.direction.value if signal else None
    )
    if not can_enter:
        logger.info("SKIP         %s", reason)
        return

    logger.info(
        "SIGNAL %s %s  regime=%s | conf=%.2f | A:%.2f B:%.2f C:%.2f",
        "▲ UP " if signal.direction.value == "UP" else "▼ DN",
        "",
        signal.entry_regime,
        getattr(signal, "confidence", 0.0),
        engine_scores.get("A", 0.0),
        engine_scores.get("B", 0.0),
        engine_scores.get("C", 0.0),
    )

    # Compute bet size using existing sizing logic.
    # The actual function is plan_position_sizes (plural) in risk/sizing.py.
    # Look at the callsite around bot.py line 2677 to see the exact arguments —
    # it takes target_primary_bet, available_bankroll, hedge_enabled, hedge_fraction, etc.
    # Use that same call pattern here, pulling values from track.state and config.
    # Example (verify exact args from the existing callsite):
    sizes = plan_position_sizes(
        target_primary_bet=min(MAX_BET_USDC, max(MIN_BET_USDC, track.state.available_bankroll_usdc() * BANKROLL_RISK_FRACTION)),
        available_bankroll=track.state.available_bankroll_usdc(),
        hedge_enabled=HEDGE_ENABLED,
        hedge_fraction=HEDGE_FRACTION,
    )
    amount_usdc = sizes.primary
    await submit_trade(
        track,
        window=window,
        signal=signal,
        amount_usdc=amount_usdc,
        role="PRIMARY",
        asset=asset,
    )
```

Note: `plan_position_sizes` lives in `risk/sizing.py` and is already imported in `bot.py`. Check the existing callsite around line 2677 for the exact parameter names and pattern — copy that pattern here rather than guessing.

- [ ] **Step 4: Run tests — expect pass**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_evaluate_track_skips_wrong_regime tests/test_bot_models.py::test_evaluate_track_enters_matching_regime -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "feat: add _evaluate_track fan-out helper with regime filter"
```

---

## Task 9: sweep_unresolved_trades_once + unresolved_resolution_loop — per-track

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot_models.py`

- [ ] **Step 1: Write failing test**

```python
def test_sweep_unresolved_uses_track_ledger(mock_client):
    from bot import build_model_tracks, sweep_unresolved_trades_once
    from unittest.mock import MagicMock

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    conviction = next(t for t in tracks if t.name == "CONVICTION_ONLY")

    # Ledger returns no unresolved trades
    conviction.ledger.get_unresolved_trades = MagicMock(return_value=[])
    count = sweep_unresolved_trades_once(conviction, mock_client)
    assert count == 0
    conviction.ledger.get_unresolved_trades.assert_called_once()
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_sweep_unresolved_uses_track_ledger -v
```
Expected: TypeError — wrong signature.

- [ ] **Step 3: Update `sweep_unresolved_trades_once` in `bot.py`**

Find the existing function (around line 698). Change its signature from:
```python
def sweep_unresolved_trades_once(market_client, ledger, runtimes, *, now_ts=None):
```
to:
```python
def sweep_unresolved_trades_once(track: ModelTrack, market_client, *, now_ts=None) -> int:
```

Inside the body:
- Replace `ledger` with `track.ledger`
- Replace `runtimes` iteration with using `track.state` and `track.stats` directly
- The function only resolves trades from this track's ledger

- [ ] **Step 4: Update `unresolved_resolution_loop` in `bot.py`**

Find the existing function (around line 1687). Change its signature from:
```python
async def unresolved_resolution_loop(market_client, ledger, runtimes, *, interval):
```
to:
```python
async def unresolved_resolution_loop(tracks: list[ModelTrack], market_client, *, interval: float) -> None:
```

Inside the loop body, replace the single `sweep_unresolved_trades_once(market_client, ledger, runtimes)` call with:
```python
for track in tracks:
    sweep_unresolved_trades_once(track, market_client, now_ts=now_ts)
```

- [ ] **Step 5: Run test — expect pass**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_sweep_unresolved_uses_track_ledger -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "refactor: sweep_unresolved_trades_once and resolution loop are per-track"
```

---

## Task 10: build_runtime_status() — multi-track schema

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot_models.py`

- [ ] **Step 1: Write failing test**

```python
def test_build_runtime_status_multi_track_schema(mock_client):
    from bot import build_model_tracks, build_runtime_status
    from state.session import BotState

    tracks = build_model_tracks(client=mock_client, asset="BTC",
                                mode_label="DRY_RUN", market_source="gamma", dry_run=True)
    shared_state = BotState(btc_price=83420.0)
    engine_scores = {"A": 0.62, "B": 0.68, "C": 0.71}

    payload = build_runtime_status(tracks, shared_state, engine_scores)

    assert "btc_price" in payload
    assert "engine_scores" in payload
    assert payload["engine_scores"] == {"A": 0.62, "B": 0.68, "C": 0.71}
    assert "tracks" in payload
    assert len(payload["tracks"]) == 3

    track_names = {t["name"] for t in payload["tracks"]}
    assert track_names == {"CONVICTION_ONLY", "MISPRICING_ONLY", "BOTH"}

    for t in payload["tracks"]:
        assert "css_color" in t
        assert "status" in t
        assert "win_stats" in t
        assert "pnl_history" in t
        assert t["pnl_history"] == [0.0]
        assert "recent_trades" in t
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_build_runtime_status_multi_track_schema -v
```
Expected: TypeError — wrong signature.

- [ ] **Step 3: Rewrite `build_runtime_status` in `bot.py`**

Find `def build_runtime_status(runtimes: list[ModelRuntime]) -> dict:` (around line 1557) and replace the entire function:

```python
def build_runtime_status(
    tracks: list[ModelTrack],
    shared_state: BotState,
    engine_scores: dict[str, float],
) -> dict:
    now = time.time()
    window = shared_state.current_window

    # BTC open price lives on the window (WindowState.open_price), not on BotState.
    btc_direction = "flat"
    window_open_price = window.open_price if window and hasattr(window, "open_price") else None
    if shared_state.btc_price and window_open_price:
        if shared_state.btc_price > window_open_price:
            btc_direction = "up"
        elif shared_state.btc_price < window_open_price:
            btc_direction = "down"

    secs_remaining = window.seconds_remaining() if window else 0
    next_window_secs = (300 - (int(now) % 300)) if secs_remaining <= 0 else 0

    active_track_name = next((t.name for t in tracks if t.active), "BOTH")
    # Determine mode from active track
    active_track = next((t for t in tracks if t.active), tracks[-1])
    mode = "LIVE" if not active_track.orderer.dry_run else "DRY_RUN"

    total_pnl = round(sum(t.pnl_history[-1] for t in tracks), 4)

    track_payloads = []
    for track in tracks:
        state = track.state
        stats = track.stats

        # Status badge
        can_enter, _ = track.guard.can_enter()
        if track.active and not track.orderer.dry_run:
            status = "live"
        elif not can_enter:
            status = "cooldown"
        else:
            status = "shadow"

        # Signal (last known)
        last_signal = getattr(stats, "last_signal", None)
        signal_payload = None
        if last_signal:
            signal_payload = {
                "direction": getattr(last_signal, "direction_str", None),
                "regime": getattr(last_signal, "entry_regime", None),
                "confidence": round(getattr(last_signal, "confidence", 0.0), 3),
            }

        # Open position
        open_pos_payload = None
        open_positions = state.get_open_positions() if hasattr(state, "get_open_positions") else []
        if open_positions:
            pos = open_positions[0]
            current_price = (
                state.current_window.up_book.best_bid
                if pos.direction == "UP" and state.current_window and state.current_window.up_book
                else state.current_window.down_book.best_bid
                if pos.direction == "DOWN" and state.current_window and state.current_window.down_book
                else pos.entry_price
            )
            open_pos_payload = {
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "unrealized_pnl": round(pos.unrealized_pnl(current_price), 4),
                "held_seconds": int(now - pos.entry_ts),
                "tp": pos.take_profit_price,
                "sl": pos.stop_loss_price,
            }

        # Win stats
        trade_history = getattr(state, "trade_history", [])
        wins = [t for t in trade_history if getattr(t, "pnl", 0) > 0]
        losses = [t for t in trade_history if getattr(t, "pnl", 0) < 0]
        win_rate = round(len(wins) / len(trade_history), 3) if trade_history else 0.0
        avg_win = round(sum(t.pnl for t in wins) / len(wins), 4) if wins else 0.0
        avg_loss = round(sum(t.pnl for t in losses) / len(losses), 4) if losses else 0.0
        gross_wins = sum(t.pnl for t in wins)
        gross_losses = abs(sum(t.pnl for t in losses))
        profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else None

        # Recent trades (last 5)
        recent = trade_history[-5:] if trade_history else []
        recent_payload = [
            {
                "direction": getattr(t, "direction", ""),
                "exit_reason": getattr(t, "exit_reason", ""),
                "pnl": round(getattr(t, "pnl", 0.0), 4),
            }
            for t in reversed(recent)
        ]

        track_payloads.append({
            "name": track.name,
            "css_color": track.css_color,
            "status": status,
            "signal": signal_payload,
            "open_position": open_pos_payload,
            "win_stats": {
                "win_rate": win_rate,
                "trades": len(trade_history),
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
            },
            "recent_trades": recent_payload,
            "pnl_history": track.pnl_history,
        })

    return {
        "generated_ts": now,
        "btc_price": shared_state.btc_price or 0.0,
        "btc_direction": btc_direction,
        "window_seconds_remaining": secs_remaining,
        "next_window_seconds": next_window_secs,
        "mode": mode,
        "active_track": active_track_name,
        "total_pnl": total_pnl,
        "engine_scores": engine_scores,
        "tracks": track_payloads,
    }
```

Note: Adjust field access (`pos.direction`, `pos.entry_price`, `pos.take_profit_price`, `pos.stop_loss_price`, `t.pnl`, `t.direction`, `t.exit_reason`) to match the actual `Position` and `TradeRecord` field names in `state/position.py` and `state/session.py`.

- [ ] **Step 4: Run test — expect pass**

```bash
cd . && python -m pytest tests/test_bot_models.py::test_build_runtime_status_multi_track_schema -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "feat: build_runtime_status outputs multi-track JSON schema"
```

---

## Task 11: Refactor trading_loop — full fan-out

**Files:**
- Modify: `bot.py`

This is the central wiring task. The existing `trading_loop` takes individual args (state, finder, orderer, guard, ledger, ...). We replace this with `tracks: list[ModelTrack]` + `shared_state: BotState`.

- [ ] **Step 1: Add new `trading_loop` signature**

The existing `trading_loop` (around line 2260) takes:
```python
async def trading_loop(state, finder, orderer, guard, ledger, market_client, model, *, once, asset, stats, live_mode, stop_event):
```

Change it to:
```python
async def trading_loop(
    tracks: list[ModelTrack],
    shared_state: BotState,
    market_client,
    *,
    once: bool,
    asset: str,
    live_mode: bool = False,
    stop_event: asyncio.Event | None = None,
) -> None:
```

- [ ] **Step 2: Rewrite the loop body**

The new loop body structure (preserve all existing logic for window finding, BTC feed, etc., just replace the signal evaluation and entry/exit parts):

```python
    # Derived helpers
    both_track = next(t for t in tracks if t.name == "BOTH")
    finder = both_track.finder  # shared finder (any track's finder works)

    while True:
        if stop_event and stop_event.is_set():
            break

        # -- 1. Get current window --
        window = finder.find_active_window()
        if window is None:
            await asyncio.sleep(cfg.SNIPE_POLL_SECONDS)
            if once:
                break
            continue

        # Print window boundary marker on new window
        # (add logic to track previous window condition_id)

        # -- 2. Update shared market state --
        # (preserve existing BTC price update and book refresh logic,
        #  but use shared_state instead of single state)

        # -- 3. Generate signal once (using BOTH track) --
        engine_scores = compute_all_engine_scores(shared_state, window)
        selection = await select_snipe_signal(
            model=both_track.model,
            state=shared_state,
            orderer=both_track.orderer,
            ledger=both_track.ledger,
            stats=both_track.stats,
            window=window,
            asset=asset,
            live_mode=live_mode,
            # IMPORTANT: also carry forward these keyword args from the existing trading_loop
            # (check the old select_snipe_signal callsite around line 2497 for the full list):
            force_trade=force_interval_trades,
            allow_waiting_conviction=waiting_conviction_active,
        )

        # -- 4. Fan out to each track --
        for track in tracks:
            await _evaluate_track(track, selection, engine_scores, window=window, asset=asset)

        # -- 5. Write status file --
        status = build_runtime_status(tracks, shared_state, engine_scores)
        _write_runtime_status(status)  # existing helper or write directly

        await asyncio.sleep(cfg.SNIPE_POLL_SECONDS)
        if once:
            break
```

Note: Preserve all existing window boundary detection, BTC price feed handling, session reset logic, and rate limiting from the original `trading_loop`. Only replace the signal evaluation + entry/exit section with the fan-out. Do not remove any existing safeguards.

- [ ] **Step 3: Update call sites that invoke `trading_loop`**

Find where `trading_loop(...)` is called (the main coroutine, around line 2400+). Update the call to pass `tracks` and `shared_state` instead of individual runtime args.

Where `build_model_runtimes()` was previously called to set up runtimes, replace with `build_model_tracks()`. Pass the first (or any) track's `shared_state` as `shared_state`.

- [ ] **Step 4: Delete `ModelRuntime` class and `build_model_runtimes()`**

Find and remove the `ModelRuntime` dataclass (around line 958) and `build_model_runtimes()` function (around line 1306). If any other function still references `ModelRuntime`, update it to use `ModelTrack`.

- [ ] **Step 5: Run the full test suite**

```bash
cd . && python -m pytest tests/ -v -x 2>&1 | tail -40
```
Expected: all tests pass. Fix any remaining `ModelRuntime` references.

- [ ] **Step 6: Smoke test — verify bot runs in dry-run mode**

```bash
cd . && timeout 15 python bot.py --demo --once 2>&1 | tail -20
```
Expected: Bot starts, finds (or doesn't find) a window, logs with colored track prefixes, exits cleanly.

- [ ] **Step 7: Commit**

```bash
git add bot.py tests/test_bot_models.py
git commit -m "feat: trading_loop fans out to 3 tracks with shared signal generation"
```

---

## Task 12: webapp/manager.py — per-track ledger env vars

**Files:**
- Modify: `webapp/manager.py`

- [ ] **Step 1: Update `start_run()` / `build_command()` to pass per-track ledger env vars**

In `webapp/manager.py`, find the `start_run()` function (around line 72). Locate where `env.update({...})` is called to set the subprocess environment. Replace the `LEDGER_FILE` entry with the three per-track ledger files:

```python
# Remove:
# "LEDGER_FILE": str(ledger_file),

# Add:
"LEDGER_FILE_CONVICTION": str(self.runtime_dir / f"{run_id}_trades_conviction.jsonl"),
"LEDGER_FILE_MISPRICING": str(self.runtime_dir / f"{run_id}_trades_mispricing.jsonl"),
"LEDGER_FILE_BOTH":       str(self.runtime_dir / f"{run_id}_trades_both.jsonl"),
```

Update `state["ledger_file"]` (if used for `build_report_payload`) to use the BOTH track's ledger file:
```python
"ledger_file": str(self.runtime_dir / f"{run_id}_trades_both.jsonl"),
```

- [ ] **Step 2: Verify webapp still starts cleanly**

```bash
cd . && timeout 5 python -m webapp.server 2>&1 | head -10
```
Expected: Server starts on port 8787.

- [ ] **Step 3: Commit**

```bash
git add webapp/manager.py
git commit -m "feat: webapp passes per-track ledger env vars to bot subprocess"
```

---

## Task 13: webapp/index.html — 3-column dashboard

**Files:**
- Modify: `webapp/index.html`

- [ ] **Step 1: Rewrite `webapp/index.html` entirely**

The new dashboard polls `/api/status` every 2 seconds and renders the 3-column panel layout. No external libraries.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Bot Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; }

  /* Global status bar */
  #status-bar {
    background: #161b22; border-bottom: 1px solid #30363d;
    padding: 8px 16px; display: flex; gap: 24px; align-items: center; flex-wrap: wrap;
  }
  #status-bar .item { display: flex; flex-direction: column; }
  #status-bar .label { font-size: 9px; color: #6e7681; text-transform: uppercase; letter-spacing: 0.5px; }
  #status-bar .value { font-size: 13px; font-weight: 700; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; }
  .badge-dry { background: #1f2937; color: #6b7280; }
  .badge-live { background: #14532d; color: #4ade80; }

  /* 3-column grid */
  #panels { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; padding: 12px; }

  /* Panel */
  .panel {
    background: #161b22; border-radius: 8px; padding: 12px;
    border-top: 2px solid var(--track-color); border: 1px solid #30363d;
    border-top-width: 2px;
  }
  .panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .panel-title { font-weight: 700; font-size: 13px; color: var(--track-color); }
  .status-badge { padding: 2px 7px; border-radius: 10px; font-size: 9px; font-weight: 700; }
  .status-shadow  { background: #1f2937; color: #6b7280; }
  .status-live    { background: #14532d; color: #4ade80; }
  .status-cooldown { background: #431407; color: #f87171; }

  /* Sparkline */
  .sparkline-container { height: 48px; background: #0d1117; border-radius: 4px; margin-bottom: 10px; position: relative; overflow: hidden; }
  .sparkline-container svg { width: 100%; height: 100%; }
  .sparkline-pnl { position: absolute; top: 4px; right: 6px; font-size: 11px; font-weight: 700; }

  /* Sections */
  .section { background: #0d1117; border-radius: 4px; padding: 7px; margin-bottom: 8px; }
  .section-label { font-size: 9px; color: #6e7681; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }

  /* Signal */
  .signal-row { display: flex; align-items: center; gap: 8px; }
  .signal-dir { padding: 2px 8px; border-radius: 3px; font-weight: 700; font-size: 11px; }
  .signal-up   { background: #14532d22; color: #4ade80; }
  .signal-down { background: #7f1d1d22; color: #f87171; }
  .signal-none { background: #1f2937; color: #6b7280; }
  .signal-meta { color: #6b7280; font-size: 10px; }

  /* Engine bars */
  .engine-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  .engine-label { color: #6e7681; font-size: 9px; width: 12px; }
  .engine-bar-bg { flex: 1; height: 5px; background: #21262d; border-radius: 2px; }
  .engine-bar-fill { height: 100%; border-radius: 2px; background: var(--track-color); transition: width 0.3s; }
  .engine-value { color: var(--track-color); font-size: 9px; width: 28px; text-align: right; }

  /* Open position */
  .pos-row { display: flex; justify-content: space-between; margin-bottom: 3px; }
  .pos-dir { font-weight: 700; }
  .pos-tp-sl { display: flex; gap: 4px; margin-top: 4px; }
  .pos-tp { flex: 1; background: #14532d33; color: #4ade80; text-align: center; border-radius: 2px; padding: 2px 0; font-size: 9px; }
  .pos-sl { flex: 1; background: #7f1d1d33; color: #f87171; text-align: center; border-radius: 2px; padding: 2px 0; font-size: 9px; }
  .pos-warn-sl { background: #f59e0b33 !important; border: 1px solid #f59e0b; }
  .pos-warn-tp { background: #4ade8033 !important; border: 1px solid #4ade80; }

  /* Win stats */
  .stats-grid { display: flex; gap: 4px; }
  .stat-cell { flex: 1; background: #0d1117; border-radius: 3px; padding: 4px; text-align: center; }
  .stat-value { font-size: 12px; font-weight: 700; }
  .stat-label { font-size: 8px; color: #6e7681; margin-top: 1px; }

  /* Recent trades */
  .trade-row { display: flex; justify-content: space-between; background: #0d1117; padding: 3px 6px; border-radius: 2px; margin-bottom: 2px; font-size: 10px; }
  .trade-up { color: #4ade80; }
  .trade-dn { color: #f87171; }
  .trade-reason { color: #6e7681; }

  .green { color: #4ade80; }
  .red   { color: #f87171; }
  .dim   { color: #6e7681; }
</style>
</head>
<body>

<div id="status-bar">
  <div class="item">
    <span class="label">BTC Price</span>
    <span class="value" id="btc-price">—</span>
  </div>
  <div class="item">
    <span class="label">Window</span>
    <span class="value" id="window-countdown">—</span>
  </div>
  <div class="item">
    <span class="label">Next Window</span>
    <span class="value" id="next-window">—</span>
  </div>
  <div class="item">
    <span class="label">Mode</span>
    <span class="value" id="mode-badge"><span class="badge badge-dry">DRY RUN</span></span>
  </div>
  <div class="item">
    <span class="label">Total P&amp;L</span>
    <span class="value" id="total-pnl">—</span>
  </div>
</div>

<div id="panels"></div>

<script>
const TRACK_COLORS = {
  "CONVICTION_ONLY": "mediumpurple",
  "MISPRICING_ONLY": "goldenrod",
  "BOTH": "mediumseagreen",
};

const TRACK_LABELS = {
  "CONVICTION_ONLY": "CONVICTION",
  "MISPRICING_ONLY": "MISPRICING",
  "BOTH": "BOTH",
};

function fmt(n, decimals=2, prefix="") {
  if (n === null || n === undefined) return "—";
  const s = Math.abs(n).toFixed(decimals);
  return (n >= 0 ? prefix + s : "-" + prefix + s);
}

function fmtPnl(n) {
  if (n === null || n === undefined) return "—";
  const s = "$" + Math.abs(n).toFixed(2);
  return n >= 0 ? "+" + s : "-" + s;
}

function buildSparkline(history) {
  if (!history || history.length < 2) return `<line x1="0" y1="24" x2="200" y2="24" stroke="#30363d" stroke-width="1"/>`;
  const min = Math.min(...history);
  const max = Math.max(...history);
  const range = Math.max(Math.abs(max - min), 0.01);
  const w = 200, h = 44, pad = 4;
  const pts = history.map((v, i) => {
    const x = pad + (i / (history.length - 1)) * (w - 2 * pad);
    const y = h - pad - ((v - min) / range) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = history[history.length - 1];
  const color = last >= 0 ? "#4ade80" : "#f87171";
  const fillPts = `${pad},${h - pad} ${pts} ${w - pad},${h - pad}`;
  return `
    <polygon points="${fillPts}" fill="${color}15"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/>`;
}

function renderTrack(track, engineScores) {
  const color = TRACK_COLORS[track.name] || "#888";
  const label = TRACK_LABELS[track.name] || track.name;
  const last_pnl = track.pnl_history ? track.pnl_history[track.pnl_history.length - 1] : 0;
  const pnlClass = last_pnl >= 0 ? "green" : "red";

  // Signal
  let signalHtml = `<span class="signal-dir signal-none">— NO TRADE</span>`;
  if (track.signal) {
    const dir = track.signal.direction === "UP" ? "▲ UP" : "▼ DN";
    const cls = track.signal.direction === "UP" ? "signal-up" : "signal-down";
    signalHtml = `
      <span class="signal-dir ${cls}">${dir}</span>
      <span class="signal-meta">${track.signal.regime || ""} · ${(track.signal.confidence * 100).toFixed(0)}% conf</span>`;
  }

  // Open position
  let posHtml = `<span class="dim">No open position</span>`;
  if (track.open_position) {
    const p = track.open_position;
    const pnlCls = p.unrealized_pnl >= 0 ? "green" : "red";
    const dirCls = p.direction === "UP" ? "trade-up" : "trade-dn";
    const dirArrow = p.direction === "UP" ? "▲ UP" : "▼ DN";
    const sl_distance = p.current_price - p.sl;
    const tp_distance = p.tp - p.current_price;
    const range = p.tp - p.sl;
    const nearSl = range > 0 && sl_distance / range < 0.1;
    const nearTp = range > 0 && tp_distance / range < 0.1;
    posHtml = `
      <div class="pos-row">
        <span class="${dirCls} pos-dir">${dirArrow} @ ${p.entry_price.toFixed(3)}</span>
        <span class="${pnlCls}">${fmtPnl(p.unrealized_pnl)}</span>
      </div>
      <div class="pos-row">
        <span class="dim">Now: ${p.current_price.toFixed(3)}</span>
        <span class="dim">Held: ${p.held_seconds}s</span>
      </div>
      <div class="pos-tp-sl">
        <div class="pos-tp ${nearTp ? 'pos-warn-tp' : ''}">TP ${p.tp.toFixed(3)}</div>
        <div class="pos-sl ${nearSl ? 'pos-warn-sl' : ''}">SL ${p.sl.toFixed(3)}</div>
      </div>`;
  }

  // Recent trades
  let tradesHtml = `<span class="dim">No trades yet</span>`;
  if (track.recent_trades && track.recent_trades.length > 0) {
    tradesHtml = track.recent_trades.map(t => {
      const cls = t.pnl >= 0 ? "trade-up" : "trade-dn";
      const arr = t.direction === "UP" ? "▲" : "▼";
      return `<div class="trade-row"><span class="${cls}">${arr} ${t.direction}</span><span class="trade-reason">${t.exit_reason}</span><span class="${cls}">${fmtPnl(t.pnl)}</span></div>`;
    }).join("");
  }

  // Win stats
  const ws = track.win_stats || {};
  const pf = ws.profit_factor !== null && ws.profit_factor !== undefined ? ws.profit_factor.toFixed(1) + "x" : "—";

  return `
<div class="panel" style="--track-color: ${color}; border-top-color: ${color};">
  <div class="panel-header">
    <span class="panel-title">${label}</span>
    <span class="status-badge status-${track.status || 'shadow'}">${(track.status || 'shadow').toUpperCase()}</span>
  </div>

  <!-- Sparkline -->
  <div class="sparkline-container">
    <svg viewBox="0 0 200 44" preserveAspectRatio="none">${buildSparkline(track.pnl_history)}</svg>
    <span class="sparkline-pnl ${pnlClass}">${fmtPnl(last_pnl)}</span>
  </div>

  <!-- Signal -->
  <div class="section">
    <div class="section-label">Signal</div>
    <div class="signal-row">${signalHtml}</div>
  </div>

  <!-- Engine Scores (A/B/C) — passed in from top-level engine_scores -->
  <div class="section">
    <div class="section-label">Engine Scores</div>
    ${["A","B","C"].map(k => {
      const val = (engineScores[k] || 0);
      return `<div class="engine-row">
        <span class="engine-label">${k}</span>
        <div class="engine-bar-bg"><div class="engine-bar-fill" style="width:${(val*100).toFixed(0)}%"></div></div>
        <span class="engine-value">${val.toFixed(2)}</span>
      </div>`;
    }).join("")}
  </div>

  <!-- Open Position -->
  <div class="section">
    <div class="section-label">Open Position</div>
    ${posHtml}
  </div>

  <!-- Win Stats -->
  <div class="stats-grid" style="margin-bottom:8px;">
    <div class="stat-cell">
      <div class="stat-value ${(ws.win_rate||0) >= 0.5 ? 'green' : 'red'}">${((ws.win_rate||0)*100).toFixed(0)}%</div>
      <div class="stat-label">Win Rate</div>
    </div>
    <div class="stat-cell">
      <div class="stat-value">${ws.trades || 0}</div>
      <div class="stat-label">Trades</div>
    </div>
    <div class="stat-cell">
      <div class="stat-value green">${pf}</div>
      <div class="stat-label">Prof. Factor</div>
    </div>
  </div>

  <!-- Recent Trades -->
  <div class="section-label">Recent Trades</div>
  ${tradesHtml}
</div>`;
}

async function fetchStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.status) return;
    const s = data.status;

    // Update status bar
    const dir = s.btc_direction === "up" ? "▲" : s.btc_direction === "down" ? "▼" : "—";
    document.getElementById("btc-price").textContent = s.btc_price ? `${dir} $${s.btc_price.toLocaleString()}` : "—";
    document.getElementById("window-countdown").textContent = s.window_seconds_remaining > 0 ? `${Math.floor(s.window_seconds_remaining/60)}m ${s.window_seconds_remaining%60}s` : "—";
    document.getElementById("next-window").textContent = s.next_window_seconds > 0 ? `in ${s.next_window_seconds}s` : "—";
    const modeBadge = document.getElementById("mode-badge");
    if (s.mode === "LIVE") {
      modeBadge.innerHTML = `<span class="badge badge-live">LIVE · ${s.active_track || ""}</span>`;
    } else {
      modeBadge.innerHTML = `<span class="badge badge-dry">DRY RUN</span>`;
    }
    const totalPnl = document.getElementById("total-pnl");
    totalPnl.textContent = fmtPnl(s.total_pnl);
    totalPnl.className = "value " + ((s.total_pnl || 0) >= 0 ? "green" : "red");

    // Render panels
    const panels = document.getElementById("panels");
    const engineScores = s.engine_scores || {};
    if (s.tracks && s.tracks.length > 0) {
      panels.innerHTML = s.tracks.map(t => renderTrack(t, engineScores)).join("");
    }
  } catch (e) {
    console.warn("Status fetch failed:", e);
  }
}

// Engine scores section — rendered globally in status bar (same for all panels)
// (Add to panels in a second pass or render inside each panel from top-level engine_scores)

fetchStatus();
setInterval(fetchStatus, 2000);
</script>
</body>
</html>
```

- [ ] **Step 2: Verify dashboard loads**

Start the webapp and open `http://localhost:8787`:
```bash
cd . && python -m webapp.server &
sleep 2
curl -s http://localhost:8787 | head -5
```
Expected: Returns HTML with `<!DOCTYPE html>`.

- [ ] **Step 3: Verify `/api/status` returns the new schema**

With a running bot (or using the mock status file):
```bash
curl -s http://localhost:8787/api/status | python -m json.tool | head -30
```
Expected: JSON with `btc_price`, `engine_scores`, `tracks` array.

- [ ] **Step 4: Commit**

```bash
git add webapp/index.html
git commit -m "feat: rebuild dashboard as 3-column panel layout with SVG sparklines"
```

---

## Task 14: Window boundary log markers

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add window boundary marker logic to the main loop**

In `trading_loop()`, track the previous window's `condition_id`. When it changes (new window), print the separator line before fan-out:

```python
_last_condition_id: str | None = None

# Inside the loop, after finding window:
if window.condition_id != _last_condition_id:
    _last_condition_id = window.condition_id
    ts_str = time.strftime("%H:%M:%S")
    price_str = f"BTC ${shared_state.btc_price:,.0f}" if shared_state.btc_price else "BTC —"
    secs = window.seconds_remaining()
    mins, secs_r = divmod(secs, 60)
    bar = f"── {ts_str} ──── WINDOW {time.strftime('%Y%m%d-%H:%M', time.localtime(window.end_ts - 300))} | {price_str} | {mins}m {secs_r:02d}s "
    bar = bar.ljust(90, "─")
    print(bar, flush=True)
```

- [ ] **Step 2: Run smoke test and verify marker appears**

```bash
cd . && timeout 20 python bot.py --demo --once 2>&1 | grep "WINDOW"
```
Expected: At least one `── WINDOW ...` line printed.

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: print window boundary markers in bot log output"
```

---

## Task 15: Full test suite + cleanup

**Files:**
- Modify: `tests/test_bot_models.py` (remove old `ModelRuntime` references)

- [ ] **Step 1: Update `test_build_model_runtimes_clones_initial_window`**

Find this test (around line 50). If `build_model_runtimes` has been deleted, either remove this test or rename it to test `build_model_tracks`. The equivalent assertion is that each track gets its own cloned initial window, not a shared reference.

- [ ] **Step 2: Run the full test suite**

```bash
cd . && python -m pytest tests/ -v 2>&1 | tail -40
```
Expected: All tests pass. Fix any remaining failures.

- [ ] **Step 3: Run existing bot tests specifically**

```bash
cd . && python -m pytest tests/test_bot_models.py tests/test_risk.py tests/test_orderer.py tests/test_track.py -v
```
Expected: All pass.

- [ ] **Step 4: Final commit**

```bash
git add tests/test_bot_models.py
git commit -m "test: update test suite for ModelTrack, finalize regime filter implementation"
```

---

## Completion Checklist

- [ ] All tests pass: `python -m pytest tests/ -v`
- [ ] Bot runs in demo mode: `timeout 30 python bot.py --demo`
- [ ] Three colored track prefixes appear in logs
- [ ] Window boundary markers appear in logs
- [ ] CONVICTION_ONLY only logs signals when regime is `LATE_CONVICTION`
- [ ] MISPRICING_ONLY only logs signals when regime is `MISPRICING`
- [ ] Dashboard at `http://localhost:8787` shows 3 columns
- [ ] Each panel updates live (2s poll)
- [ ] Cooldown on one track does not affect others
- [ ] Positions can exit during cooldown (exits bypass `can_enter`)
