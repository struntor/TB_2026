# Pruefbericht: SPX Flyagonal 12/15DTE

- Strategy ID: `spx-flyagonal-12-15dte`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Die Konstruktion und mehrere Ergebnis-Ledger sind vorhanden und intern auswertbar. Der direkte Monday-Replay ist jedoch negativ; der positive Management-Kandidat (`+6979`) wurde auf demselben, nur 54 Trades grossen OptionOmega-Export nach mehreren Varianten gewaehlt. Das ist Exit-/Filter-Research, kein belastbares Portfolio-Money-Management und kein OOS-Nachweis.

## 2. Gepruefte Quellen

### Code

- `single_strategy_lab/research_spx_flyagonal_monday_cycle.py`: Monday-Auswahl, 12/15-Kalendertage-Zielverfaelle, 10-Punkte-Strike-Rundung, Quote-Mids, Intrinsic-Fallback ab Verfall, vier Haltefristen, Filter und PT-Risk-Overlays.
- `single_strategy_lab/flyagonal_spx_strategy_spec.json`: fuenf Legs, Screenshot-Rekonstruktion, Offsets, Kosten- und geplante Train/Test-Regeln.

### Ergebnisartefakte

- `research_runs/2026-06-26_13-43-33/outputs/research_trades.csv`: 3360 Variantenzeilen; 35 geplante Entries, eine kritische Entry-Quote-Luecke.
- `research_exit_rule_summary.csv`: 34 geschlossene Baseline-Trades je Exit-Regel.
- `thin_rally_full_export_backtest_20260628_112722/outputs/thin_rally_full_summary.csv`: 16 Varianten auf 54 Export-Trades.
- `thin_rally_full_trade_results.csv`: 864 Zeilen = 54 Trades x 16 Varianten.

### Dokumentation

- `agent_state/CURRENT_STATE.md`, Stand 2026-06-28.

## 3. Tatsaechlich implementierte Strategie

- SPX/SPXW, fuenf Legs: langer Back-Put, kurzer Front-Put, Front-Call-Fly `+1/-2/+1`.
- Historical Research nutzt spot-relative Screenshot-Offsets; Strikes werden auf 10 Punkte gerundet. Das ist keine Delta-/IV-basierte Auswahl.
- Monday-Replay; Zielverfaelle ungefaehr 12/15 Kalendertage, reale DTE koennen abweichen.
- Marking primaer mit Bid/Ask-Mid; ab Verfall Intrinsic-Fallback. Der direkte Replay weist keine belastbar nachgewiesenen realistischen Market-Fills aus.
- Der spaetere OptionOmega-Export arbeitet mit 54 fremd erzeugten Trades und separater Managementsimulation.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 35 Entries, 34 auswertbar | Replay CSV | eindeutige Entry-Dates/Status und Summary geprueft | `artifact_verified` | bestaetigt |
| Hold-3TD P/L `-2175`, Hold-5TD `-4120` | Exit-Summary | CSV-Zeilen gelesen | `artifact_verified` | bestaetigt |
| 178 Missing Rows, davon 3 kritische Entry-Rows | Missing-Quote-Audit/CURRENT_STATE | Auditbericht vorhanden, Rohquotes nicht neu geladen | `artifact_verified` | dokumentierter Audit; nicht reproduziert |
| 54 Trades im Full Export | Thin-Rally-Ledger | 864 = 54 x 16, Summary geprueft | `artifact_verified` | bestaetigt |
| Warning plus Stop P/L `6979` bei 42 Trades | Thin-Rally-Summary | drei Threshold-Varianten liefern identisches Ergebnis | `artifact_verified` | bestaetigt, aber in-sample selektiert |
| Train 2023-24/Test 2025-26 | Strategy-Spec | aktueller Replay beginnt 2025-10; keine ausgefuehrte Train-Selektion belegt | `conflict` | Spec ist Plan, nicht realisierte Validierung |

## 5. Methodische Risiken

- Viele Exit-, Filter-, Proxy- und Warning-Varianten auf derselben kleinen Stichprobe; erheblicher Multiple-Testing-Bias.
- OptionOmega-Export, Monday-Replay und Proxy-Laeufe sind unterschiedliche Baselines und duerfen nicht kombiniert interpretiert werden.
- Exakte ThetaData-Reproduktion wurde punktuell belegt, nicht fuer alle Trade-Pfade; Proxy-Exitwerte divergierten stark.
- Keine unabhaengige OOS-Periode; 2025-10 bis 2026-06 ist durch wiederholte Analyse verbraucht.
- Spot-relative fixe Offsets koennen Regime-/Volatilitaetsaenderungen nicht skalieren.
- Keine Portfolio-Korrelation, Margin-Pfade oder gleichzeitige Exposures getestet.

## 6. Money Management und Portfolio

Getestet wurden Trade-Exits, Stops, Entry-Sperren und Profit Targets. Nicht getestet/belegt sind kontobasiertes Sizing, Portfolio-Limits, Margin-Stress, Korrelation und Kapitalallokation. Die Registry-Stufe `money_management` ist deshalb zu hoch.

## 7. IBKR-Integrationsreife

Kein Trade-Intent-Adapter, Replay gegen IBKR-Kontraktauflosung, Paper-Test, Restart-/Duplikatschutz oder Order-Gate fuer diese Strategie belegt. Multi-Leg-Combo-Fills und Leg-Risk sind offen. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Direkte Mid-Replay-Baselines sind negativ, waehrend ein auf einem anderen Export selektierter Managementpfad positiv ist.
- Die Spec nennt Kommission `0.65` und 10% Mid-Slippage; der untersuchte Replay-Code berechnet P/L aus Marks, ohne diese Spec-Kosten sichtbar verbindlich anzuwenden.
- Bezeichnung 12/15DTE versus tatsaechlich dokumentierter OptionOmega-Repro mit 10/13DTE.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `money_management`
- Empfohlene Stufe: `backtest_validation`
- Begruendung: reproduzierbare Research-Artefakte, aber negative direkte Baseline, selektierter Managementpfad und kein OOS-/Portfolio-MM-Nachweis.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Vorab genau eine Konstruktion, Entry-/Fill-Regel und Managementregel einfrieren und ohne weitere Selektion auf einer bisher nicht verwendeten, vollstaendig mit echten Bid/Ask-Quotes bewertbaren Zeitperiode testen; Entscheidungskriterium vor Start festlegen.
