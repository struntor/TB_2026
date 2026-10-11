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

Die aktuelle feste Symbolauswahl der Regime-Strategie steht in
[`current_asset_basket.md`](current_asset_basket.md): 50 Basket-Symbole,
davon 48 grundsätzlich für direkte Stock-/ETF-Orders routbar.

## Code-Standort nach der Konsolidierung (10.10.2026)

Die unten aufgeführte Tagespipeline verwendet jetzt den Projektcode ausschließlich
aus `D:\PycharmProjects\IBKR_TradingBot` (Repository `TB_2026`), einschließlich
Regel-Engine, benötigter Research-Helfer und Rohdaten-Downloader. Der alte
Checkout `PythonProject` wird dafür nicht mehr benötigt. Die Startbefehle und
Orderfreigaben bleiben unverändert. Der Bot arbeitet mit lokalen Daten unter
`D:\Trading`; das NAS liefert den geprüften Umzugs-/Sicherungsstand.

Vor dem nächsten Start kann die Code-Struktur ohne TWS-Verbindung geprüft werden:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
.\.venv\Scripts\python.exe .\scripts\verify_runtime_layout.py
```

Erwartet: `"status": "ok"`. Dies ist eine Offline-Import- und Konfigurationsprüfung,
kein vollständiger Backtest und keine Prüfung der Erreichbarkeit von TWS.
Details und Grenzen: [Repository-Konsolidierung](consolidated_repository.md).

## 0. Einmalige Einrichtung auf dem anderen Rechner

Dieser Abschnitt ist auch die Übergabe an ChatGPT/Codex auf dem neuen Rechner.
Zuerst Installation, Datenwiederherstellung und Offline-Prüfung durchführen.
Das Lesen dieser Anleitung ist keine Freigabe, echte Orders zu senden; die
Order-Schalter unten nur beim ausdrücklich gewünschten Handelsstart verwenden.

### Programme, Repository und Python-Umgebung

Vorausgesetzt werden Windows, Git, Python **3.13 in 64 Bit**, TWS sowie Zugriff
auf `\\Synology_NAS\home` mit dem eigenen NAS-Benutzer. Die Freigabe zuerst im
Windows-Explorer öffnen und die Anmeldung prüfen. NAS- und TWS-Passwörter nicht
in das Repository schreiben.

Die Engine verwendet derzeit feste Windows-Pfade. Deshalb auf diesem Rechner
ebenfalls `D:\PycharmProjects\IBKR_TradingBot` und `D:\Trading` verwenden; andere
Laufwerke sind ohne weiteren Code-Umbau nicht vollständig unterstützt.

Für einen neuen Checkout:

```powershell
New-Item -ItemType Directory -Force D:\PycharmProjects | Out-Null
git clone https://github.com/struntor/TB_2026.git D:\PycharmProjects\IBKR_TradingBot
cd D:\PycharmProjects\IBKR_TradingBot
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Falls das Repository bereits existiert, stattdessen dort zuerst `git status`
prüfen und bei sauberem Arbeitsverzeichnis `git pull --ff-only` ausführen.
Die eigene `.venv` und `requirements.txt` sind auch dann erforderlich. Der alte
Checkout `PythonProject` und die bisherige gemeinsame Python-Umgebung sind für
den neuen Rechner nicht nötig.

### NAS-Quellen und erforderliche lokale Zielpfade

`/home` auf dem Synology-NAS entspricht hier der Windows-Freigabe
`\\Synology_NAS\home`. Die Daten werden **auf die lokale SSD kopiert**:

| Inhalt | Quelle auf dem NAS | Ziel auf dem neuen Rechner |
| --- | --- | --- |
| Kursdaten | `\\Synology_NAS\home\Rohdaten\Rohdaten_*.csv` | `D:\Trading\Rohdaten\Rohdaten_*.csv` |
| Downloader-Universum | `\\Synology_NAS\home\Rohdaten\assets.csv` | `D:\Trading\assets.csv` |
| Trading-Journal | `\\Synology_NAS\home\Journal\trading_journal.sqlite` | `D:\Trading\journal\trading_journal.sqlite` |
| Strategie-Zustände, auch für offene Options-Trades | `\\Synology_NAS\home\Journal\strategy_state\*.json` | `D:\Trading\state\*.json` |
| IVR70-Auswahl | `\\Synology_NAS\home\Rohdaten\runtime_inputs\research\single_stock_drawdown_recovery_put_credit\equity_screen_v1\asset_diagnostics.csv` | `D:\Trading\research\single_stock_drawdown_recovery_put_credit\equity_screen_v1\asset_diagnostics.csv` |
| Öffentliche Earnings-Caches für IVR70 | `\\Synology_NAS\home\Rohdaten\runtime_inputs\research\single_stock_drawdown_recovery_put_credit\sec_earnings_cache_all\*.json` | `D:\Trading\research\single_stock_drawdown_recovery_put_credit\sec_earnings_cache_all\*.json` |

Der geprüfte Stand vom **10.10.2026** enthält 561 Rohdaten-CSV-Dateien,
das Journal mit ca. 10,5 GB, acht Strategie-State-Dateien und 233 zusätzliche
Scanner-Eingaben. Insgesamt sind ca. 14,5 GB zu übertragen; zusätzlichen freien
Platz für Backtest-Ausgaben, Logs und das wachsende Journal einplanen.

Für die Wiederherstellung das mitgelieferte Skript verwenden:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
$python = 'D:\PycharmProjects\IBKR_TradingBot\.venv\Scripts\python.exe'
& $python .\scripts\restore_nas_data.py --nas-home '\\Synology_NAS\home'
```

Das Skript liest die neuesten `transfer_manifest_*.json` und
`runtime_inputs_manifest_*.json` aus dem NAS-Ordner `Journal`, prüft alle
Dateigrößen und SHA-256-Prüfsummen und erstellt die Zielordner. Bei abweichenden
vorhandenen lokalen Dateien bricht es ab. Ein vorhandenes Journal niemals
ungeprüft mit dem NAS-Snapshot überschreiben. Die NAS-Dateien müssen zum
gewählten Manifest passen; `*.before-*` und `*.incoming-*` sind keine aktuellen
Eingabedateien und werden nicht wiederhergestellt.

Die aktive Journal-Datenbank bleibt **lokal**. Den Bot nicht direkt auf der
SQLite-Datei des NAS betreiben. Die Tagessteuerungsdatei
`trading_bot_24_7_state.json`, Prozess-Locks, alte Datenbank-Backups und
historische Backtest-Ausgaben werden nicht auf den neuen Rechner übernommen.
Seine erste Pipeline synchronisiert die Broker-Daten und berechnet die
Backtest-Historie bis zum letzten abgeschlossenen Handelstag erneut.

Falls der alte Rechner seit dem Snapshot wieder gehandelt hat, dort vor dem
Umzug einen neuen konsistenten Snapshot mit `scripts/export_nas_data.py` und
die Scanner-Eingaben mit `scripts/export_nas_runtime_inputs.py` exportieren.
Die vollständigen Exportbefehle stehen in
[`new_computer_nas_setup.md`](new_computer_nas_setup.md).

### Weitere lokale Dateien und Verzeichnisse

Die aktive Symbol-/Regelauswahl und die Regel-Stack-JSON liegen bereits im
Repository unter `statistic\spx_adx_di_5min_2024_02_03`; unverändert übernehmen.
`config\assets.csv` ist zusätzlich im Repo gesichert. Die Rohdaten und das
Journal sind wegen ihrer Größe und ihres laufenden Zustands auf dem NAS.

Für ThetaData-Nachladungen die private Datei vom bisherigen Rechner separat
nach `D:\PycharmProjects\IBKR_TradingBot\config\thetadata_creds.txt` übertragen.
Diese Datei ist absichtlich nicht in Git enthalten. Der reguläre TWS-Download
und Broker-Handel benutzen die angemeldete TWS; dafür ist keine ThetaData-
Anmeldung erforderlich.

```powershell
New-Item -ItemType Directory -Force D:\Trading\logs | Out-Null
New-Item -ItemType Directory -Force D:\Trading\journal | Out-Null
New-Item -ItemType Directory -Force D:\Trading\state | Out-Null
& $python .\scripts\verify_runtime_layout.py
& $python -m unittest discover -s tests -p test_consolidated_runtime.py
```

Erwartet werden `"status": "ok"` und erfolgreiche Tests. Das bestätigt die
Code-/Konfigurationsstruktur, nicht die Aktualität sämtlicher Kursdaten oder
die TWS-Verbindung. TWS auf dem neuen Rechner anmelden und API-Zugriff sowie
die unten genannten Ports/Konten prüfen. Offene Positionen, Orders und
wiederhergestellte Strategie-Zustände beim ersten Start abgleichen.

Vor dem Handelswechsel den Bot auf dem bisherigen Rechner beenden. Die lokale
Lockdatei verhindert keinen parallelen Bot auf einem anderen Rechner. Nur ein
Rechner soll Orders für dieselben Konten erzeugen.

### Erster Vorbereitungslauf ohne Orders

Für den ersten Funktionstest genügt die Paper-TWS auf Port 7496. Dieser Lauf
führt Journal-Sync, Rohdaten-Aktualisierung und den Backtest aus. Mit
`--skip-live-trading` endet er danach ohne Live-Signalphase und ohne Orders:

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
& .\.venv\Scripts\python.exe -u .\scripts\run_trading_day.py --host 127.0.0.1 --port 7496 --account DU9372630 --workers 8 --skip-live-trading
```

Optionsstrategien werden erst mit ihren jeweiligen `--enable-*`-Schaltern
aktiviert. Die Workerzahl an CPU und RAM des neuen Rechners anpassen; `24` im
unten stehenden bisherigen Handelsbefehl nicht ungeprüft übernehmen. Der
Vorbereitungslauf speichert erfolgreiche Sync-/Download-/Backtest-Phasen.
Die Live-Phase bleibt offen und kann später über den regulären Handelsstart
ausgeführt werden. Einen vollständigen Dry-Run mit Live-Phase nicht mit dieser
Vorbereitungsvariante verwechseln: Er kann `live_trading` für den Tag bereits
als abgeschlossen markieren.

## 1. Vor dem Start

1. Beide TWS-Instanzen müssen angemeldet sein und API-Verbindungen erlauben.
2. Paper-TWS muss auf `127.0.0.1:7496` laufen und das Konto `DU9372630`
   melden.
3. Production-TWS muss auf `127.0.0.1:7497` laufen und das Konto `U7492180`
   melden.
4. Der Start sollte deutlich vor `09:35 ET` erfolgen. Allein der Rohdaten-
   Download kann rund 30 Minuten dauern; die Backtest-Dauer hängt vom Rechner
   und Datenstand ab. Empfohlen ist zunächst `05:00 ET`; die tatsächliche
   Laufzeit auf dem neuen Rechner anhand des Performance-Logs prüfen.
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

$python = 'D:\PycharmProjects\IBKR_TradingBot\.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force D:\Trading\logs | Out-Null

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
  -FilePath $python `
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
.\.venv\Scripts\python.exe .\scripts\run_data_backtest_catchup.py
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
