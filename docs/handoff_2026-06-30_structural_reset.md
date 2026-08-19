# Handoff 2026-06-30: Full-History, Live-History und Strukturbruch-Reset

## Kontext

Ausgangspunkt war die Verwirrung zwischen:

- `all_engine_trade_signals_2026.csv`: Live-nahe Engine-Signale, u. a. AZO Short am 2026-06-26.
- `engine_trade_simulation_2026.csv`: Full-History-Simulation, AZO Short fehlte.

Die Ursache war kein offener Trade, sondern abweichender Engine-State durch unterschiedliche Historie.

## Wichtige Erkenntnisse

### AZO Full-History vs Kaltstart

AZO 2026-06-26:

- Kaltstart/Live-Lookback ab ca. 2025-01-06:
  - `bearish_failed_countermove_short_baseline`
  - `last_countermove_high = 3170.12` vom 2026-06-15
  - `broken_support = 3210.72`
  - `bars_since_support_break = 4`
  - `mature_break = True`
  - CRV ca. 1.46
  - Trade wird geoeffnet.
- Full-History ab 2023-01-01:
  - kein Trade
  - `last_countermove_high = 3412.40` vom 2026-03-23
  - `bars_since_support_break = 1`
  - `mature_break = False`

Spaeter wurde klar: Den Kaltstart-Trade muessen wir fachlich nicht zwingend "retten". Die alte Zone um 3190/3210 ist charttechnisch relevant, der Kurs hatte laut visueller Einschaetzung/Volumenprofil der letzten 150 Tage einen neuen Value/POC-Bereich um 3100 gefunden. Ende Juni 2026 ist AZO eher `no_trade` als ein hochwertiger Short oder Long.

### Full-History-Plots

Der Plot `cutoff_forward_top50_variants_cutoff_zero_equity.png` stammt nicht aus dem Live-Modus.

Pfad:

- `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\compare_cutoff_forward_topn_variants.py`

Er nutzt:

- `trade_ledger_baseline_best_modes.csv`

Dieser Ledger wurde ueber:

- `build_trade_ledger.py`
- `DEFAULT_START = "2023-01-01"`
- `run_backtest(symbol, start, end, ...)`

gebaut. Danach wurden nur Entries ab 2024-01-01 behalten. Die State-Historie ist aber Full-History/Long-Warmup.

## Bisherige Code-Aenderungen

Alle Aenderungen liegen in:

`D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03`

### `manual_regime_rules_backtest.py`

Ergaenzt:

- `max_history_days`
  - begrenzt `prior`/Swing-Erkennung und bestimmte State-Anker.
- `structural_reset_mode`
  - Werte: `off`, `log`, `apply`.
- `detect_structural_reset(...)`
  - erkennt starke Post-Peak-Drawdowns, z. B. AZO am 2025-10-30.
- Debug-Spalten fuer Failed-Countermove:
  - `last_countermove_high`
  - `last_countermove_high_day`
  - `mature_break`
  - `first_fast_retest`
  - `failed_countermove_fail_zone`
  - `failed_countermove_target`
  - `failed_countermove_risk`
  - `failed_countermove_crv`
  - `structure_epoch_day`
- Testweise `structure_epoch_idx`
  - nach einem `structural_reset_mode=apply` werden Strukturlevels ab Reset-Tag berechnet.
  - Das erwies sich fuer AZO als zu hart, weil dann alte, aber relevante Zonen wie 3190/3210 fehlen koennen.

### `build_trade_ledger.py`

Ergaenzt:

- `--max-history-days`
- `--structural-reset-mode off|log|apply`

### `compare_cutoff_forward_topn_variants.py`

Ergaenzt:

- `--output-prefix`

Damit werden alte Plot-Dateien nicht ueberschrieben.

## Erzeugte Artefakte

### Live-History 548d

Ledger:

- `trade_ledger_baseline_best_modes_live_history_548d.csv`

Plots/CSVs:

- `cutoff_forward_top50_variants_live_history_548d_*`

Ergebnis `cutoff_2025-11_top50`:

- Full-History: ca. +36,641 USD
- Live-History 548d: ca. +34,404 USD

Die Kurve bleibt sehr robust.

### Structural Reset

Ledger:

- `trade_ledger_baseline_best_modes_structural_reset.csv`

Plots/CSVs:

- `cutoff_forward_top50_variants_structural_reset_*`

Ergebnis `cutoff_2025-11_top50`:

- Structural Reset: ca. +29,011 USD

Der Reset ist aktuell zu defensiv bzw. zu grob, besonders mit hartem `structure_epoch_idx`.

## Aktueller fachlicher Stand

Wir wollen nicht mehr primaer den Live-Kaltstart exakt rekonstruieren.

Stattdessen:

- Alte path-abhaengige Regime-/Countermove-State-Anker sollen bei deutlichem Strukturbruch geloescht werden.
- Alte wichtige Preiszonen duerfen weiterhin relevant bleiben, wenn der Markt daran wiederholt reagiert hat.
- Nach Major-Break sollte `bullish_tier3` deutlich schwerer erreichbar sein.
- In Uebergangszonen wie AZO Ende Juni 2026 ist `no_trade` fachlich plausibler als ein erzwungener Short oder Long.

## Wichtigste offene technische Frage

Der harte `structure_epoch_idx` loescht zu viel. Er entfernt auch alte Preiszonen, die fuer Support/Resistance noch relevant sein koennen.

Naechster sinnvoller Umbau:

1. `structure_epoch_idx` nicht global auf alle Swings/Support/Resistance anwenden.
2. Stattdessen getrennte Gedaechtnisse:
   - Regime-/Countermove-/Breakout-State: Reset bei Strukturbruch.
   - Support-/Resistance-Level: weiterhin aus Live-History-Fenster oder aus relevanten Volume-/Reaction-Zones.
   - Bullish-Regime-Aufstufung nach Major-Break: strenger oder mit Cooldown.
3. Fuer AZO Ende Juni 2026 sollte die Engine idealerweise `no_trade` liefern mit Begruendung:
   - alte Support-Zone 3190/3210
   - neuer tieferer Value/POC-Bereich ca. 3100
   - gebrochene groessere Struktur
   - kein sauberer Trend-Trade

## Wichtige Diagnosewerte AZO

Reset mit Epoch ab 2025-10-30:

- alte 2024-Level verschwinden.
- Juni 2026 nutzt nahe Levels.
- Trotzdem kein Kaltstart-Trade, weil `last_countermove_high` nicht auf 3170.12 vom 2026-06-15 springt.

Kaltstart ab 2025-01-06:

- behalt alte, aber relevante Level aus Jan/Okt 2025.
- 2026-06-15 kippt kurz auf `bullish_tier3`.
- Danach `last_countermove_high = 3170.12`.
- Short am 2026-06-17/26.

Fachliche Einschaetzung: Dieses `bullish_tier3` ist bei AZO zu optimistisch.

## Commands, die zuletzt funktionierten

AZO-Diagnose:

```powershell
python -c "import sys,pandas as pd; sys.path.insert(0,r'D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03'); import manual_regime_rules_backtest as m; signals,trades,daily=m.run_backtest('AZO','2023-01-01','2026-07-01',learn_long_mode='off',structural_reset_mode='apply'); print(signals.tail().to_string())"
```

Live-History-Ledger:

```powershell
python "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\build_trade_ledger.py" --start 2023-01-01 --end 2026-06-18 --workers 32 --learn-mode-source best --max-history-days 548 --output "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_baseline_best_modes_live_history_548d.csv"
```

Structural-Reset-Ledger:

```powershell
python "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\build_trade_ledger.py" --start 2023-01-01 --end 2026-06-18 --workers 32 --learn-mode-source best --structural-reset-mode apply --output "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_baseline_best_modes_structural_reset.csv"
```

Plots:

```powershell
python "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\compare_cutoff_forward_topn_variants.py" --ledger "D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\trade_ledger_baseline_best_modes_structural_reset.csv" --top-n 50 --output-prefix cutoff_forward_top50_variants_structural_reset
```

