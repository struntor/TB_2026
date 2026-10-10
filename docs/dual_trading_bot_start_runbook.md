# Dualen Trading-Bot sicher starten

Diese Anleitung beschreibt den täglichen manuellen Start des gemeinsamen
Paper-/Produktionslaufs. Sie ergänzt
[`paper_production_parallelbetrieb.md`](paper_production_parallelbetrieb.md)
und ist für den aktuell eingerichteten Betrieb gedacht:

| Ziel | TWS-Port | Konto | Zweck |
| --- | ---: | --- | --- |
| Paper | `7496` | `DU9372630` | Stock-Plan und aktivierte Optionsstrategien |
| Produktion | `7497` | `U7492180` | Nur freigegebene Manual-Regime-Stockorders |

Optionsstrategien werden systemseitig niemals an das Produktionskonto geroutet.

## Code-Standort nach der Konsolidierung (10.10.2026)

Die unten aufgeführte Tagespipeline verwendet jetzt den Projektcode ausschließlich
aus `D:\PycharmProjects\IBKR_TradingBot` (Repository `TB_2026`), einschließlich
Regel-Engine, benötigter Research-Helfer und Rohdaten-Downloader. Der alte
Checkout `PythonProject` wird dafür nicht mehr benötigt. Die Startbefehle und
Orderfreigaben bleiben unverändert. Rohdaten und Journal bleiben unter `D:\Trading`.

Vor dem nächsten Start kann die Code-Struktur ohne TWS-Verbindung geprüft werden:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\verify_runtime_layout.py
```

Erwartet: `"status": "ok"`. Dies ist eine Offline-Import- und Konfigurationsprüfung,
kein vollständiger Backtest und keine Prüfung der Erreichbarkeit von TWS.
Details und Grenzen: [Repository-Konsolidierung](consolidated_repository.md).

## 1. Vor dem Start

1. Beide TWS-Instanzen müssen angemeldet sein und API-Verbindungen erlauben.
2. Paper-TWS muss auf `127.0.0.1:7496` laufen und das Konto `DU9372630`
   melden.
3. Production-TWS muss auf `127.0.0.1:7497` laufen und das Konto `U7492180`
   melden.
4. Der Start sollte deutlich vor `09:35 ET` erfolgen. Rohdaten-Download und
   Backtest benötigen üblicherweise rund 30 Minuten; empfohlen ist `05:00 ET`.
5. Vor einer neuen Instanz prüfen, dass kein Tageslauf bereits aktiv ist.

```powershell
$paper = Test-NetConnection -ComputerName 127.0.0.1 -Port 7496 -InformationLevel Quiet
$production = Test-NetConnection -ComputerName 127.0.0.1 -Port 7497 -InformationLevel Quiet

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'run_trading_day\.py|run_trading_bot_24_7\.py' } |
  Select-Object ProcessId, CommandLine

[pscustomobject]@{
  paper_tws = $paper
  production_tws = $production
}
```

Nur wenn beide Ports `True` liefern und kein laufender Tagesprozess erscheint,
den nächsten Schritt ausführen. Ein offener Port allein reicht nicht als
Kontoprüfung: Der Bot verifiziert die Kontonummern beim Start erneut und sperrt
den Produktionspfad bei Abweichungen.

## 2. Tagespipeline mit echter Production-Freigabe starten

Der folgende Befehl führt exakt einmal aus:

1. Journal-Sync beider Konten ab dem vorherigen US-Handelstag;
2. Rohdaten-Download und Vollständigkeitsprüfung;
3. Backtest bis zum letzten abgeschlossenen US-Handelstag;
4. gemeinsame Signalberechnung;
5. Paper-Orders sowie die separat freigegebenen Production-Stockorders.

`--production-place-orders` ist die explizite Freigabe echter Orders. Den
Schalter nur verwenden, wenn Production-Orders tatsächlich gewünscht sind.

```powershell
cd D:\PycharmProjects\IBKR_TradingBot

$botArgs = @(
  '-u', '.\scripts\run_trading_day.py',
  '--host', '127.0.0.1', '--port', '7496',
  '--sync-client-id', '63', '--live-client-id', '62',
  '--account', 'DU9372630', '--place-orders',
  '--dual-broker',
  '--production-host', '127.0.0.1', '--production-port', '7497',
  '--production-sync-client-id', '73', '--production-live-client-id', '72',
  '--production-account', 'U7492180', '--production-place-orders',
  '--workers', '24',
  '--enable-failed-auction-fly',
  '--enable-spxw-15dte-put-credit',
  '--enable-spxw-0dte-bull-put-iu30',
  '--enable-spxw-75dte-put-bwb',
  '--enable-xsp-vix-pullback',
  '--enable-stock-ivr70-put-credit'
)

$date = Get-Date -Format 'yyyyMMdd'
$stdout = "D:\Trading\logs\dual_mode_${date}_stdout.log"
$stderr = "D:\Trading\logs\dual_mode_${date}_stderr.log"

Start-Process `
  -FilePath 'D:\PycharmProjects\.venv\Scripts\python.exe' `
  -ArgumentList $botArgs `
  -WorkingDirectory 'D:\PycharmProjects\IBKR_TradingBot' `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -WindowStyle Hidden `
  -PassThru
```

Die zurückgegebene `Id` ist die PID. Sie dient zur gezielten Kontrolle und
beendet weder TWS noch einzelne Orders, falls der Python-Prozess später
kontrolliert werden muss.

## 3. Unmittelbar nach dem Start prüfen

Nach etwa 10 bis 20 Sekunden müssen beide Journal-Sync-Phasen erscheinen.

```powershell
Get-Content $stdout -Tail 80
Get-Content $stderr -Tail 80
Get-Process -Id <PID> -ErrorAction SilentlyContinue
```

Erwartete Logzeilen:

- `journal_sync: completed` für Paper;
- `journal_sync_production: completed` für Produktion;
- danach `raw_data: starting`;
- vor dem echten Produktionsrouting: `production TWS validated`;
- beim Routing: `routing approved manual-regime stock plan to production`.

Die Sync-Zusammenfassung enthält `orders`, `executions` und `commissions`.
Damit werden auch am Vortag geschlossene TP-/SL-Orders und ihre Fills ins
Journal übernommen, sofern TWS sie über die API liefert.

Ein leerer Fehlerlog und ein noch laufender Prozess bedeuten, dass die Pipeline
in der Vorbereitung arbeitet. Ein Trade wird erst nach Datenprüfung, Backtest
und dem jeweiligen Zeitfenster ausgelöst.

## 4. Laufend kontrollieren

```powershell
Get-Content $stdout -Tail 100
Get-Content 'D:\Trading\logs\trading_bot_24_7.log' -Tail 100
Get-Content 'D:\Trading\journal\trading_bot_24_7_state.json'
```

Zusätzliche Artefakte für die Nachvollziehbarkeit:

- Journal: `D:\Trading\journal\trading_journal.sqlite`
- Backtest-/Phasenstatus: `D:\Trading\journal\trading_bot_24_7_state.json`
- Entscheidungen: `D:\Trading\logs\manual_regime_live_decisions_YYYYMMDD.jsonl`
- Stock-Tradeplan: `D:\Trading\logs\manual_regime_live_trades_YYYYMMDD.csv`

Der State speichert erfolgreiche Phasen. Ein erneuter Start am selben Tag
überspringt diese Phasen; die Lockdatei verhindert parallele Tagesläufe.

## 5. Sichere Varianten und Fehlerfälle

### Paper- und Production-Dry-Run

Für einen Lauf ohne neue Orders `--place-orders` und
`--production-place-orders` aus dem Befehl entfernen. Journal-Sync, Download,
Backtest und Signalberechnung laufen weiterhin.

### Nur Daten und Backtests nachziehen

Für Wochenenden oder einen reinen Catch-up ohne Live-Bot:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\run_data_backtest_catchup.py
```

Dieser Einstieg startet niemals Live-Trading und besitzt keine
Orderfreigabe. Für TWS-Ausführungen ist zusätzlich der reguläre read-only
Journal-Sync nötig.

### Production-TWS nicht erreichbar oder falsches Konto

Nicht mit einer Ersatz-Portzuordnung fortfahren. Der Dual-Modus ist
absichtlich fail-closed: Paper kann weiterlaufen, Produktion bleibt für den
gesamten Tageszyklus gesperrt. TWS/Port/Konto korrigieren und vor einem neuen
Start den gespeicherten Phasenstatus prüfen.

### Pipelinefehler

1. Fehlerlog und Prozesslog sichern bzw. prüfen.
2. Keine zweite Pipeline starten, solange der bestehende Prozess noch läuft.
3. Ursache beheben (TWS, Netzwerk, Datenquelle oder Berechtigung).
4. Danach denselben Tagesstarter erneut ausführen. Erfolgreiche Phasen werden
   anhand des State übersprungen; die erste offene bzw. fehlgeschlagene Phase
   wird fortgesetzt.

Für Details zu Journal-Auswertungen siehe
[`trading_journal_reports.md`](trading_journal_reports.md); die vollständigen
Parallelbetriebs-Garantien stehen in
[`paper_production_parallelbetrieb.md`](paper_production_parallelbetrieb.md).
