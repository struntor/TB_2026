# Pruefbericht: SPX 15DTE Put-Credit Income Core

- Strategy ID: `spx-15dte-put-credit-income-core`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `robustness_validation`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Die berichtete aktuelle Baseline (`mg100`, EMA-Gap > -0,5 %, POC20 > 1,0) und ihre Kennzahlen sind in vorhandenen CSV-Artefakten belegt: 119 Trades, +11.545 USD konservativer PnL und -1.070 USD MaxDD. Sie wurden in diesem Audit nicht neu aus Rohquotes erzeugt. Die Baseline ist das Ergebnis zahlreicher auf denselben Jahren 2023-2026 durchgefuehrter Filter-, Management- und Schwellentests; diese Jahre sind deshalb keine unangetastete OOS-Bestaetigung mehr. Zudem widerspricht der spaetere 2023-Baseline-Datensatz dem frueheren approximativen 2023-Core-Test erheblich. Der Status `money_management` ist zu hoch: Portfolio-Sizing und unabhaengige Validierung fehlen.

## 2. Gepruefte Quellen

### Code

- `research/stress_15dte_core_poc_threshold_robustness.py`: mischt vier jahresspezifische Tradequellen, erzeugt 420 Schwellenvarianten und berechnet die aktuelle Baseline.
- `research/analyze_15dte_current_baseline_loss_audit.py`: Filter- und Damage-Exit-Counterfactual auf dem ausgewaehlten Baseline-Export.
- `research/test_15dte_core_2023_2026.py`: frueher 2023/2026-Test mit dokumentierter schneller Strike-Approximation.
- `research/backtest_option_omega_15dte_ruleset1_oos_2025_full.py`: vollstaendige 2025-Quote-/Trade-Erzeugung.

### Ergebnisartefakte

- `.../thetadata_15dte_current_baseline_equity_2023_2026/current_baseline_equity_summary_2023_2026.csv`: 119 Trades; Jahreszeilen 2023-2026.
- `.../thetadata_15dte_current_baseline_loss_audit/baseline_management_counterfactual_summary.csv`: 119 Trades, 16 Damage-Exits.
- `.../thetadata_15dte_core_poc_threshold_stress/stress_variant_summary_all.csv`: 420 Varianten; Baseline und Nachbarschaft vorhanden.
- `.../thetadata_15dte_core_2023_2026/summary_2023_2026.csv`: frueherer, nicht apples-to-apples 2023/2026-Test.

### Dokumentation

- `ai_agents/stand_15dte_current_baseline_strategy_details_2026-06-12.md`
- `ai_agents/stand_15dte_core_2023_2026_2026-06-11.md`
- `ai_agents/stand_15dte_core_poc_candidate_robustness_2026-06-12.md`

## 3. Tatsaechlich implementierte Strategie

- SPX/SPXW 10-Punkte Put-Credit-Spread, Entry 13:35, Ziel 15DTE.
- Core-Block: konservativer Credit < 1,4 oder Short-IV >= 0,19 bei Distanz zum Low10 <= 5 %.
- Reentry: `reentry_fast_3d_not_low10`; zusaetzlich Vortag ueber EMA20, Entry-Gap > -0,5 % und SPY-Abstand zum 20-Tage-POC > 1 Punkt.
- Management: Damage-Exit nur bei Management-Flag, `structure_combo` und Friction-Gap <= 100; sonst Expirations-PnL.
- Fill-Modell: Entry/Exit konservativ gegen Bid/Ask; Mid wird separat gefuehrt.
- POC wird aus SPY-5-Minuten-/Volumenprofil-Daten bis 13:35 abgeleitet. Die Auditierung hat die kausale Konstruktion nicht vollstaendig von Rohbars rekonstruiert.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 119 Trades, +11.545, MaxDD -1.070 | Equity-Summary | CSV direkt gelesen | `artifact_verified` | bestaetigt |
| 2023/24/25/26: +2.895/+4.255/+4.605/-210 | Equity-Summary | Jahreszeilen gelesen | `artifact_verified` | bestaetigt |
| Damage-Management verbessert Tail gegen No-Damage | Counterfactual-CSV | +11.545/-1.070 vs. +8.610/-6.450 | `artifact_verified` | bestaetigt, aber auf derselben selektierten Stichprobe |
| 420 Schwellenvarianten, 199 bestehen Floor | Stress-CSV/Code | Grid 4x5x3x7 = 420; Floor im Code | `artifact_verified` | 420 bestaetigt; 199 dokumentiert, nicht separat nachgezaehlt |
| 2023 robust positiv | spaete Baseline vs. frueher Test | spaet +2.895; frueher final_core cons -20.670 | `conflict` | unterschiedliche Quellpopulation/Management; nicht austauschbar |
| OOS validiert | Dateinamen/Berichte | 2025 wurde wiederholt fuer Regelwahl genutzt | `conflict` | historische OOS-Bezeichnung, heute kein unangetastetes OOS |

## 5. Methodische Risiken

- Starke Multiple-Testing-Belastung durch viele auf denselben Jahren getestete Entry-, POC-, EMA-, Damage- und Reentry-Regeln.
- 2023/2026 basieren teils auf anderer Kandidatengenerierung als 2024/2025; spaete Baseline ersetzt den frueheren negativen 2023-Test durch eine andere Quelle.
- Konservative Quotes koennen in Stressphasen extrem breit sein; Ausfuehrbarkeit und Quote-Qualitaet sind nicht modelliert.
- MaxDD ist Trade-PnL-sequenziell, nicht kapital-/Margin- oder mark-to-market-basiert.
- 2026 hat nur drei Baseline-Trades; Regimeabdeckung ist gering.

## 6. Money Management und Portfolio

Damage-Exit und Reentry sind Strategie-Management, kein Portfolio-Money-Management. Es fehlen Margin-/Buying-Power-Modell, Positionsgroesse, simultane Exposures, Verlustlimits und Zusammenspiel mit anderen Strategien.

## 7. IBKR-Integrationsreife

Kein Trade-Intent-Adapter, Replay, Paper-Test, Order-Gate oder Restart-/Duplikatschutz fuer diese Strategie belegt. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Frueherer 2023-Test: final_core cons -20.670; spaete Baseline: +2.895. Der spaete Datensatz muss als andere Pipeline/Population bezeichnet werden.
- `oos_2025_full` ist nach wiederholter Regelwahl kein unabhaengiges OOS mehr.
- POC- und EMA-Schwellen wurden nach Einsicht in alle vier Jahre gewaehlt.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `money_management`
- Empfohlene Stufe: `robustness_validation`
- Begruendung: Strategie-Management und robuste Artefakte vorhanden, aber keine unabhaengige OOS-/Walk-forward-Bestaetigung und kein Portfolio-Sizing.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Baseline unveraendert einfrieren und auf dem naechsten vollstaendig neuen, bisher nicht fuer Regelwahl verwendeten Zeitblock mit identischer Strike-/Quote-Pipeline testen; vorab Akzeptanzkriterien fuer Tradezahl, PnL, MaxDD und Quote-Ausfaelle festlegen.
