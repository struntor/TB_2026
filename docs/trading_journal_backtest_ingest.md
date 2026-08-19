# Backtest ingestion

`src/journal/backtest_ingest.py` stores one backtest as a normalized journal
run. It is deliberately independent of every strategy package: the strategy
produces `DecisionRecord` objects and a completed-trades DataFrame; the journal
only consumes those public values.

## Required identity

Before ingestion, enrich the trade frame in the strategy project with its
existing helper:

```python
trades = decision_records.attach_record_keys_to_trades(trades, records)
```

Then pass the enriched frame to the journal. The journal requires a
`signal_key` on every completed trade and resolves that key against an accepted
decision. It never associates decisions and trades by DataFrame row position.
An unknown key or a duplicate accepted key fails the run; the transaction
leaves no partial decisions or trades behind.

## Example

```python
from src.journal import JournalStore
from src.journal.backtest_ingest import (
    BacktestJournalIngestor,
    BacktestRunSpec,
    BacktestTradeDefaults,
)

spec = BacktestRunSpec(
    run_id="manual-regime-spx-2024-2026-config-abc",
    strategy_id="manual-regime",
    strategy_version="rules-v1",
    config_hash="config-abc",
    environment="simulation",
    started_at_utc="2026-07-15T08:00:00Z",
    parameters={"start": "2024-01-01", "end": "2026-07-01"},
    metadata={"data_revision": "bars-2026-07-14"},
    trade_defaults=BacktestTradeDefaults(
        symbol="SPX",
        asset_class="IND",
        currency="USD",
        exchange="CBOE",
        naive_timestamp_timezone="America/New_York",
    ),
)

with JournalStore() as store:
    result = BacktestJournalIngestor(store).ingest_run(
        spec,
        records,
        trades,
        completed_at_utc="2026-07-15T08:05:00Z",
    )
```

`started_at_utc` and `completed_at_utc` are the execution times of the backtest
run. `entry_date` and `exit_date` are market-event times. If those DataFrame
columns contain timezone-naive timestamps, `naive_timestamp_timezone` is
mandatory; the journal converts them to UTC. Set it to `None` only when the
frame already contains aware timestamps.

## Trade fields

The standard manual-regime frame works without renaming:

- `entry_date` / `exit_date` become UTC open/close timestamps.
- `entry` / `exit` become trade and single-leg prices.
- `quantity` or `qty` is preferred as the quantity. If neither exists, `size`
  is used and `metadata.quantity_source` records that its semantics are a
  strategy sizing value rather than guaranteed broker units.
- `realized_pnl`, `pnl`, or `profit_loss` populates currency PnL.
- `points` and `weighted_points` remain distinct metadata. They are never
  silently presented as currency PnL.
- `exit_reason`, the full normalized source row, and all additional strategy
  columns are retained in metadata.
- A deterministic single-asset leg stores entry, exit, quantity and exchange.

The trade id is deterministic from `(run_id, signal_key)`, so replay is
idempotent. Backtest and paper/live trades for the same causal signal have
different trade ids because their run ids differ.

## Explicit reconciliation

`reconcile_backtest_to_execution()` links one backtest trade and one paper/live
trade with the same `signal_key`. It records entry-price slippage, quantity and
currency-PnL differences as `execution minus backtest`. Missing values stay
`NULL`; they are not treated as zero.

## Verification

```powershell
python -m unittest -v test_backtest_ingest
```
