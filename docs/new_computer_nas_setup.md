# Trading-Bot auf einem neuen Windows-Rechner

Die Installation besteht aus dem Repository `TB_2026` und einem geprüften
Datenstand auf `\\Synology_NAS\home`. Der dokumentierte Aufbau verwendet wie
bisher `D:\PycharmProjects\IBKR_TradingBot` und `D:\Trading`. Die bisherige Engine
enthält feste Windows-Pfade; ein anderer Laufwerksaufbau ist damit noch nicht
vollständig unterstützt. Die Pfadübersicht steht in `config/deployment.json`.

## 1. Programme und Repository

Git, Python 3.13 (64 Bit) und TWS installieren. Die NAS-Freigabe im Windows-
Explorer mit dem eigenen NAS-Benutzer öffnen. Die Zugangsdaten werden durch
Windows verwaltet und gehören nicht ins Repository.

```powershell
New-Item -ItemType Directory -Force D:\PycharmProjects | Out-Null
git clone https://github.com/struntor/TB_2026.git D:\PycharmProjects\IBKR_TradingBot
cd D:\PycharmProjects\IBKR_TradingBot
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Der Migrationsstand muss zuvor auf GitHub committet und gepusht worden sein.
Ein Clone eines älteren Stands enthält diese Einrichtung noch nicht.

## 2. Daten aus dem NAS wiederherstellen

Auf dem NAS liegen:

- `home\Rohdaten\Rohdaten_*.csv`: Kursdaten;
- `home\Rohdaten\assets.csv`: Downloader-Universum;
- `home\Rohdaten\runtime_inputs\research\...`: Auswahl und öffentliche
  Earnings-Caches für den IVR70-Scanner;
- `home\Journal\trading_journal.sqlite`: konsistente Datenbank-Sicherung;
- `home\Journal\strategy_state\*.json`: Zustand der Optionsstrategien;
- `home\Journal\*manifest_*.json`: Dateigrößen und SHA-256-Prüfsummen.

```powershell
.\.venv\Scripts\python.exe .\scripts\restore_nas_data.py --nas-home '\\Synology_NAS\home'
```

Das Skript prüft zuerst alle NAS-Dateien gegen die neuesten Manifeste und
überträgt sie anschließend nach `D:\Trading`. Abweichende vorhandene lokale
Dateien führen zu einem Abbruch, damit kein bestehendes Journal überschrieben
wird. Genügend freien lokalen Speicher für ca. 15 GB einplanen, zusätzlich
Platz für künftige Journal- und Backtest-Ausgaben.

Der Bot schreibt seine aktive SQLite-Datenbank weiterhin auf die lokale SSD.
Die NAS-Datei ist ein Sicherungs-/Umzugsstand. Tagessteuerungs-State und
Prozess-Lock werden nicht vom alten Rechner übernommen. Der neue Rechner führt
seine eigene Tagesvorbereitung aus; historische Backtest-Ausgaben werden dabei
bei Bedarf neu erzeugt. Alte Datenbank-Backups und Research-Reports sind für
diesen Start nicht erforderlich und wurden nicht mit übertragen.

## 3. Lokale Zugangsdaten und Prüfung

Für ThetaData-Nachladungen die bestehende private Datei
`config/thetadata_creds.txt` separat sicher übertragen. Sie wird nicht per Git
gesichert. Für den normalen TWS-Handel die entsprechenden TWS-Konten,
API-Berechtigungen und Ports wie im Start-Runbook einrichten.

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_runtime_layout.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_consolidated_runtime.py
```

Vor dem Wechsel den bisherigen Bot beenden. Falls er seit dem NAS-Snapshot
erneut gelaufen ist, zuerst einen neuen Snapshot erstellen und übertragen.
Offene TWS-Positionen und Orders beim ersten Start abgleichen. Nur ein Rechner
soll Orders für dieselben Konten erzeugen.

## 4. Tagesstart

Die Argumente aus `docs/dual_trading_bot_start_runbook.md` verwenden; als Python-
Programm jetzt `.\.venv\Scripts\python.exe` statt der alten gemeinsamen Umgebung
`D:\PycharmProjects\.venv\Scripts\python.exe` verwenden. Die vorhandenen
Paper-/Production-Freigaben bleiben explizite Startschalter. Der erste Test
erfolgt wie vereinbart im Paper-Modus.

## Erneuter NAS-Snapshot vom bisherigen Rechner

```powershell
$stage = 'D:\Trading\tmp\nas_migration_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
.\.venv\Scripts\python.exe .\scripts\export_nas_data.py --nas-home '\\Synology_NAS\home' --stage $stage --preserve-existing
.\.venv\Scripts\python.exe .\scripts\export_nas_runtime_inputs.py --nas-home '\\Synology_NAS\home' --stage $stage
```

Vorhandene abweichende NAS-Dateien bleiben als `*.before-<Zeitstempel>` erhalten.
Die lokalen Originale bleiben ebenfalls erhalten. Ein späteres Entfernen ist
eine eigene, kontrollierte Aufräumaktion.
