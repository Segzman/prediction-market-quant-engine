from __future__ import annotations

import argparse
from dataclasses import dataclass

from config import TARGET_ASSET


@dataclass
class CliArgs:
    live: bool
    shadow_live: bool
    asset: str
    model: str
    models: tuple[str, ...]
    yes_live: bool
    debug: bool
    demo: bool
    demo_local: bool
    once: bool
    duration: int | None
    dashboard: bool | None
    bankroll: float | None


def parse_args() -> CliArgs:
    parser = argparse.ArgumentParser()
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true")
    mode_group.add_argument(
        "--shadow-live",
        action="store_true",
        help="Use real Binance and Polymarket public data with dry-run orders",
    )
    mode_group.add_argument(
        "--demo",
        action="store_true",
        help="Run a bounded dry-run demo that prefers live public data and falls back to local simulation",
    )
    mode_group.add_argument(
        "--demo-local",
        action="store_true",
        help="Force local simulated prices and demo markets for a fast dry-run walkthrough",
    )
    parser.add_argument("--model", default="A", choices=["A", "B", "C"])
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model list to run side by side, for example A,B,C",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Skip the interactive live trading confirmation prompt",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run a single trading iteration")
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Maximum runtime in seconds before stopping cleanly",
    )
    parser.add_argument(
        "--asset",
        default=TARGET_ASSET,
        help="Target asset symbol, for example BTC, ETH, or POL",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Starting bankroll per model in USDC",
    )
    dashboard_group = parser.add_mutually_exclusive_group()
    dashboard_group.add_argument(
        "--dashboard",
        dest="dashboard",
        action="store_true",
        help="Show a live terminal dashboard while the bot runs",
    )
    dashboard_group.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Disable the live terminal dashboard",
    )
    parser.set_defaults(dashboard=None)
    parsed = parser.parse_args()
    try:
        models = parse_model_names(parsed.model, parsed.models)
    except ValueError as exc:
        parser.error(str(exc))
    return CliArgs(
        live=parsed.live,
        shadow_live=parsed.shadow_live,
        asset=parsed.asset.upper(),
        model=parsed.model,
        models=models,
        yes_live=parsed.yes_live,
        debug=parsed.debug,
        demo=parsed.demo,
        demo_local=parsed.demo_local,
        once=parsed.once,
        duration=parsed.duration,
        dashboard=parsed.dashboard,
        bankroll=parsed.bankroll,
    )


def parse_model_names(default_model: str, raw_models: str | None) -> tuple[str, ...]:
    if raw_models is None:
        return (default_model.upper(),)

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_models.split(","):
        name = item.strip().upper()
        if not name:
            continue
        if name not in {"A", "B", "C"}:
            raise ValueError(f"Unsupported model {name}. Supported models: A, B, C")
        if name in seen:
            continue
        seen.add(name)
        models.append(name)

    if not models:
        raise ValueError("No valid models supplied. Use --models A,B,C")
    return tuple(models)
