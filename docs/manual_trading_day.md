# Manueller Trading-Tageslauf

Solange der Trading-Bot nicht dauerhaft als 24/7-Prozess läuft, ist
`scripts\run_trading_day.py` der verbindliche manuelle Einstiegspunkt. Der
direkte Start von `live_manual_regime_tws_bot.py` ist nur für gezielte Diagnose
gedacht, weil er Rohdaten, TWS-Sync und Backtest nicht selbst vorbereitet.

Für einen reinen Wochenend-/Recovery-Catch-up ohne Journal-Sync, Live-Bot oder
Orderübermittlung gibt es `scripts\run_data_backtest_catchup.py`. Der Einstieg
lädt die historischen Rohdaten über eine read-only TWS-Verbindung bis zum
letzten abgeschlossenen US-Handelstag und erzeugt anschließend nur die noch
fehlenden täglichen Backtest-Manifeste der vergangenen sieben Kalendertage:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_data_backtest_catchup.py
```

Mit `--start-date` und `--end-date` kann das Sitzungsfenster explizit gesetzt
werden. Dieser Einstieg startet niemals den Live-Bot und besitzt bewusst keine
Option zur Orderübermittlung.

## Voraussetzungen

- Die Paper-TWS läuft und akzeptiert API-Verbindungen auf Port `7496`.
- Das gewünschte Konto beginnt mit `DU`.
- Der Tagesstarter wird rechtzeitig vor dem Live-Launch-Cutoff `09:35 ET`
  gestartet. Wegen des ungefähr 30 Minuten dauernden Rohdaten-Downloads und des
  anschließenden Backtests ist `05:00 ET` der empfohlene Startzeitpunkt.
- Für bis zu sieben Tage alte TWS-Ausführungen muss das Trade Log der TWS auf
  den entsprechenden Zeitraum eingestellt sein. Der tägliche Ablauf benötigt
  normalerweise nur den vorherigen US-Handelstag.

## Reihenfolge

Ein Start führt für den heutigen Handelstag in New York genau diese Phasen aus:

1. `journal_sync`: offene und abgeschlossene Orders sowie Ausführungen seit dem
   vorherigen US-Handelstag read-only aus der TWS laden; bekannte Bracket-Orders
   den ursprünglichen Trades zuordnen und Status, Exitpreis, Gebühren und
   realisierten P/L aktualisieren.
2. `raw_data`: `D:\PycharmProjects\PythonProject\Daten_Download\Download_Asset_Data.py`
   ausführen und anschließend prüfen, dass alle Produktionssymbole vollständige
   Daten für den vorherigen Handelstag besitzen.
3. `backtest`: die Manual-Regime-Engine mit 24 Workern bis gestern ausführen,
   die Modell-Trades ins Journal schreiben und die exakte Backtest-Run-ID
   festhalten.
4. `live_trading`: diese Run-ID an den Live-Bot übergeben, bis 09:40 ET warten,
   heutige Signale berechnen und nur bei aktiviertem `--place-orders` an die TWS
   senden.
5. Die aktivierten Optionsmonitore bis 16:05 ET betreiben; anschließend endet
   der manuelle Prozess.

Eine spätere Phase startet nur, wenn die vorherige erfolgreich war. Nach dem
Cutoff werden keine verspäteten Aktienorders erzeugt. Die SPXW-0DTE-Strategie
hat zusätzlich ihr eigenes 09:50-ET-Timing-Veto.

## Paper-Trading mit allen derzeit integrierten Strategien

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_day.py `
  --port 7496 `
  --workers 24 `
  --place-orders `
  --enable-failed-auction-fly `
  --enable-spxw-15dte-put-credit `
  --enable-spxw-0dte-bull-put-iu30 `
  --enable-spxw-75dte-put-bwb `
  --spxw-75dte-bwb-initial-entry-work-minutes 5 `
  --spxw-75dte-bwb-entry-work-minutes 5 `
  --spxw-75dte-bwb-final-entry-work-minutes 5 `
  --enable-xsp-vix-pullback `
  --xsp-vix-pullback-entry-work-minutes 10 `
  --enable-stock-ivr70-put-credit `
  --options-account-value-usd 25000 `
  --options-max-risk-fraction 0.10
```

Ohne `--place-orders` wird die vollständige Vorbereitung einschließlich
Rohdaten und Backtest ausgeführt, aber geplante Orders werden als Dry-Run im
Journal blockiert.

## Wiederanlauf und Duplikatschutz

Der Zustand jeder Phase wird unter
`D:\Trading\journal\trading_bot_24_7_state.json` gespeichert. Ein erneuter Start
am selben Tag überspringt bereits erfolgreich abgeschlossene Phasen und setzt
bei der ersten fehlgeschlagenen oder noch nicht ausgeführten Phase fort. Die
Lockdatei `D:\Trading\journal\trading_bot_24_7.lock` verhindert zwei parallele
Tagesläufe.

Für einen ausdrücklich gewählten Recovery-Tag bleibt der technische Befehl
verfügbar:

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_trading_bot_24_7.py `
  --once-day 2026-07-30
```

## Protokolle und Kontrolle

- Prozesslog: `D:\Trading\logs\trading_bot_24_7.log`
- Phasenstatus: `D:\Trading\journal\trading_bot_24_7_state.json`
- Journal: `D:\Trading\journal\trading_journal.sqlite`
- Tagesentscheidungen: `D:\Trading\logs\manual_regime_live_decisions_YYYYMMDD.jsonl`
- Tages-Tradeplan: `D:\Trading\logs\manual_regime_live_trades_YYYYMMDD.csv`

Die Journaltabellen und Equity-/Live-vs.-Backtest-Reports sind in
[`trading_journal_reports.md`](trading_journal_reports.md) beschrieben.

Der kontogetrennte Betrieb einer Paper- und einer Produktions-TWS mit nur einem
Rohdatenlauf, Backtest und Signalplan ist in
[`paper_production_parallelbetrieb.md`](paper_production_parallelbetrieb.md)
beschrieben. Ohne `--dual-broker` bleibt der hier dokumentierte Paperablauf
unverändert.
