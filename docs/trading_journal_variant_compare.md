# Reproducible strategy-variant comparison

`src.journal.variant_compare` compares two completed backtest runs without
changing the journal database. It is intended for old/new strategy variants,
not for paper/live reconciliation.

## Required provenance

Every compared run must provide the following values. Put run-specific values
under `parameters.comparison_provenance`; immutable variant values may come
from the linked `strategy_variants` row.

```json
{
  "comparison_provenance": {
    "period_start": "2025-01-01T00:00:00Z",
    "period_end": "2025-12-31T23:59:59Z",
    "starting_capital": 100000,
    "capital_currency": "USD"
  }
}
```

The linked variant supplies:

- `data_revision`
- `fee_model`
- `slippage_model`
- `money_management_version`

The comparator fails closed when any required value is missing or differs.
`--allow-incompatible` permits an explicitly marked exploratory comparison for
data/cost/slippage/MM differences. It cannot invent a common period, starting
capital, or currency: those must still match. Every override and incompatibility
is included in the result.

Only trades with `closed_at_utc` are included in the equity curve. A closed
trade without a finite `realized_pnl` is an error rather than being silently
discarded. Trade currency must match `capital_currency`.

## Output

The JSON result contains:

- old/new equity curves from the identical starting capital
- P&L, return, win rate and absolute/percentage maximum drawdown
- signal-key membership: `old_only`, `new_only`, or `common`
- old/new and delta values for entry, exit, P&L and quantity
- P&L contribution grouped by stable `rule_id` and immutable
  `rule_version_id`
- turnover and time-weighted entry-notional exposure

Turnover and exposure are never estimated from incomplete data. Their result is
`available=false` with a reason when price, quantity, holding interval or a
non-stock contract multiplier is missing. Exposure is explicitly an
entry-notional proxy; it is not a mark-to-market or margin exposure series.

## CLI

```powershell
python -m src.journal.variant_compare `
  --database D:\Trading\journal\trading_journal.sqlite `
  --old-run-id backtest:baseline `
  --new-run-id backtest:candidate `
  --output D:\Trading\journal\comparisons\baseline_candidate.json
```

The database connection is opened with SQLite `mode=ro`. Omitting `--output`
prints the JSON report. Use `--allow-incompatible` only when the reported
provenance differences are intentionally accepted.

For older journal schemas without variant/rule identity columns, the reader
falls back to run provenance and `primary_rule`/`strategy_version`; such output
is less precise and is marked with fallback identifiers such as
`__unattributed__` when no decision can be linked.
