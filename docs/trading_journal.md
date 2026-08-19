# Trading journal

The backtest DataFrame adapter, run lifecycle, and explicit backtest-to-live
reconciliation are documented in
[`trading_journal_backtest_ingest.md`](trading_journal_backtest_ingest.md).
Reproducible old/new backtest analysis is documented in
[`trading_journal_variant_compare.md`](trading_journal_variant_compare.md).
Practical month/year equity reports and backtest-to-paper/live comparisons are
documented in
[`trading_journal_reports.md`](trading_journal_reports.md).

The journal is the durable audit trail between a strategy decision and the
broker result. It uses only Python's `sqlite3` module and defaults to:

```text
D:\Trading\journal\trading_journal.sqlite
```

Constructing `JournalStore()` creates the parent directory, enables foreign
keys, configures WAL and a busy timeout, and applies pending schema migrations.
All timestamps are normalized to ISO-8601 UTC strings ending in `Z`. JSON is
canonical, strict JSON; `NaN`, infinity and unsupported Python objects are
rejected before a transaction is committed.

## Data model

| Table | Purpose |
| --- | --- |
| `schema_migrations` | Applied schema versions |
| `strategy_runs` | One backtest, paper or live run and its immutable identity |
| `rules` | Stable business rule IDs, independent of implementation versions |
| `rule_versions` | Immutable SemVer, logic/code hash and engine provenance per rule |
| `strategy_variants` | Immutable engine, money-management, data and cost provenance |
| `strategy_variant_rules` | Ordered rule-version composition of a strategy variant |
| `signal_decisions` | Final/current structured decision per run and signal |
| `decision_factors` | Ordered 1:n rule, filter, veto, context and risk factors |
| `trades` | A trade or multi-leg strategy instance |
| `trade_legs` | Stock, option, future or combo legs without flattening |
| `orders` / `order_legs` | Broker orders and combo-leg routing |
| `order_status_events` | Idempotent broker status history |
| `executions` | Partial and complete fills |
| `commissions` | Broker commission reports associated with executions |
| `trade_events` | Trade/order lifecycle events |
| `risk_events` | Approvals, blockers, sizing and portfolio-risk events |
| `reconciliation` | Live/paper trades matched to reference backtest trades |

`UNIQUE(run_id, signal_key)` is enforced for decisions and trades. The same
causal signal key can therefore occur once in a backtest run and once in a
paper/live run, while their concrete decision and trade IDs remain different.
Option spreads and combinations are represented by one trade with ordered
`trade_legs`; no option semantics are packed into a stock-trade row.

## Identity and idempotency

Every write DTO carries an external ID. Adapters should create it with
`deterministic_id(kind, *immutable_identity_parts)`. Replaying the same broker
snapshot or engine output updates the same row rather than appending a copy.
Reusing an ID for another run or signal raises `IdentityConflictError`.
The immutable run identity includes strategy ID/version, configuration hash,
run type, environment and start time. Status, end time and metadata may be
updated without changing that identity.

For IBKR orders the adapter stores the account, permanent ID, client ID and
client order ID as queryable columns. Unique indexes protect permanent broker
identity and the client-session fallback identity. Raw broker fields remain in
`metadata_json` as well. A previously stored `live_final` decision cannot be
downgraded by a later replay of an older engine-stage record.

## Rule and strategy version registry

Schema version 2 separates stable business identity from implementation
identity. A `rule_id` such as `entry.crash_repair_long` survives implementation
changes. Each implementation is registered separately with a SemVer, logic
hash, code hash and engine version. A recommended deterministic ID is:

```python
deterministic_id("rule-version", rule_id, semver)
```

Do not reuse a rule version after changing conditions, parameters or code;
register a new SemVer. Strategy variants also use immutable IDs, never mutable
aliases such as `current`. The initial manual-regime reference is:

```text
variant_id:               manual-regime-baseline@1.0.0
engine_version:           manual-regime-engine@1.0.0
money_management_version: fixed-risk-per-trade@1.0.0
```

The current Paper baseline keeps the V2 portfolio/risk policy and registers the
revised failed-countermove stop as a new immutable variant:

```text
variant_id:               manual-regime-baseline@3.0.0
parent variant:           manual-regime-baseline@2.0.0
engine_version:           manual-regime-engine@1.1.0
money_management_version: graded-structure-volume-sizing@1.0.0
changed rule version:     bearish_failed_countermove_short_baseline@1.1.0
```

A variant fixes its ordered rule-version composition together with config hash,
data revision, fee model and slippage model. Re-registering the same ID with
changed provenance or membership raises `IdentityConflictError`. For a valid
old/new equity comparison, create two backtest runs with distinct `variant_id`
values but the same period, input-data revision, capital, fee and slippage
assumptions.

```python
from src.journal import (
    RuleDefinitionRecord,
    RuleVersionRecord,
    StrategyVariantRecord,
    StrategyVariantRuleRecord,
)

journal.register_rule(rule)
journal.register_rule_version(rule_version)
journal.register_strategy_variant(variant, [variant_rule])
```

`StrategyRunRecord.variant_id` optionally binds a run to the registered
variant. `SignalDecisionRecord.rule_id` and `rule_version_id` optionally bind a
decision to the exact rule implementation and must be supplied together.
Legacy schema-v1 rows keep these columns as `NULL`. Opening an existing v1
database applies the migration transactionally without rewriting trades,
orders, fills or decisions.

On reconnect, account plus IBKR `permId` resolves the existing order together
with its original run/trade/signal binding. A multi-day GTC resync therefore
adds status, fills and commissions to the originating order instead of moving
it into the current daily run. Unchanged status snapshots have a stable event
identity even when observed later; broker `trade.log` entries include their
broker timestamp and remain distinct.

Status/history events are append-only in meaning but use deterministic event
IDs, so reconnect delivery of the same event is harmless. A genuinely later
status needs a different event identity.

## API

Typed DTOs live in `src.journal.models`; the persistence boundary is
`src.journal.store.JournalStore`:

```python
from src.journal.models import StrategyRunRecord
from src.journal.store import JournalStore

with JournalStore() as journal:
    journal.start_run(
        StrategyRunRecord(
            run_id="paper:2026-07-15:manual-regime",
            strategy_id="manual-regime",
            strategy_version="v1",
            config_hash="...",
            run_type="paper",
            environment="IBKR-paper",
            started_at_utc="2026-07-15T09:30:00Z",
            ended_at_utc=None,
            status="running",
            parameters={},
            metadata={},
        )
    )
```

The write methods are:

- `start_run`
- `upsert_decision(record, factors)`
- `create_trade(record, legs)`
- `upsert_order(record, legs)`
- `add_order_status`
- `upsert_execution`
- `upsert_commission`
- `add_event`
- `add_risk_event`
- `upsert_reconciliation`

Each method is atomic. Several calls can be grouped with
`with journal.transaction():`; nested store calls join the outer transaction,
and any exception rolls the entire group back.

For `create_trade` and `upsert_order`, omitting `legs` (`None`) preserves the
stored leg set. Passing an explicit empty tuple clears it. This distinction
prevents scalar status updates from deleting option/combo structure.

## Operational notes

### Read-only TWS resynchronization

The live bot observes the first few seconds after bracket submission. Later
GTC take-profit/stop fills and delayed commission reports are imported with:

```powershell
python -m src.journal.tws_sync `
  --host 127.0.0.1 `
  --port 7496 `
  --client-id 63 `
  --account DU1234567
```

The command is strictly read-only: it requests all open orders, completed
orders with `apiOnly=False`, executions, and commission reports. It never calls
`placeOrder` or `cancelOrder`. By default it refuses an account whose name does
not start with `DU`; `--allow-live-account` is required to sync a live account.
Use `--journal-path` for a non-default database.

Run the sync once after the exchange close and once when the bot/TWS session
starts. Replays are idempotent. Account plus IBKR `permId` keeps a multi-day GTC
fill attached to its original strategy run, trade, and signal. Orders not yet
known to the journal, including manual TWS orders, are stored under a
deterministic daily `ibkr-broker-sync` run with no invented signal or trade ID.

### Model history for live risk rules

The manual-regime cluster veto reads its rolling two-calendar-day strategy state
from `src.journal.realized_history`; no historical trade CSV is used at runtime.
The state comes exclusively from one completed backtest/shadow run of the exact
strategy variant. Only closed model trades for the configured rule IDs with a
finite `realized_pnl` count. The New York window is start-inclusive and
end-exclusive: `[trading day - 2 calendar days, trading day)`.

Paper/live trades, TWS orders, executions, commissions, missed orders and
technical connection failures never replace, suppress or otherwise modify this
model history. They remain separate journal facts for reconciliation and broker
risk controls. This ensures that a broker outage cannot change tomorrow's
strategy decision.

The model run is selected by an explicit `model_run_id`, or by exactly one run
tagged `metadata.model_history_role=canonical_model_history`. A sole completed
backtest/shadow run is accepted as a compatibility fallback. Multiple ambiguous
runs fail closed; trades from different runs are never mixed. The selected run
must declare `period_start` and `period_end_exclusive`, and
`period_end_exclusive` must reach the trading day. Thus a missing morning
catch-up backtest fails closed instead of silently producing an empty loss
cluster.

The read-only TWS sync is still required for execution reconciliation, actual
positions and execution-quality analysis. It is not a source for strategy state.

- `reqAllOpenOrders()` discovers still-working children.
- `reqCompletedOrders(apiOnly=False)` includes manual and other-client orders.
- `reqExecutions()` lets ib_insync attach fills to trades by permanent ID.
- A short event-loop interval receives delayed `CommissionReport` messages.
- Broker snapshots with `totalQuantity == 0` are not valid journal orders. The
  read-only sync skips them and records observed, ingested and skipped counts in
  the sync-run metadata instead of aborting the whole reconciliation.
- Bound parent-entry executions promote a planned trade to `open`. Quantity,
  entry price and opening time are derived from the complete idempotent fill set;
  split fills therefore produce one actual net quantity and a fill-weighted VWAP.
  For a fully flattened single-leg stock trade, the sync also derives the exit
  VWAP, commissions, net realized P&L and `closed` lifecycle state. Generic
  multi-leg option lifecycle closure remains strategy-specific.

- Create a new `run_id` for each concrete backtest, paper or live session.
- Keep `signal_key` causal and stable across those runs. Do not include fills,
  slippage or P&L in it.
- Create one trade with multiple legs for an option combination.
- Record planned risk before routing, then order statuses, executions and
  commissions as separate facts.
- Match live/paper and backtest trades explicitly through `reconciliation`.
- Back up a live WAL database with SQLite's backup API or after a clean close;
  copying only the main file while `-wal` is active can omit recent commits.

The component does not yet schedule retention, encrypt the database, or import
old text logs. The 24/7 supervisor schedules the read-only TWS sync; standalone
deployments remain responsible for invoking it.
