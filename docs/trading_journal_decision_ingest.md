# DecisionRecord ingestion

`src/journal/decision_ingest.py` is the boundary between strategy engines and the
normalized trading journal. It does not parse human log lines and does not alter
strategy, sizing, backtest, or order-routing semantics.

## Identity and run modes

- A strategy run is started in `JournalStore` before ingestion.
- `DecisionIngestContext.run_mode` is explicitly one of `backtest`, `paper`, or
  `live`.
- A decision id is deterministic from `(run_id, signal_key)`. Re-ingesting the
  same record updates that row and replaces its factor set.
- A trade id is deterministic from `(run_id, signal_key)`. Consequently a live
  and backtest trade have different trade ids even when they represent the same
  causal signal.
- Cross-run comparison and reconciliation use `signal_key`, never `trade_id`.

An engine-stage accepted decision can therefore be finalized by a live-stage
veto without creating a duplicate decision. Statuses accepted by the adapter are
`accepted`, `vetoed`, `blocked`, `no_setup`, `blocked_risk`,
`duplicate_exposure`, `missing_data`, and `execution_blocked`.

## Lossless mapping

`map_decision_record()` maps all typed scalar fields, the complete structured
factor sequence, and `context`. The complete source payload is also stored in
`raw_record_json`; this preserves schema/version fields and future additive
fields without flattening them. JSON values are validated before persistence.

## Trade intents and option legs

`DecisionJournalIngestor.create_trade_intent()` only accepts an `accepted` record
whose action is `trade`. It first upserts the decision and then persists the
trade intent atomically through the store methods.

`TradeIntent` supports a single asset or a multi-leg option structure:

- When an asset intent has no explicit legs, one deterministic `single_asset`
  leg is synthesized. A trade never exists without at least one leg.
- An `OPT` intent must supply at least one explicit option leg. Option legs must
  contain expiry, finite strike, call/put right, positive finite multiplier and
  at least one durable contract identity (`con_id` or `local_symbol`). They are
  never replaced by an incomplete synthetic leg.
- A `BAG` intent requires at least two explicit `TradeIntentLeg` instances.
- Each option leg retains contract id, local symbol, expiry, strike, right,
  multiplier, ratio, quantity, side, exchange, price, and arbitrary metadata.
- Planned stop and target remain trade metadata. This avoids pretending that
  every option structure has a meaningful single-underlying stop price.
- Quantities, prices, strikes and multipliers reject `NaN` and positive or
  negative infinity before an SQLite transaction begins.

Money management remains responsible for supplying the final quantity and
prices. Broker order and fill ingestion is a separate downstream stage.

## Verification

Run the focused tests with:

```powershell
python -m unittest .\tests\test_journal_decision_ingest.py -v
```

The test covers lossless mapping, all main decision outcomes, repeated ingest,
live status finalization, single-asset leg generation, multi-leg option intents,
and backtest/live reconciliation by a shared `signal_key` with distinct trade
ids.
