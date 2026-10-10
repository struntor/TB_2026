# Repository-Konsolidierung

Stand: 10.10.2026. Zielrepository: `https://github.com/struntor/TB_2026.git`.
Lokaler Checkout: `D:\PycharmProjects\IBKR_TradingBot`.

## Umfang

Die Tagespipeline benötigt keinen Code mehr aus dem separaten Checkout
`D:\PycharmProjects\PythonProject` (`TB_Prod_V1.md`). Übernommen wurden 40
Python-Laufzeitdateien, drei aktive Auswahl-Konfigurationen und sechs
Unit-Testmodule. Die 49 kopierten Dateien sind byteidentisch zur bisherigen
Arbeitskopie; das schließt dort vorhandene, noch nicht committete Änderungen ein.
Die Herkunft und SHA-256-Prüfsummen stehen in
[`runtime_migration_manifest.json`](runtime_migration_manifest.json).

Es handelt sich um eine Zusammenführung des benötigten Laufzeitcodes, nicht um
eine Zusammenführung der Git-Historien oder aller historischen Forschungsprojekte.
Das alte Repo wurde weder gelöscht noch verändert oder archiviert. Es wurden
keine Strategie-Regeln, Kontofreigaben oder Orderparameter geändert.

## Pfade

| Funktion | Pfad relativ zum Bot-Repository |
| --- | --- |
| Manueller Tagesstart | `scripts/run_trading_day.py` |
| 24/7-Steuerung | `scripts/run_trading_bot_24_7.py` |
| Orchestrierung | `src/bot/daily_orchestrator.py` |
| Rohdaten-Downloader | `Daten_Download/Download_Asset_Data.py` |
| Baseline-Backtest | `scripts/run_manual_regime_baseline_v1.py` |
| Regel-Engine und Live-Einstieg | `statistic/spx_adx_di_5min_2024_02_03/` |
| Benötigte Research-Helfer | `research/` |
| Journal, Execution, Optionsstrategien | `src/` |

Die verschachtelte Engine-Struktur bleibt zunächst erhalten, damit relative
Importe und eingefrorene Versions-Prüfsummen nicht unnötig geändert werden.
`.gitattributes` verhindert Git-Zeilenenden-Konvertierung dieser Engine-Dateien.

## Daten und Zugangsdaten

Ergänzung vom 10.10.2026: Für den Rechnerumzug werden die notwendigen Daten als
geprüfter Snapshot nach `\\Synology_NAS\home\Rohdaten` und
`\\Synology_NAS\home\Journal` übertragen. Die konkrete Installation und
Wiederherstellung beschreibt [Neuer Rechner mit NAS-Daten](new_computer_nas_setup.md).
Das Repository enthält jetzt außerdem die Python-Paketversionen, das
Downloader-Universum und die vom Baseline-Backtest benötigte
`current_baseline_rule_stack_2026_07_04.json`.

- Die aktive Laufzeitkopie von Rohdaten und Journal bleibt unter `D:\Trading`.
  NAS-Snapshots werden getrennt vom Git-Repository gesichert.
- Historische Research-Ausgaben wurden nicht mitkopiert. Einzelne Analyse-/Plot-
  Hilfsprogramme benötigen weiterhin ihre jeweils dokumentierten Ergebnisdateien.
- Die bestehende Python-Umgebung unter `D:\PycharmProjects\.venv` wird hier weiter
  benutzt. Auf dem neuen Rechner wird eine Repo-lokale `.venv` mit
  `requirements.txt` eingerichtet.
- Die lokale ThetaData-Zugangskonfiguration wurde nach
  `config/thetadata_creds.txt` übernommen und ist ausdrücklich in `.gitignore`
  ausgeschlossen. Auf einem anderen Rechner muss sie separat sicher eingerichtet
  werden; sie gehört niemals in einen Commit.

## Verifikation

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe .\scripts\verify_runtime_layout.py
D:\PycharmProjects\.venv\Scripts\python.exe -m unittest discover -s tests -p test_consolidated_runtime.py
D:\PycharmProjects\.venv\Scripts\python.exe -m unittest discover -s tests -p test_daily_orchestrator.py
D:\PycharmProjects\.venv\Scripts\python.exe -m unittest discover -s tests -p test_manual_trading_day_launcher.py
D:\PycharmProjects\.venv\Scripts\python.exe -m unittest discover -s statistic/spx_adx_di_5min_2024_02_03 -p test_*.py
```

Ergebnis am 10.10.2026: alle 46 Tests erfolgreich. Der Offline-Layoutcheck
blockiert Netzwerkverbindungen und Dateizugriffe auf den alten Checkout,
importiert die zentralen Laufzeitmodule aus dem neuen Repo und prüft die
versionierten Strategie-Hashes sowie den aktiven Basket (50 Symbole, 318
freigegebene Symbol/Regel-Paare). Die Windows-interne Eventloop-Socketpair wird
vor Installation dieser Netzwerksperre initialisiert.

Die CLI-Hilfe von Tagespipeline, Live-Engine, Baseline-Backtest und Downloader
wurde ebenfalls erfolgreich ausgeführt. Es wurden keine TWS-Verbindungen,
Downloads, vollständigen Backtests oder Orderläufe zur Migration gestartet.
Die Prüfung ersetzt keinen anschließenden kontrollierten Paper-Lauf.

## Betrieb und Git-Sicherung

Der Startbefehl im [Runbook](dual_trading_bot_start_runbook.md) bleibt gleich.
Ein bereits laufender Prozess wird durch diese Migration nicht neu gestartet;
der neue Code-Pfad gilt beim nächsten Start. Keine zweite Bot-Instanz parallel
starten. Das alte Repo zunächst als Rückfallstand behalten.

Die Migration und alle zuvor ausstehenden Änderungen dieses Repositorys werden
auf ausdrücklichen Benutzerwunsch gemeinsam gesichert. Der veröffentlichte Stand
ist über `git log -1` und `git status -sb` nachvollziehbar. Rohdaten und Journal
liegen auf dem NAS; Forschung unter `D:\Trading` wird dadurch nicht versioniert.
