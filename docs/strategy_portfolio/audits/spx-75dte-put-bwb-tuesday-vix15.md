# Pruefbericht: SPX 75DTE Put-BWB (Tuesday lineage / Thursday paper variant)

- Strategy ID: `spx-75dte-put-bwb-tuesday-vix15`
- Audit-Datum: `2026-08-13`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `bot_integration`
- Empfohlener Arbeitszustand: `frozen_candidate`

## 1. Urteil in Kurzform

Entry, Struktur und Exit des OptionOmega-Exports sind mit lokalen SPXW-Quotes
sehr eng reproduziert. Der Paper-Botpfad ist implementiert, unit-getestet und
seit dem 13.08.2026 gegen einen echten TWS-Paper-Fill samt GTC-Zielorder
verifiziert.

## 2. Gepruefte Quellen

### Code

- `spx_75dte_put_bwb_research/run_independent_backtest.py`: unabhaengiger Replay.
- `src/strategies/spxw_75dte_put_bwb.py`: eingefrorenes Live-Signal.
- `src/execution/spxw_put_bwb.py`: SPXW-Selektion und atomare BAG-Orders.
- `src/bot/spxw_75dte_put_bwb_monitor.py`: Zustand, Journal und Order-Lifecycle.

### Ergebnisartefakte

- `D:/Trading/tmp/trade-log(28).csv`: 138 OptionOmega-Trades.
- `D:/Trading/research/spx_75dte_put_bwb/independent_backtest_v1/trades.csv`.
- `D:/Trading/research/spx_75dte_put_bwb/daily_mtm_v1/daily_mtm.csv`.

### Dokumentation

- `docs/spxw_75dte_put_bwb_paper_runbook.md`.

## 3. Tatsaechlich implementierte Strategie

SPXW wird in der aktuellen Paper-Variante donnerstags ab 09:32 ET bei VIX-Open
mindestens 15 gehandelt. Der erste
gelistete Verfall mit mindestens 75 DTE wird verwendet. Der Body liegt am
naechsten gelisteten Strike zu 99% des SPX, die Fluegel liegen +35/-55 Punkte.
Der Einstieg arbeitet jeweils fünf Minuten am aktualisierten Mid minus 0,20,
minus 0,40 und minus 0,65 Punkte. Ohne Fill wird er danach storniert.
Nach Fill liegt eine inverse GTC-Combo bei 40% Gewinn; andernfalls wird bis
Verfall gehalten.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| Entry-Strikes/Verfall stimmen | Export und Exact-Entry-Replay | 136 gemeinsame Zeilen | reproduced | 136/136 exakt |
| Trade-P/L stimmt | unabhaengiger Replay | 135 gemeinsame Abschluesse | reproduced | Korrelation 0,9971 |
| MaxDD stimmt | taegliches MTM | taegliche Optionsmids | reproduced | -13,46% vs. -13,6% |
| Bot erzeugt BAG und Journal | Unit-Dry-Run | fuenf neue Tests | reproduced | bestanden |

## 5. Methodische Risiken

Keine separate unangetastete OOS-Periode der oeffentlichen Vorlage. Mid-Quotes
und modellierte Slippage ersetzen echte Fills. Der einzelne Verfallverlust und
die hohe Trefferquote machen Tail-Risiko zentral.

## 6. Money Management und Portfolio

Ein Kontrakt pro Donnerstag, maximal sieben Positionen. Historisches Spitzenrisiko
10.115 USD. Der Paper-Sleeve ist deshalb auf 10.500 USD begrenzt. Korrelation
mit anderen SPXW-Strategien ist noch nicht im gemeinsamen Paperkonto validiert.

## 7. IBKR-Integrationsreife

TradeIntent, drei Optionslegs, DecisionRecord, Restart-State, eindeutige
OrderRefs, DU-Guard, Credit-BAG und inverse GTC-Zielorder sind implementiert.
Der TWS-Paper-Fill erfolgte am 13.08.2026 um 14:17:56 UTC zu 4,10 Credit; die
inverse GTC-Zielorder wurde zu 2,45 Debit übermittelt. Die laufende
Paper-Reconciliation bleibt vor einer Produktion erforderlich.

## 8. Widersprueche und offene Punkte

Lokaler VIX-Feed und OptionOmega tauschen drei Grenzfaelle um VIX 15. Zwei am
Stichtag offene lokale Signale unterscheiden den MTM-Endwert von der reinen
Abschlusskurve. Live verwendet das IB-Tages-Open des VIX.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: nicht registriert
- Empfohlene Stufe: `paper_trading`
- Begruendung: reproduziert, unit-getestet und mit echtem Paper-Fill; laufende Paperbeobachtung fehlt
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Die dreistufige Version 1.4.0 über mehrere reguläre Donnerstagseinstiege
beobachten und Fill-Stufe, aktuellen Mid, tatsächlichen Credit, Maximalverlust,
GTC-Zielorder und Journal-Brokerbindung vergleichen.

## 11. Mittwoch-Paper-Variante 1.1.0

Der Dienstag-Entry vom 11.08.2026 wurde nicht gefillt. Fuer einen erneuten
Paper-Fill-Test wurde deshalb in Version `1.1.0-paper-wednesday` nur der
Einstiegstag auf Mittwoch verschoben. Struktur, VIX-Schwelle, Limitlogik,
40%-Zielorder und Risiko-Sleeve sind unveraendert. Ein eigener Restart-State
`D:\Trading\state\spxw_75dte_put_bwb_wednesday_v1_1.json` trennt den Versuch
von der bisherigen Dienstagshistorie. Die Strategy-ID bleibt als stabile
Herkunfts-ID unveraendert; Performancezahlen bleiben die des Referenztests.

## 12. Donnerstag-Paper-Variante 1.2.0

Auch der Mittwoch-Entry vom 12.08.2026 wurde nicht gefillt. Fuer einen erneuten
Paper-Fill-Test wurde deshalb in Version `1.2.0-paper-thursday` nur der
Einstiegstag auf Donnerstag verschoben. Alle Struktur-, Preis-, Exit- und
Risikoregeln bleiben unveraendert. Der neue Restart-State lautet
`D:\Trading\state\spxw_75dte_put_bwb_thursday_v1_2.json`.

## 13. Fill-Ladder-Varianten 1.3.0 und 1.4.0

Der isolierte Test vom 13.08.2026 wurde um 14:17:56 UTC (16:17:56 CEST) zu
4,10 Punkten Credit gefüllt. Die Legs waren 7760/+1, 7725/-2 und 7670/+1,
Verfall 30.10.2026. Die inverse GTC-Zielorder liegt bei 2,45 Punkten Debit.
Version `1.3.0-paper-thursday-fill-ladder` führte zunächst zwei Preisstufen.
Die Sensitivitätsanalyse ab 2024 zeigt für Mid-0,65 einen MTM-Gewinn von
8.945 USD bei -3.013 USD Maximaldrawdown; die täglichen P/L-Änderungen
korrelieren mit Mid-0,40 zu 0,986. Version
`1.4.0-paper-thursday-three-stage-fill-ladder` verwendet deshalb Mid-0,20,
Mid-0,40 und Mid-0,65 für jeweils fünf Minuten und storniert danach. Ein Fill
während Änderung oder Storno wird vor dem Statuswechsel erneut geprüft und
sofort durch die Zielorder geschützt.

## 14. Befristetes Weekday-Paper-Experiment 1.5.0

Vom 14.08.2026 bis einschließlich 27.08.2026 darf die Strategie montags bis
freitags einsteigen, um reale Fill-Stufen schneller zu beobachten. VIX-Open
mindestens 15, Struktur, Preisleiter und Exit bleiben unverändert. Im
Experiment gelten maximal 20 gleichzeitige BWB-Positionen; der dedizierte
BWB-Risikodeckel wird nur in diesem Paper-Zeitfenster nicht als Entry-Veto
verwendet. Ab dem 28.08.2026 gelten automatisch wieder Donnerstag, maximal
sieben Positionen und der normale Risikodeckel.
