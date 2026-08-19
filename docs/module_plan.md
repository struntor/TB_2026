# Module and Class Plan

This document defines the first implementation slice for the new bot project.
It keeps the rule engine separate from money management and broker execution.

## Top-Level Flow

1. Select basket
2. Build request for the decision day
3. Load market data via IB adapter
4. Evaluate rule engine for that day
5. Apply money management
6. Send approved orders through IB
7. Persist state, fills, and logs

## `src/bot`

### `TradingBot`

Main orchestrator for a single trading day.

Responsibilities:

- coordinate the daily workflow
- call basket selection
- request market data
- call the rule engine
- apply risk and sizing
- hand orders to execution
- record results

### `TradingSession`

Represents a single trading day or decision run.

Responsibilities:

- hold the current day
- hold the active basket
- store request and result objects
- carry run status

### `BotConfig`

Runtime settings for the bot.

Responsibilities:

- IB connection parameters
- basket selection parameters
- risk limits
- execution preferences
- logging and persistence paths

## `src/data`

### `MarketDataAdapter`

Adapter interface for market data retrieval.

Responsibilities:

- load bars for the requested basket/day
- optionally load quotes or snapshots
- ensure only data known at decision time is returned

### `IBMarketDataAdapter`

IB-backed implementation of the data adapter.

Responsibilities:

- connect to IB
- fetch historical or live bars
- normalize data into the engine format
- handle retries and missing data cases

### `BasketDataResolver`

Resolves the requested basket into actual symbols/contracts.

Responsibilities:

- map basket names to concrete assets
- resolve IB contracts
- return the instruments the bot must query

## `src/engine`

### `RuleEngineGateway`

Thin wrapper around the existing rule engine.

Responsibilities:

- accept loaded bars
- call the existing engine API
- return the day's signals and trades
- keep the engine broker-free

### `EngineCore`

Shared engine container with one `EngineState` per asset.

Responsibilities:

- keep one engine class for the whole bot
- hold separate state objects per symbol
- evaluate a single day or a basket
- support live and replay modes through the same core

### `TradeBatch`

Daily engine result.

Responsibilities:

- store the decision day
- store signals
- store concrete trade intents
- provide a normalized interface for downstream layers

## `src/risk`

### `RiskManager`

Determines whether and how a trade may be taken.

Responsibilities:

- position sizing
- per-trade risk
- daily loss cap
- max portfolio exposure
- basket concentration limits
- optional kill-switch logic

### `PositionSizer`

Translates trade intent into size.

Responsibilities:

- use stop distance and risk budget
- respect broker constraints
- round sizes to tradable units

### `RiskDecision`

Result of the risk evaluation.

Responsibilities:

- approved or rejected flag
- final size
- rejected reason
- risk metadata

## `src/execution`

### `ExecutionEngine`

Sends approved orders to IB.

Responsibilities:

- create orders from trade intents
- submit orders
- monitor order state
- handle partial fills
- collect execution reports

### `OrderFactory`

Builds broker-specific order objects.

Responsibilities:

- translate trade intent into IB order fields
- apply stop/target structure
- choose order type and time-in-force

### `ExecutionReport`

Final status of submitted orders.

Responsibilities:

- broker order ids
- fill quantity
- average price
- rejection or cancellation reason

## `src/models`

Shared data models used across all layers.

Suggested models:

- `BasketRequest`
- `MarketBar`
- `TradeIntent`
- `RiskDecision`
- `OrderIntent`
- `ExecutionReport`
- `FillEvent`
- `BotRunResult`

## `configs`

Configuration files for:

- IB connection settings
- basket definitions
- risk parameters
- logging paths
- runtime environment switches

## `tests`

First test areas:

- request construction
- basket resolution
- adapter normalization
- rule-engine call contract
- sizing logic
- order translation
- end-to-end dry-run orchestration

## Build Order

Recommended implementation sequence:

1. shared models
2. basket resolver
3. IB market data adapter
4. rule-engine gateway
5. risk manager
6. execution engine
7. bot orchestrator
8. tests and dry-run harness

## Non-Negotiable Constraints

- the rule engine must not know about IB
- money management must not be inside the rule engine
- data loading and order execution must stay separate
- every daily run must be restartable
- all decisions must be logged with the current day and basket
