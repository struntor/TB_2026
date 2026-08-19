# Ressourcenmessung des Trading-Bots

Die Start-Pipeline misst jede externe Phase separat:

1. TWS-Journal-Synchronisation
2. Rohdaten-Download
3. Backtest
4. Live-Trading-Prozess

Die normalen Ablaufmeldungen bleiben in
`D:\Trading\logs\trading_bot_24_7.log`. Die Ressourcenwerte werden als ein
JSON-Objekt pro Phase und Zeile in
`D:\Trading\logs\trading_bot_performance.jsonl` geschrieben.

## Installation

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe -m pip install -r D:\PycharmProjects\IBKR_TradingBot\requirements-performance.txt
```

## Gemessene Werte

- Laufzeit und Exit-Code der Phase
- Anzahl der Prozesse im gestarteten Prozessbaum
- CPU-Zeit, durchschnittlich genutzte Kerne und beobachteter Spitzenwert
- höchster Working Set und Private Memory des Prozessbaums
- gelesene und geschriebene Bytes des Prozessbaums
- gesamte Systemlast, verfügbarer RAM sowie systemweiter Datenträger- und Netzwerkverkehr
- CPU-, RAM- und I/O-Werte der parallel laufenden TWS

Die Prozessbaumwerte gehören zur gestarteten Phase und schließen deren
Worker-Prozesse ein. Datenträger- und Netzwerkwerte unter `system` gelten für
den ganzen Rechner. Summierter Working Set kann gemeinsam genutzte
Speicherseiten mehrfach zählen; für die Dimensionierung sind deshalb auch
`peak_private_bytes` und `minimum_available_memory_bytes` relevant.

Vor dem Backtest wird der angeforderte Prozesspool einschließlich echter
Worker-Aufgaben geprüft. Falls Windows Named Pipes, Handles, RAM oder die
Auslagerungsdatei die Worker-Erzeugung verhindern, läuft die vollständige
Berechnung mit einem pipe-freien Thread-Fallback weiter. Ein späterer
Infrastrukturfehler im Prozesspool löst denselben vollständigen Fallback aus;
ein fachlicher Fehler wird dagegen weiterhin als Fehler gemeldet.

## Sonntäglicher Vorbereitungslauf

Der folgende Befehl bereitet Montag vor, synchronisiert den letzten
Handelstag, lädt die Rohdaten bis Freitag und führt den Backtest bis zum letzten
verfügbaren Handelstag aus. Live-Trading und Orders werden nicht gestartet.

```powershell
cd D:\PycharmProjects\IBKR_TradingBot
D:\PycharmProjects\.venv\Scripts\python.exe -u .\scripts\run_trading_bot_24_7.py `
  --once-day 2026-08-17 `
  --skip-live-trading `
  --workers 24 `
  --port 7496
```

Die abgeschlossenen Vorbereitungsphasen werden im Pipeline-State vermerkt.
Ein späterer Start für denselben Handelstag überspringt sie und kann den
Live-Teil ausführen.

## Optionen

- `--performance-log-path PATH`: anderes Performance-Logfile
- `--performance-sample-seconds 0.5`: Abtastintervall in Sekunden
- `--skip-live-trading`: nur zusammen mit `--once-day`; führt ausschließlich
  die Vorbereitungsphasen aus
