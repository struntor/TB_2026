# Pruefbericht: 15DTE Damage Bear-Call Alternative

- Strategy ID: `spx-15dte-damage-bear-call`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `statistical_evidence`
- Empfohlener Arbeitszustand: `rejected`

## 1. Urteil in Kurzform

Der getestete Bear-Call-Zweig ist anhand von Code, Einzeltrades und Summary klar negativ. Keine der DTE-/Exit-Varianten ist konservativ in beiden Samples positiv. Das Damage-Signal ist damit als Risk-Off-Signal fuer den Put-Credit-Core belegt, nicht als direktes Bear-Call-Entry-Signal. `rejected` ist sachlich richtig.

## 2. Gepruefte Quellen

### Code

- `research/test_option_omega_bear_call_strategy.py`: Signal-, Quote-, Strike-, Expiration- und Recovery-Exit-Logik.

### Ergebnisartefakte

- `.../thetadata_bear_call_strategy/bear_call_entries_raw.csv`: 21 Signale x DTE, ein 30DTE-Skip 2025.
- `.../bear_call_variant_trades.csv`.
- `.../bear_call_summary.csv`: 15/21/30DTE, Expiration/Recovery, Mid/Cons.
- `.../bear_call_filter_diagnostics.csv`.

### Dokumentation

- `ai_agents/stand_15dte_bear_call_strategy_2026-06-09.md`.

## 3. Tatsaechlich implementierte Strategie

- Signal SPY 15:55: `low10_near AND off_5d_high_2pct`.
- Entry naechster Handelstag 13:35; Short Call ca. +0,25 Delta, Long Call 10 Punkte hoeher; DTE 15/21/30.
- Varianten: bis Expiration 15:55 oder Recovery-Exit 13:35, sobald SPY ueber Signal-Close und `dist_low10 > 0,003` liegt.
- Konservativ: Short am Bid/Long am Ask; Exit umgekehrt. Nichtpositive Credits werden geskippt.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 2025 H1 Expiration konservativ negativ | Summary | CSV gelesen | `artifact_verified` | 15/21/30DTE: -1.240/-1.210/-80 |
| 2024 Expiration konservativ negativ | Summary | CSV gelesen | `artifact_verified` | -2.265/-6.240/-6.310 |
| Recovery begrenzt Mid-DD, erzeugt aber keinen konservativen Edge | Summary | CSV gelesen | `artifact_verified` | alle sechs Sample/DTE-Kombinationen negativ |
| Credit-Filter robust | Diagnose/Dokument | 2025 positiv, 2024 weiter negativ | `artifact_verified` | widerlegt |

## 5. Methodische Risiken

- Sehr kleine Stichprobe: sechs 2025- und 14/15 2024-Trades je DTE.
- DTE-, Exit- und nachtraegliche Credit-/Clusterfilter auf denselben Signalen.
- Recovery-Regel ist ex ante, aber Quotes/Slippage in Stressphasen bleiben kritisch.
- Kein unabhängiges OOS nach Ideenfindung; „2025 OOS“ ist historischer Projektname.

## 6. Money Management und Portfolio

Nur Trade-Exit untersucht. Kein Sizing oder Nachweis, dass der Bear Call das Portfolio-/Core-Risiko verbessert. Wegen negativer konservativer PnL nicht weiterzufuehren.

## 7. IBKR-Integrationsreife

Vor Integration verworfen; kein Adapter, Dry-Run, Paper-Test oder Order-Gate.

## 8. Widersprueche und offene Punkte

- Das Signal kann offene Bull-Put-Risiken anzeigen, besitzt aber keinen nachgewiesenen Bear-Call-Entry-Edge.
- Positiver Februar 2025 wird durch April und 2024 ueberkompensiert.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `statistical_evidence`
- Empfohlene Stufe: `statistical_evidence`
- Begruendung: negative statistische Evidenz ist ausreichend dokumentiert; der Arbeitszustand `rejected` bleibt entscheidend.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Kein weiterer Test fuer diese Strategy ID. Nur eine fachlich neue Rebound-Entry-Hypothese darf als neue Strategy ID mit vorab fixierter Regel angelegt werden.
