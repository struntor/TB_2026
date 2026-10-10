# IBKR Trading Bot

Trading-Bot mit IB-API-Anbindung, separatem Money Management und integrierter Regel-Engine.

## Aktueller Betrieb

Seit der Konsolidierung vom 10.10.2026 liegt der für die Tagespipeline benötigte
Projektcode vollständig in diesem Repository (`TB_2026`). Das alte
`PythonProject` ist für diesen Startpfad nicht mehr erforderlich.

- [Start des dualen Trading-Bots](docs/dual_trading_bot_start_runbook.md)
- [Repository-Struktur, Migration und Offline-Prüfung](docs/consolidated_repository.md)
- [Installation auf einem neuen Rechner mit NAS-Daten](docs/new_computer_nas_setup.md)

Die folgenden Architekturabschnitte beschreiben die ursprüngliche Planung.

## Ziel

- aktuelle Kursdaten über einen eigenen IB-Datenadapter laden
- Basket-Auswahl vom Bot bestimmen lassen
- Regel-Engine mit den geladenen Bars füttern
- Trades des Tages als Trade-Intents zurückbekommen
- Money Management außerhalb der Regel-Engine anwenden
- Orders über IB ausführen

## Leitplanken

- keine Laufzeitabhängigkeit vom alten `PythonProject`-Checkout
- Regel-Engine bleibt brokerfrei
- Money Management bleibt außerhalb der Regel-Engine
- Datenadapter und Execution-Adapter werden getrennt gehalten
- jeder Lauf soll nachvollziehbar und restartfähig sein

## Vorgeschlagene Struktur

```text
IBKR_TradingBot/
  README.md
  docs/
    architecture.md
    requirements.md
    decision_log.md
  src/
    bot/
    data/
    engine/
    risk/
    execution/
    models/
  configs/
  tests/
  runs/
  ai_agents/
```

## Nächste Arbeitsschritte

1. Basket- und Request-Modell festlegen
2. IB-Datenadapter spezifizieren
3. Money-Management-Regeln definieren
4. Execution-Flow für IB abgrenzen
5. Restart-Handoff-Format für laufende Experimente definieren

