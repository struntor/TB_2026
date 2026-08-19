# Architecture Draft

## Pipeline

1. Basket auswählen
2. `BasketRequest` bauen
3. aktuelle Bars über IB-Datenadapter laden
4. Bars an die Regel-Engine übergeben
5. Tages-Trades erhalten
6. Risiko und Positionsgröße bestimmen
7. Orders an IB senden
8. Fills und Status speichern

## Komponenten

### `bot`

Steuert den Tagesablauf und ruft die anderen Schichten in der richtigen Reihenfolge auf.

### `data`

Lädt Markt- und ggf. Snapshot-Daten über IB.

### `engine`

Bindet die bestehende Regel-Engine an, ohne Brokerlogik hineinzuziehen.

### `risk`

Berechnet Positionsgröße, Tagesrisiko, Exposure-Limits und Kill-Switches.

### `execution`

Sendet und überwacht Orders über IB.

### `models`

Gemeinsame Datenmodelle für Basket, Bars, Signale, Trades, Orders und Fills.

## Offene Entscheidungen

- Welche Asset-Klassen gehören in den ersten Basket?
- Soll IB nur für Live/near-live genutzt werden oder auch für historische Replay-Daten?
- Welche Money-Management-Regel ist die erste verbindliche Version?
- Wird der Bot intraday-only oder auch über Session-Grenzen hinweg betrieben?

