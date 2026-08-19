# Test Trading Bot Start Guide

This document is the operational handoff for starting the current bot in the
configured test environment.

This setup is treated as an end-to-end test environment. The configured TWS/IB
Gateway target is the test/paper endpoint for this workflow, so `--place-orders`
is expected to be used when testing order handoff. In this environment, using
`--place-orders` does not require an additional confirmation step.

The current Paper baseline is `manual-regime-baseline@4.0.0`. It retains the V3
portfolio selection, entry and stop logic. V4 adds causal quarter-risk sizing
for selected `bearish_gap_breakdown_continuation_short` signals while retaining
the V3 quantity as a virtual parent shadow.

The current baseline runner is **not** the generic
`IBKR_TradingBot/src/bot/live_trade_probe.py` path. For the active stock/ETF
manual-regime baseline, use:

```text
D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py
```

This is the runner that was used for the successful SPY test/paper order route
on 2026-07-06.

## Correct Test Start

Start from `D:\PycharmProjects`:

```powershell
cd D:\PycharmProjects
python .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --place-orders --start-local 15:00 --decision-fetch-et 09:40 --risk-per-trade 100 --host 127.0.0.1 --port 7496 --client-id 62
```

Signal-only dry run, without sending orders:

```powershell
cd D:\PycharmProjects
python .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --start-local 15:00 --decision-fetch-et 09:40 --risk-per-trade 100 --host 127.0.0.1 --port 7496 --client-id 62
```

Important:

- `--place-orders` is required to test order transmission to the configured
  test/paper TWS endpoint.
- In this test environment, `--place-orders` may be set without additional
  confirmation.
- Without `--place-orders`, the bot only writes/logs the trade plan.
- Default test TWS port is `7496`.
- Use a different port only if TWS/IB Gateway is configured for it.
- Default client id for this runner is `62`.

## Timing

The intended operational flow is:

1. Start the script at or before `15:00` Europe/Berlin.
2. The bot waits until `15:00` Berlin if started earlier.
3. It connects to TWS.
4. It waits until `09:40` America/New_York, which is normally `15:40` Berlin during US/EU summer time overlap.
5. It fetches the first two completed US RTH 5-minute bars.
6. It builds a test/live overlay for the rule engine.
7. It calculates current baseline trades.
8. It filters to the active baseline rules.
9. For failed-countermove shorts, it refreshes the stop from information known
   at 09:40 ET and rejects the signal if the revised CRV is below 1.0.
10. It applies the V2 regime/volume position sizing to the revised stop.
11. It applies the V4 gap-breakdown quarter-risk overlay from completed daily
    sessions before the entry day.
12. It writes the trade plan.
13. If `--place-orders` is set, it sends market parent orders and OCA stop/target children to the configured test/paper TWS endpoint.

If the script is started after `15:00` Berlin, it does not fail only because the
start time has passed. It logs that the start time has passed and continues.

If the script is started after `09:40` New York, it fetches the first two bars
immediately. This is acceptable for a late/manual start, but it is not the
preferred scheduled test procedure.

## What This Bot Trades

This runner is for the current non-options manual-regime baseline only.

It does **not** trade the SPX options strategy and does **not** trade the
OptionOmega butterfly research path.

It loads:

- top-50 symbols from
  `PythonProject/statistic/spx_adx_di_5min_2024_02_03/current_baseline_optimized_cutoff_2025_11_top50_symbols.csv`
- active symbol/rule pairs from
  `PythonProject/statistic/spx_adx_di_5min_2024_02_03/cutoff_forward_top50_variants_live_history_548d_post_break_range_symbol_rule_selection.csv`
- model loss-cluster context from one completed, current backtest/shadow run in
  the trading journal (never from TWS executions or a historical CSV)

It calls:

```text
manual_regime_bot_api.get_trade_openings_for_symbols(...)
```

with:

- `history_lookback_days=548`
- `structure_lookback=10`
- `bull_continuation_mode=combo`
- `learn_long_mode=off`
- default cutoff variant `cutoff_2025-11_top50`

After engine output, it keeps only:

- `track == baseline`
- symbol/rule pairs marked as kept in the active cutoff variant
- post-filtered test/live baseline trades

## Order Routing

The runner routes only stock/ETF-style contracts.

Non-routable symbols in this runner:

```text
SPX
SPX_FUT
```

If those symbols generate a signal, they are skipped for order routing.

Order behavior:

- parent order: `MKT`, `DAY`
- side: long -> `BUY`, short -> `SELL`
- child stop: `STP`, `GTC`
- child target: `LMT`, `GTC`
- all three legs use the same account and deterministic `orderRef`
- parent and target are staged with `transmit=False`
- the attached stop is sent last with `transmit=True`
- TWS transmits the complete attached bracket and holds both children until the parent fills
- every leg must reach an accepted TWS status without an order error

The runner uses `src/execution/safe_bracket_order.py`. This removes the former
window in which a market parent could fill before stop and target were sent.
By default the runner accepts only a Paper account (`DU...`). A deliberately
configured non-Paper account additionally requires `--allow-live-account`.

Position sizing:

```text
quantity = floor((risk_per_trade * size_multiplier) / abs(entry - stop))
```

For `bearish_failed_countermove_short_baseline`, V3 first calculates:

```text
fresh_stop = max(
    V2 stop,
    prior-session 12:00-15:55 high + ATR tolerance,
    09:30-09:35 high + ATR tolerance
)
```

The entry and target do not change. The signal is routed only when
`(entry - target) / (fresh_stop - entry) >= 1.0`. The prior V2 stop and V2
quantity are stored as `legacy_v2_stop` and `fresh_stop_parent_v2_quantity`;
they are not sent as a second broker order.

For V2, `size_multiplier` is the engine multiplier times the following causal
risk multiplier, calculated only from completed sessions before the entry day:

- vulnerable rules at SPX structure score 70: `0.75`;
- vulnerable rules at SPX structure score 100: `0.50`;
- a correlated-market short after lower SPY value rejection with the highest
  overhead HVN at least 1 ATR away: capped at `0.50`;
- reductions are not compounded below `0.50`;
- missing required SPX/SPY context uses a conservative `0.50` cap for the
  affected trade.

The V1 full-size quantity is never sent as a second broker order. It is stored
as `legacy_shadow_quantity` alongside the V2 quantity in the trade plan,
DecisionRecord risk factor and journal trade metadata.

For `bearish_gap_breakdown_continuation_short`, V4 multiplies the V3 size by
`0.25` when either of these causal conditions is true:

- `volume_price_phase == high_volume_breakout`;
- `volume_price_phase == high_volume_breakdown` while the prior completed SPX
  session is not in `strong_bull` direction;
- SPX has advanced more than 2 ATR over 20 completed sessions and the asset's
  20-session return is at least as strong as SPX.

SPX direction and relative strength use only sessions strictly before the entry
day. The routed quantity is calculated after applying the 0.25 multiplier and
rounding down to whole shares. `v3_parent_shadow_quantity` stores the unscaled
V3 quantity virtually; no second broker order is sent.

When at least two routable `bearish_lower_high_failure_short` signals occur
simultaneously in `SPY, IVV, VOO, VTI, OEF`, V2 routes only rank 1 by planned
CRV. The otherwise eligible V1 candidates remain visible in their journaled
selection factors as legacy shadow candidates.

Default risk in the recommended command:

```text
--risk-per-trade 100
```

Before sending a new order, the runner checks existing IB positions and open
orders for the same symbol. If exposure already exists, it skips the duplicate.

## Logs And Outputs

Main output files are under:

```text
D:\Trading\logs
```

Current test/live trade plan:

```text
D:\Trading\logs\manual_regime_live_trades_YYYYMMDD.csv
```

Test/live raw overlay used by the engine:

```text
D:\Trading\logs\manual_regime_live_overlay\YYYYMMDD\Rohdaten_<SYMBOL>.csv
```

Engine logs:

```text
D:\Trading\logs\manual_regime_live_engine
```

The script also logs important status lines to stdout:

- start/wait status
- TWS connection status
- first-two-bar fetch status
- raw engine trade count
- active V4 baseline trade count
- V4 base quantity, V3 parent-shadow quantity, V2 stop-shadow quantity, V1
  size-shadow quantity and causal sizing reason
- trade plan path
- safe-bracket `orderRef`, order ids, permanent ids and leg statuses
- skipped duplicate/existing exposure

If a persistent console log is needed, start it with PowerShell logging, for example:

```powershell
cd D:\PycharmProjects
python .\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py --place-orders --start-local 15:00 --decision-fetch-et 09:40 --risk-per-trade 100 --host 127.0.0.1 --port 7496 --client-id 62 | Tee-Object -FilePath D:\Trading\logs\manual_regime_bot_YYYYMMDD.log
```

Replace `YYYYMMDD` manually before running if a date-specific stdout log is
required.

## Pre-Flight Checklist

Before using `--place-orders`:

- TWS or IB Gateway is already running.
- API access is enabled in TWS.
- The configured API port is open, normally `7496`.
- The TWS account/session is the intended test/paper account for this workflow.
- The log confirms `paper_guard=True` and `safe_attached_bracket=true`.
- There are no unwanted existing positions or open orders.
- It is acceptable for the bot to send test/paper market parent orders.
- The machine clock and timezone are correct.
- The current date is a US trading day.
- The command is started before the expected `09:40` New York decision fetch.

## Test Procedure

For a normal end-to-end test of signal generation and TWS order handoff, run the
recommended command with `--place-orders`. No extra confirmation is required in
this test environment.

For a signal-only test, omit `--place-orders`; the bot then only writes/logs the
trade plan and does not test order handoff.

## What Not To Use For The Current Test Baseline

Do not use the following as the default test start path for the current baseline:

```text
IBKR_TradingBot\src\bot\live_trade_probe.py
```

That file still exists and is useful as a generic IBKR TradingBot orchestration
probe. It has its own money-management config, basket config, execution log
format, and defaults. It is not the runner that was used for the 2026-07-06
successful SPY test/paper route.

Use `live_trade_probe.py` only when explicitly testing the generic
`IBKR_TradingBot` runner.

If it is intentionally used, its current test-order command must include the
non-default flags:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
python .\src\bot\live_trade_probe.py --execute-orders --entry-order-type MKT --bracket-style manual_oca --market-open-delay-minutes 10 --bootstrap-bars 2 --countdown-poll-seconds 5
```

Signal-only generic probe:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
python .\src\bot\live_trade_probe.py --entry-order-type MKT --bracket-style manual_oca --market-open-delay-minutes 10 --bootstrap-bars 2
```

## Related Code

Current test baseline runner:

- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\live_manual_regime_tws_bot.py`
- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\manual_regime_bot_api.py`
- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\manual_regime_rules_backtest.py`
- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\manual_regime_position_sizing.py`
- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\manual_regime_sizing_v2_manifest.py`

Generic IBKR TradingBot runner:

- `D:\PycharmProjects\IBKR_TradingBot\src\bot\live_trade_probe.py`
- `D:\PycharmProjects\IBKR_TradingBot\src\data\market_data_hub.py`
- `D:\PycharmProjects\IBKR_TradingBot\src\execution\ib_order_executor.py`
- `D:\PycharmProjects\IBKR_TradingBot\src\risk\money_manager.py`
