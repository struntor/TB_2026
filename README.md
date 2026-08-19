# IBKR Trading Bot

Neues, sauberes Projekt für den Trading-Bot mit IB-API-Anbindung, separatem Money Management und externer Regel-Engine.

## Ziel

- aktuelle Kursdaten über einen eigenen IB-Datenadapter laden
- Basket-Auswahl vom Bot bestimmen lassen
- Regel-Engine mit den geladenen Bars füttern
- Trades des Tages als Trade-Intents zurückbekommen
- Money Management außerhalb der Regel-Engine anwenden
- Orders über IB ausführen

## Leitplanken

- keine Vermischung mit dem alten `PythonProject`
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

