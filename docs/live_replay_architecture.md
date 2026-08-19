# Live / Replay Architecture

This document defines the target operating model for the bot.

## Goal

Use one shared engine core for both:

- live trading before and during market hours
- after-close replay and simulation from saved raw data

The live path must be incremental and stateful.
The replay path must reconstruct the same state without IB.

## Core Principle

The rule engine should not be re-run from scratch for every symbol and day.
Instead:

1. pre-open, load the historical context needed for each asset
2. at market open, ingest only the new 5-minute bars
3. evaluate the day from the current state
4. after close, persist raw data and replay the same logic offline

## Runtime Modes

### `live`

Used before open and intraday.

Responsibilities:

- bootstrap state for all basket assets
- ingest new bars as they appear
- update regime, swing levels, open trades, and gating state
- produce trade decisions for the current day
- send approved orders to IB

### `replay`

Used after market close.

Responsibilities:

- read stored raw data
- reconstruct the same per-asset state
- run the same decision logic without IB
- compare output against live logs or previous runs

## Shared Engine State

Each asset needs its own persisted state object.

Suggested state contents:

- current regime
- last confirmed swing levels
- broken support / resistance context
- open trades
- exit and cooldown state
- learn-long gating state
- last processed bar timestamp
- last decision timestamp

## Proposed Modules

### `src/state`

Holds the serializable state model for one asset.

Suggested files:

- `engine_state.py`
- `state_store.py`

### `src/live`

Live trading orchestration.

Suggested files:

- `preopen_bootstrap.py`
- `bar_ingestor.py`
- `live_runner.py`

### `src/replay`

After-close simulation and regression.

Suggested files:

- `replay_runner.py`
- `replay_loader.py`
- `replay_compare.py`

### `src/store`

Persistence helpers for:

- raw bars
- engine snapshots
- trade decisions
- fills
- comparison artifacts

## Live Flow

1. Load basket version and asset list.
2. Restore or initialize one engine state per asset.
3. Bootstrap state from historical data up to the previous close.
4. Wait for the first market bars.
5. Ingest only the new bars for the current session.
6. Evaluate the engine for the day.
7. Pass trade intents into money management.
8. Send approved orders via IB.
9. Persist state after each relevant event.

## Replay Flow

1. Load the same basket version.
2. Read the stored raw bar files.
3. Rebuild the per-asset state from the same input sequence.
4. Run the same decision logic in the same order.
5. Compare signals, trades, and state transitions with the live run.

## No-Lookahead Rules

- Only completed information may enter the state before the decision timestamp.
- The first 5-minute bar is only usable once it is actually available.
- The day decision must use the same cutoff in live and replay.
- After-close ingestion may not change any decision already made intraday.

## Open Design Questions

- Should the pre-open bootstrap use one daily summary plus the minimal intraday history, or the full retained intraday context?
- Should the state store be JSON, parquet, or SQLite?
- Should live and replay share the same engine object, or only the same pure state transition functions?
- Which timestamp should be the canonical "decision ready" marker: first 5-minute bar close or second 5-minute bar close?

## Recommended Implementation Order

1. define `EngineState`
2. define serialization for state snapshots
3. build a replay-only runner from stored bars
4. build a live bootstrap path
5. connect incremental bar ingestion
6. compare live vs replay on a known day

