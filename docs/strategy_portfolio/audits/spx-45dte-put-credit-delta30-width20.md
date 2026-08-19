# Pruefbericht: SPX 45DTE Put-Credit Delta30 Width20

- Strategy ID: `spx-45dte-put-credit-delta30-width20`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `robustness_validation`
- Empfohlener Arbeitszustand: `parked`

## 1. Urteil in Kurzform

Die -0,30-Delta/20-Punkte-Struktur ist als bessere 45DTE-Basis gegen die alte -0,25/10-Struktur durch vorhandene Trade- und Summary-Artefakte belegt. Die beste Variante ist jedoch nicht stabil zwischen den beiden betrachteten Samples: `fast_reentry_iv_credit_rule` ist 2025 H1 staerker, `damage_baseline` 2024. Volume-Profile-Full-Close und 50%-De-Risking schlagen die jeweilige Basis nicht konsistent. Kein echtes Portfolio-Money-Management oder unangetastetes OOS ist belegt.

## 2. Gepruefte Quellen

### Code

- `research/test_option_omega_45dte_delta30_width20_exit_21dte.py`: 45DTE, Delta -0,30, Width 20, geplanter Exit 21DTE.
- `research/test_45dte_delta30_width20_volume_profile_damage_control.py`: VP-Full-Close/Overlay.
- `research/test_45dte_delta30_width20_volume_profile_derisk_50.py`: 50%-Teilabbau und Rest-Baseline.

### Ergebnisartefakte

- `.../thetadata_45dte_delta30_width20_exit_21dte/option_omega_45dte_delta30_width20_summary.csv`: 42-77 Trades je Variante/Sample.
- `.../thetadata_45dte_delta30_width20_volume_profile_damage/...summary.csv`
- `.../thetadata_45dte_delta30_width20_volume_profile_derisk_50/...summary.csv`

### Dokumentation

- `ai_agents/stand_45dte_delta30_width20_exit21_2026-06-09.md`
- `ai_agents/stand_45dte_volume_profile_damage_control_2026-06-10.md`

## 3. Tatsaechlich implementierte Strategie

- SPX/SPXW Put-Credit-Spread; Entry 13:35; Ziel-Short-Delta -0,30; Width 20; Expiration >= Entry +45 Kalendertage.
- Geplanter Exit am ersten Handelstag ab Expiration minus 21 Kalendertage, 13:35.
- Varianten: Damage-Control; 3-Tage-Fast-Reentry; zusaetzlicher 15DTE-IV/Credit-Block.
- VP-Tests nutzen SPY-1h, 20 Kalendertage Profil und Cross/Speed/Prev-Low-Regeln; konservative und Mid-PnL getrennt.
- Konservatives Fill-Modell gegen Bid/Ask; keine Kommission/Margin/Liquiditaetsgrenze belegt.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 2025 H1 fast+IV/Credit: 42 Trades, +4.765, DD -1.690 | Basis-Summary | CSV gelesen | `artifact_verified` | bestaetigt |
| 2024 dieselbe Regel: 66 Trades, +2.975, DD -3.760 | Basis-Summary | CSV gelesen | `artifact_verified` | bestaetigt |
| 2024 Damage-Baseline besser: +5.100, DD -2.850 | Basis-Summary | CSV gelesen | `artifact_verified` | bestaetigt |
| VP Full-Close nicht robust besser | VP-Summary | Varianten gegen Baselines verglichen | `artifact_verified` | bestaetigt |
| 50%-VP besser als beste Fast-IV-Basis | De-Risk-Summary | 2025 +3.900/-1.725 vs +4.765/-1.690; 2024 +2.115/-4.635 vs +2.975/-3.760 | `artifact_verified` | widerlegt |
| OOS 2025 | Dokumentation | nur Jan-Jun und fuer Variantenwahl genutzt | `conflict` | keine heutige unabhaengige OOS-Bestaetigung |

## 5. Methodische Risiken

- Regeln aus 15DTE wurden auf 45DTE uebertragen und anhand derselben 2024/2025-Samples beurteilt.
- Viele VP-Signal-, Puffer- und Exitvarianten: erhebliche Multiple-Testing-Belastung.
- Zeitbasierter 21DTE-Exit plus Stress-Damage-Exit; keine mark-to-market Kapitalbindung/Margin.
- Nur 2024 und 2025 H1; starke Regimekonzentration.
- VP-Features und Optionsquotes stammen aus unterschiedlichen Underlyings (SPY vs SPX); Mapping-/Timing-Risiko bleibt.

## 6. Money Management und Portfolio

Getestet sind Trade-Exit, Reentry und Teilabbau. Nicht getestet sind Portfolio-Sizing, simultane Trades, Buying Power, Exposure-Limits und Korrelation mit dem 15DTE-Core.

## 7. IBKR-Integrationsreife

Kein Strategy-Adapter, Trade-Intent, Dry-Run, Paper-Run oder Restart-Schutz belegt. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Es gibt keine einzige dominante Baseline: 2025 favorisiert Fast+IV/Credit, 2024 Damage-Baseline.
- Der Registry-Text nennt `existing_fast_iv_credit` als beste Basis, obwohl 2024 Damage-Baseline klar besser ist.
- VP-Warnflag/Entry-Sperre wurde empfohlen, aber nicht als isolierter Entry-Test belegt.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `money_management`
- Empfohlene Stufe: `robustness_validation`
- Begruendung: Exit-/De-Risk-Tests existieren, doch Baseline-Instabilitaet und fehlende Portfolioebene verhindern `money_management` als abgeschlossenen Reifegrad.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Vor Wiederaufnahme genau zwei vorab eingefrorene Baselines (`damage_baseline`, `fast_reentry_iv_credit_rule`) auf einem neuen Zeitblock ohne weitere Parameterwahl gegeneinander testen; Entscheidung nach konservativem PnL, MaxDD und Quote-Ausfallquote.
