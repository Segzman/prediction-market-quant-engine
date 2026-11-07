# bot.py — Spec

## Purpose

Entry point and async orchestrator ONLY. Zero business logic here.
Wire everything together, call `asyncio.gather`, handle shutdown.

## CLI

```
python bot.py           # DRY_RUN mode (from .env, default True)
python bot.py --live    # Override DRY_RUN=False (adds an extra confirmation prompt)
python bot.py --model B # Use Brownian motion model instead of default A
python bot.py --debug   # Set log level to DEBUG
```

## Startup Sequence

```python
async def main():
    # 1. Parse CLI args
    # 2. validate_config()
    # 3. If --live flag: print warning and require user to type "YES" to confirm
    # 4. Initialize all components:
    state   = BotState()
    ledger  = Ledger()
    client  = PolymarketClient(...)
    finder  = MarketFinder(client)
    orderer = Orderer(client, dry_run=DRY_RUN)
    guard   = DailyGuard(state)
    feed    = BinanceFeed(state)
    # 5. Print startup banner with all config values
    # 6. asyncio.gather(feed.connect(), trading_loop(...))
```

## trading_loop() — Full Logic

```python
async def trading_loop(state, finder, orderer, guard, ledger, model):
    while True:
        # 1. Daily reset check (guard handles this)
        
        # 2. Refresh window if expired
        if no current window or current window expired:
            state.current_window = finder.find_active_window()
            if none found: sleep 10s, continue

        window = state.current_window
        now = int(time.time())
        phase = window.get_phase(now)

        # 3. Handle each phase
        if phase == WAITING:
            window.set_open_price(state.btc_price)
            sleep 5s
            continue

        if phase in (TOO_LATE, EXPIRED):
            # If we had an open order, cancel it
            if window.order_id and not window.traded:
                orderer.cancel(window.order_id)
            sleep until window.end_ts + 1
            continue

        # phase == ENTRY_WINDOW
        
        # 4. Check if we can trade
        can, reason = guard.can_trade()
        if not can:
            log.info(f"Skipping: {reason}")
            sleep 5s
            continue

        # 5. Get market prices
        up_price   = orderer.get_best_ask(window.up_token_id)
        down_price = orderer.get_best_ask(window.down_token_id)
        if either is None:
            sleep 5s
            continue

        # 6. Evaluate signal
        signal = evaluate(
            window, state.btc_price, window.seconds_remaining(now),
            up_price, down_price, model=model
        )
        if signal is None:
            log.info(f"No signal | {window.seconds_remaining(now)}s left | BTC ${state.btc_price:,.2f}")
            sleep 5s
            continue

        # 7. Size the bet
        bet_size = get_bet_size(signal.edge, signal.market_price, bankroll=MAX_BET_USDC * 10)

        # 8. Place order
        token_id = window.up_token_id if signal.direction == UP else window.down_token_id
        result = orderer.place_bet(token_id, signal.direction, bet_size, signal.market_price)

        # 9. Record trade
        ledger.record_trade(
            window_end_ts=window.end_ts,
            direction=signal.direction.value,
            amount_usdc=bet_size,
            price=signal.market_price,
            edge=signal.edge,
            ev=signal.ev,
            model=signal.model_used,
            order_id=result.order_id,
            dry_run=DRY_RUN,
            market_url=window.market_url,
        )
        state.record_trade(...)

        window.traded = True
        window.order_id = result.order_id
        state.last_trade_ts = time.time()

        # 10. Wait for fill (async, with deadline = window close)
        filled = await orderer.wait_for_fill(result.order_id, deadline_ts=window.end_ts)

        # 11. Record resolution (after window closes)
        await asyncio.sleep(max(0, window.end_ts - int(time.time()) + 2))
        # ... determine if window resolved UP (compare open_price vs chainlink/final)
        # For now: use state.btc_price as proxy
        resolved_up = state.btc_price >= window.open_price
        ledger.record_resolution(result.order_id, window.end_ts, resolved_up, ...)
        state.record_resolution(result.order_id, resolved_up)

        # Log daily summary
        summary = ledger.get_daily_summary()
        log.info(f"Daily: {summary['wins']}W/{summary['losses']}L | PnL: ${summary['total_pnl']:.2f}")
```

## Startup Banner

```
═══════════════════════════════════════════════════
  Polymarket BTC 5-Min Bot
  Mode:         DRY RUN ← print in yellow, or LIVE in red
  Model:        A (Momentum Squeeze)
  Max bet:      $5.00 USDC
  Daily stop:   $20.00 USDC
  Edge req:     4¢
  Entry window: 90s–15s before close
═══════════════════════════════════════════════════
```
