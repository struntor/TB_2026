# Pruefbericht: SPY Failed-Auction Hard-6 First-Touch Options

- Strategy ID: `spy-failed-auction-poc-hard6-first-touch-options`
- Audit-Datum: `2026-07-17`
- Audit-Typ: `causal_sibling_hypothesis_review`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `exploration`

## 1. Urteil in Kurzform

Hard-6 ist als weichere Signalhypothese sauber getrennt getestet. Es erhoeht die strikte Stichprobe deutlich, verbessert die POC-Touchrate aber nicht materiell und erzeugt im konservativ gegateten Options-Grid keinen robusten Kandidaten. Die Variante wird geparkt, nicht in Hard-12 integriert.

## 2. Gepruefte Quellen

- `D:\Trading\research\spy_failed_auction_poc_fly\signal_heatmaps\REPORT.md`
- `D:\Trading\research\spy_failed_auction_poc_fly\hard6_signal_option_grid_initial_touch_v1\REPORT.md`
- `D:\PycharmProjects\PythonProject\statistic\spy_failed_auction_poc_fly_research\run_failed_auction_signal_heatmaps.py`
- `D:\PycharmProjects\PythonProject\statistic\spy_failed_auction_poc_fly_research\sweep_hard_signal_options.py`

## 3. Tatsaechlich implementierte Strategie

Gleicher Failed-Auction-Kontext und strikter First-Touch-Guard wie Hard-12, aber Signalbestaetigung bereits nach sechs aufeinanderfolgenden Inside-Value-Closes. Entry ist weiterhin der folgende 5-Minuten-Bar-Open. Optionsgrid und Kostenannahmen sind identisch zum Hard-12-Vergleich.

## 4. Ergebnispruefung

- 143 Hard-6-Signale, davon 108 strikte Initial-Touch-Einstiege.
- 74/108 POC-Touches bis Next+2 = 68,5 Prozent.
- 525 Parameterzellen ueber 1-3 DTE, Body-Offset und Fly-Breiten.
- Null Zellen bestehen gleichzeitig alle drei Zeitbloecke, positive Nachbarschaft und den 22,63-USD-Kalibrierungsfehler-Gate.
- 3DTE bleibt negativ; 2DTE ist im juengsten Zeitblock fuer POC-zentrierte symmetrische Breiten negativ.

## 5. Methodische Risiken

Auch dieser Lauf verwendet den Optionspreis-Proxy. Hard-6 und Hard-12 ueberlappen an vielen Tagen, besitzen aber unterschiedliche Entry-Zeitpunkte. Ein Vergleich der Rohzahlen ist deshalb keine unabhaengige OOS-Pruefung. 2026-Zellen bleiben klein.

## 6. Money Management und Portfolio

Nicht begonnen. Ohne robusten Strukturkandidaten waere Sizing-Forschung verfrueht.

## 7. IBKR-Integrationsreife

Keine Integration und keine Produktionsfreigabe. Es existiert weder ein Contract-Resolver noch ein Combo-Order-Paper-Test fuer diese Variante.

## 8. Widersprueche und offene Punkte

Die Underlying-Touchrate bleibt trotz 52 Prozent mehr strikter Einstiege praktisch gleich zu Hard-12. Die Optionsoekonomie verschlechtert sich dennoch. Das zeigt, dass Underlying-Trefferquote und Fly-P/L nicht austauschbar sind.

## 9. Reifegradentscheidung

- Empfohlene Stufe: `exploration`
- Arbeitszustand: `parked`
- Produktionsberechtigt: `false`
- Begruendung: saubere Negativ-/Neutral-Evidenz ohne robusten Optionskandidaten.

## 10. Naechster minimaler Test

Keine weitere Parametersuche. Nur neue unabhaengige Daten oder eine fachlich neue, vorab formulierte Struktur-/Exit-Hypothese rechtfertigen eine Wiederaufnahme.
