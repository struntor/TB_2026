# Hardware-Dimensionierung für den 24/7-Trading-Bot

Stand: 16. August 2026

## Zielsystem

Geprüftes Angebot:

- HP EliteDesk 800 G4 DM
- Intel Core i5-8500T
- 6 Kerne / 6 Threads, 2,10 GHz Basis, bis 3,50 GHz Turbo, 35 W
- im geöffneten Angebot zunächst 8 GB RAM und 256 GB SSD
- auswählbar: 16 GB für 56 EUR Aufpreis, 32 GB für 85 EUR Aufpreis und
  512 GB SSD für 25 EUR Aufpreis

Produktseite:
https://www.refurbed.de/p/hp-elitedesk-800-g4/41783b/?offer=19006367

Intel-Spezifikation:
https://www.intel.com/content/www/us/en/products/sku/129941/intel-core-i58500t-processor-9m-cache-up-to-3-50-ghz/specifications.html

## Vergleichbarer Backtest

Alle drei Läufe verwenden exakt:

- Zeitraum 2024-01-01 bis 2026-08-16;
- dieselben 50 Symbole und Rohdaten;
- denselben Config- und Data-Revision-Hash;
- 1.099 ausführbare Trades;
- Netto-P/L 37.370,89 USD;
- Profit Factor 1,9543;
- MaxDD -1.134,44 USD.

Die 8- und 12-Worker-Läufe wurden in getrennte Benchmark-Journale geschrieben.
Es bestand keine TWS- oder Orderverbindung. Der 24-Worker-Wert stammt aus dem
vollständigen Vorbereitungslauf desselben Datenstands.

| Worker | Laufzeit | Ø genutzte Kerne | Peak Prozesse | Peak Working Set | Peak Private Memory |
|---:|---:|---:|---:|---:|---:|
| 8 | 203,42 s | 6,11 | 10 | 1,22 GiB | 7,50 GiB |
| 12 | 178,67 s | 8,03 | 14 | 1,76 GiB | 10,83 GiB |
| 24 | 168,74 s | 12,86 | 26 | 3,34 GiB | 20,76 GiB |

Die fachlichen Journalinhalte der 8- und 12-Worker-Läufe sind identisch:

- `signal_decisions`: 2.668, sortierter Inhalts-Hash identisch;
- `decision_factors`: 63.131, sortierter Inhalts-Hash identisch;
- `trades`: 1.099, sortierter Inhalts-Hash identisch;
- `trades.csv` und `equity_curve.csv`: identische Artefakt-Hashes.

Der rohe `decision_records.jsonl`-Hash unterscheidet sich teilweise, weil die
parallel berechneten Symbolblöcke in anderer Reihenfolge fertig werden. Der
sortierte fachliche Inhalt ist unverändert.

## Bewertung des Arbeitsspeichers

Das beobachtete Peak-Private-Memory wächst annähernd linear um 0,83 GiB je
zusätzlichem Worker. Aus den drei Messpunkten ergibt sich für sechs Worker eine
Schätzung von etwa 5,85 GiB für den Backtest-Prozessbaum.

Zusätzlich sind auf dem Zielrechner ungefähr einzuplanen:

- eine TWS-Instanz: in den Messläufen etwa 1,8 bis 2,0 GiB Private Memory;
- Windows, Dienste, Virenscanner und Dateicache: typischerweise mehrere GiB;
- Supervisor, Download, Journal und Sicherheitsreserve.

Damit sind 16 GB für einen dedizierten Rechner mit sechs Workern voraussichtlich
ausreichend, aber nicht großzügig. Zwölf Worker sind auf 16 GB nicht sinnvoll:
Backtest und eine TWS-Instanz benötigen bereits ungefähr 12,6 bis 12,8 GiB
Private Memory, bevor Windows und weitere Prozesse berücksichtigt werden.

## Bewertung von CPU und Laufzeit

Der i5-8500T besitzt sechs Kerne und kein Hyper-Threading. Die sinnvolle
Konfiguration ist deshalb:

```text
--workers 6
```

Acht oder zwölf Worker würden auf diesem Prozessor hauptsächlich um dieselben
sechs Kerne konkurrieren und zusätzlich RAM reservieren. Schon auf dem
16-Kern-Messrechner war der Unterschied zwischen acht und zwölf Workern nur
24,75 Sekunden. Selbst wenn der ältere 35-W-i5 den Backtest wesentlich langsamer
als der aktuelle AMD ausführt, besteht gegenüber dem Handelsstart eine sehr
große Zeitreserve. Der Rohdaten-Download bleibt wahrscheinlich die längste
Pipelinephase.

## Speicherplatz

Aktueller Platzbedarf der für den Produktionsbot relevanten Verzeichnisse:

| Bereich | Belegung |
|---|---:|
| Rohdaten | 3,56 GiB |
| Journal einschließlich vorhandener Reports/Baselines | 7,53 GiB |
| Logs | 0,55 GiB |
| IBKR_TradingBot-Code | 0,31 GiB |
| Manual-Regime-Engine und Forschungsartefakte im Engine-Verzeichnis | 0,51 GiB |

256 GB reichen heute aus. Für einen unbeaufsichtigten 24/7-Rechner ist 512 GB
trotzdem die bessere Wahl, weil Windows, TWS, Python-Umgebung, Backtest-Baselines,
Journal-Backups und Logs über Jahre wachsen. Der ausgewiesene Aufpreis von
25 EUR ist im Verhältnis zur Betriebssicherheit sinnvoll.

## Empfehlung

### Technisch ausreichend und günstig

- i5-8500T
- 16 GB RAM
- 512 GB SSD
- Bot mit `--workers 6`
- Windows-Auslagerungsdatei systemverwaltet lassen
- keine IDE, Browser oder Research-Jobs parallel betreiben
- Logs und alte Baseline-Artefakte regelmäßig archivieren

### Bevorzugte 24/7-Konfiguration

Auf der geöffneten Produktseite kostet 32 GB nur 29 EUR mehr als 16 GB. Deshalb
ist die klare Empfehlung:

- i5-8500T oder i7-8700T
- **32 GB RAM**
- **512 GB SSD**
- zunächst weiterhin `--workers 6`; beim i7-8700T kann später ein isolierter
  8-/10-/12-Worker-Test auf dem Zielgerät durchgeführt werden

Die 32-GB-Ausführung entfernt das Pagefile-/TWS-Risiko fast vollständig und
lässt Raum für wachsende Journale, Windows-Updates und eine zweite TWS-/Gateway-
Instanz. Bei einem 24/7-Handelssystem ist der geringe Mehrpreis gegenüber 16 GB
die sinnvollste Reserve.

## Empfohlener Startparameter auf dem i5-8500T

```powershell
D:\PycharmProjects\.venv\Scripts\python.exe -u `
  D:\PycharmProjects\IBKR_TradingBot\scripts\run_trading_bot_24_7.py `
  --port 7496 `
  --workers 6
```

Order- und Kontoschalter sind bewusst nicht Bestandteil dieser
Hardwareempfehlung.

## Messartefakte

- `D:\Trading\performance\manual_regime_workers_8_process_20260816\performance.jsonl`
- `D:\Trading\performance\manual_regime_workers_12_process_20260816\performance.jsonl`
- `D:\Trading\logs\trading_bot_performance.jsonl`
- `D:\PycharmProjects\IBKR_TradingBot\scripts\benchmark_manual_regime_backtest.py`

