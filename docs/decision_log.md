# Decision Log

## 2026-06-22

- neues Wurzelprojekt angelegt: `D:\PycharmProjects\IBKR_TradingBot`
- keine Vermischung mit dem alten `PythonProject`
- Regel-Engine bleibt getrennt von Money Management und IB-Ausführung
- IB wird für Datenabruf und Order-Ausführung genutzt
- basket universe version v1 gespeichert in `configs/baskets_v1.json`
- day-only engine binding verified against full run for `GPC` on `2026-06-04`
- live/replay split defined for stateful intraday trading and after-close simulation
- shared `EngineCore` chosen with one state per asset, not one engine instance per asset
- MarketDataHub added with IB subscriptions for multiple symbols and countdown demo
- historical first-5m bootstrap verified for IVV and SPY, with collector seeding preserved
- live trade probe on 2026-06-22 with first 5m bar for all 50 basket symbols returned no trades
- live trade probe now waits for the first two IB 5-minute bars, seeds them once, then asks the rule engine for today's trades
- verified the two-bar live bootstrap against TWS for IVV and SPY on 2026-06-22; both symbols returned `trades ...=none`
- bot engine runs now accept a decision log path and write a combined per-day log file; `market_open_countdown.py` also supports `--symbols` for quick subset tests
- money-management defaults are versioned in `configs/money_management_v1.json` and loaded by the bot runners as account size, risk per trade, and max-open-trades settings
