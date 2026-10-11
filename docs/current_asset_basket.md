# Aktueller Asset-Basket der Multi-Asset Daily Regime Strategie

Stand: **11.10.2026**, aus der aktiven Code-Konfiguration ausgelesen.
Variante: `optimized_cutoff_2025_11_top50_current_baseline`.

Der feste Basket umfasst **50 Symbole**. Davon sind **48 grundsätzlich für
direkte Stock-/ETF-Orders routbar**. `SPX` und `SPX_FUT` sind im Basket enthalten,
werden aber von dieser Stock-Execution ausdrücklich nicht als direkte Orders
an TWS übermittelt. Ein routbares Symbol löst nur bei freigegebenem Signal und
bestandenen Risiko-/Ausführungsprüfungen einen Trade aus.

Die Reihenfolge entspricht der aktiven Auswahl-Datei:

| Position | Symbol | Direkte Stock-/ETF-Order grundsätzlich möglich |
| ---: | --- | --- |
| 1 | SPX | Nein |
| 2 | REGN | Ja |
| 3 | SPX_FUT | Nein |
| 4 | BLK | Ja |
| 5 | INTU | Ja |
| 6 | MLM | Ja |
| 7 | LLY | Ja |
| 8 | IVV | Ja |
| 9 | DLTR | Ja |
| 10 | MANH | Ja |
| 11 | AMD | Ja |
| 12 | ETN | Ja |
| 13 | CEG | Ja |
| 14 | HUBB | Ja |
| 15 | HUM | Ja |
| 16 | MCK | Ja |
| 17 | GRMN | Ja |
| 18 | EL | Ja |
| 19 | MSCI | Ja |
| 20 | AMZN | Ja |
| 21 | BX | Ja |
| 22 | DG | Ja |
| 23 | OEF | Ja |
| 24 | SRE | Ja |
| 25 | SPY | Ja |
| 26 | ALV | Ja |
| 27 | LYV | Ja |
| 28 | TER | Ja |
| 29 | BLDR | Ja |
| 30 | HCA | Ja |
| 31 | V | Ja |
| 32 | PNC | Ja |
| 33 | VTI | Ja |
| 34 | VOO | Ja |
| 35 | ORCL | Ja |
| 36 | SITM | Ja |
| 37 | QQQ | Ja |
| 38 | SAIA | Ja |
| 39 | CVNA | Ja |
| 40 | ACN | Ja |
| 41 | GPC | Ja |
| 42 | MA | Ja |
| 43 | MOH | Ja |
| 44 | PSA | Ja |
| 45 | DIA | Ja |
| 46 | TPR | Ja |
| 47 | ECL | Ja |
| 48 | HLT | Ja |
| 49 | GLD | Ja |
| 50 | KLAC | Ja |

## Verbindliche Quellen und Abgrenzung

- Aktive Konfiguration:
  [`current_live_baseline_config.py`](../statistic/spx_adx_di_5min_2024_02_03/current_live_baseline_config.py).
- Basket-Datei:
  [`current_baseline_optimized_cutoff_2025_11_top50_symbols.csv`](../statistic/spx_adx_di_5min_2024_02_03/current_baseline_optimized_cutoff_2025_11_top50_symbols.csv).
- Symbol-/Regelfreigaben:
  [`current_baseline_optimized_cutoff_2025_11_top50_symbol_rule_selection.csv`](../statistic/spx_adx_di_5min_2024_02_03/current_baseline_optimized_cutoff_2025_11_top50_symbol_rule_selection.csv).
  Zum Dokumentationsstand sind 318 Symbol-/Regel-Paare freigegeben;
  ihre Symbolmenge stimmt mit den 50 Basket-Symbolen überein.
- Routing-Ausnahmen: `NON_ROUTABLE` in
  [`live_manual_regime_tws_bot.py`](../statistic/spx_adx_di_5min_2024_02_03/live_manual_regime_tws_bot.py).

Diese Liste beschreibt den Standard-Basket der Regime-Strategie. Separat
aktivierbare Optionsstrategien besitzen eigene Underlyings bzw. Scanner-
Universen. Auch `D:\Trading\assets.csv` für den Rohdaten-Downloader kann deutlich
mehr Assets enthalten; sie ist nicht die Handelsfreigabe dieses Baskets.

Bei Änderungen ist die Code-/CSV-Konfiguration maßgeblich. Diese Dokumentation
ändert selbst weder die Asset-Auswahl noch die Handelsfreigaben.
