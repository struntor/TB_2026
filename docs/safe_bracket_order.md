# Safe Attached Bracket Order

Stand: 2026-07-14

## Implementierung

`src/execution/safe_bracket_order.py` stellt `SafeBracketOrderManager` bereit.
Die Utility validiert Verbindung, Account, eindeutige `orderRef`, Menge,
Preisreihenfolge und Tickgroesse. Sie sendet ein angehaengtes Bracket in dieser
Reihenfolge:

1. Parent mit `transmit=False`
2. Take-Profit mit `parentId=<parent>` und `transmit=False`
3. Stop-Loss mit `parentId=<parent>` und `transmit=True`

Erst das letzte Leg gibt damit das gesamte Bracket in TWS frei. Alle drei Legs
werden auf TWS-Status, Order-/Permanent-ID und Fehlermeldungen geprueft.

Der operative Manual-Regime-Runner verwendet diese Utility fuer
`--place-orders`. Ohne dieses Flag bleibt der Runner ein Signal-only-Dry-Run.
Standardmaessig blockiert die Utility Konten, deren Accountcode nicht mit `DU`
beginnt. Eine spaetere Livefreigabe erfordert im Runner den expliziten Schalter
`--allow-live-account`.

## Verifizierte Paper-Tests vom 2026-07-14

### Nicht marktfähiges Limit-Bracket

- Symbol: IBM, Menge 1
- Parent: Buy-Limit 200
- Target: 210
- Stop: 190
- TWS-Status: Parent `Submitted`, beide Children `PreSubmitted`
- Transmit-Kette: `False / False / True`
- Ergebnis: alle drei Legs `Cancelled`, keine Position, keine offene Order

### Vollstaendiger Fill-/Cleanup-Test

- Symbol: IBM, Menge 1
- Parent: Market-Buy, Fill 220,51
- Target: 224, `PreSubmitted`
- Stop: 214, `PreSubmitted`
- TWS-Fehler: keine
- Cleanup: Children storniert, Position per Market-Sell zu 220,34 geschlossen
- Endzustand: IBM-Position 0, keine offene IBM-Order

Die bereits vorhandenen ALV- und QQQ-Paperpositionen wurden nicht veraendert.

## Reproduzierbare Tests

- `scripts/test_tws_safe_bracket.py`: nicht marktfähiges Submit/Acknowledge/Cancel
- `scripts/test_tws_safe_bracket_fill.py`: Market-Fill, aktive Schutzlegs und Cleanup
- `tests/test_safe_bracket_order.py`: lokale Transmit-, ParentId- und Validierungstests

Die TWS-Skripte verweigern den Start, wenn das Testsymbol bereits eine Position
oder offene Order besitzt, und geben keine Accountnummer aus.

## Noch offene Produktionspunkte

Der getestete Attached-Bracket-Pfad schliesst die fruehere Luecke zwischen
vollstaendigem Parent-Fill und nachtraeglichem Senden der Schutzorders. Noch
nicht abgedeckt sind insbesondere:

- Schutz und Mengenabgleich bei Partial Fills,
- Restart-/Reconnect-Reconciliation anhand `permId` und `orderRef`,
- persistentes Trading-Journal mit Executions und Kommissionen,
- automatische Alarmierung beziehungsweise Notfall-Glattstellung bei einem
  spaeter abgelehnten oder fehlenden Schutzleg.

Bis diese Punkte implementiert und getestet sind, bleibt der Runner im
Reifegrad `bot_integration` und nicht `production`.
