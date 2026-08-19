# Pruefbericht: 15DTE Damage Put-Debit Alternative

- Strategy ID: `spx-15dte-damage-put-debit`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `statistical_evidence`
- Empfohlener Arbeitszustand: `rejected`

## 1. Urteil in Kurzform

Der generische Put-Debit nach Damage-Signal ist konservativ nicht robust. 2025 H1 profitiert stark von Februar/Maerz, 2024 ist in allen Baselines negativ. Ein nachtraeglich ausgewaehlter `cluster_first_debit_le8`-Kandidat ist in beiden Samples positiv, beruht aber nur auf 2 plus 8 Trades und verschlechtert als 0,25x-Overlay den 2024-MaxDD. Damit ist die Ablehnung als Hauptstrategie korrekt; der kleine Hedge-Kandidat ist keine validierte eigene Strategie.

## 2. Gepruefte Quellen

### Code

- `research/test_option_omega_put_debit_strategy.py`: Delta-/Width-Auswahl und Exitvarianten.
- `research/analyze_put_debit_followthrough_filters.py`: 15 Follow-through-/Cluster-/Debitfilter.
- `research/analyze_put_debit_overlay.py`: Kombination mit Put-Credit-Basis.

### Ergebnisartefakte

- `.../thetadata_put_debit_strategy/put_debit_entries_raw.csv`: 45 Replay- plus 18 OOS-DTE-Entries, dokumentiert ohne Skips.
- `.../put_debit_variant_trades.csv` und `put_debit_summary.csv`.
- `.../followthrough_analysis/put_debit_followthrough_robust_score.csv`.
- `.../overlay_analysis/pcs_put_debit_overlay_summary.csv`.

### Dokumentation

- `ai_agents/stand_15dte_put_debit_strategy_2026-06-09.md`
- `ai_agents/stand_15dte_put_debit_followthrough_2026-06-09.md`

## 3. Tatsaechlich implementierte Strategie

- Signal SPY 15:55: `low10_near AND off_5d_high_2pct`.
- Entry naechster Handelstag 13:35; Long Put ca. -0,40 Delta, Short Put 20 Punkte tiefer; DTE 15/21/30.
- Exit: Expiration, 3/5 Handelstage oder Recovery ueber Signal-Close und weg vom Low10.
- Follow-through-Analyse verwendet nur bis 13:35 bekannte SPY-Merkmale; `cluster_first` und konservativer Debit <= 8 wurden nach Sichtung der Basisergebnisse ausgewaehlt.
- Conservative gegen Bid/Ask; starke 2025-Stress-Spreads dokumentiert.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 2025 15DTE Expiration +140 cons | Summary | CSV gelesen | `artifact_verified` | bestaetigt; DD -2.970 bei 6 Trades |
| 2024 15DTE Expiration -2.720 cons | Summary | CSV gelesen | `artifact_verified` | bestaetigt; DD -3.400 bei 15 Trades |
| Alle generischen 2024-Varianten negativ | Summary | 12 Cons-Zeilen geprueft | `artifact_verified` | bestaetigt |
| `cluster_first_debit_le8`: 2025 +1.740, 2024 +190 | Follow-through-Artefakt/Dokument | vorhandene Ergebnisdatei | `artifact_verified` | bestaetigt, aber nur 2/8 Trades und selektiert |
| 0,25x Overlay verbessert Risiko | Overlay-Dokument/CSV | 2024 DD -3.535 auf -4.011,25 | `artifact_verified` | widerlegt; PnL leicht besser, DD schlechter |

## 5. Methodische Risiken

- Kleine, stark regimekonzentrierte Stichprobe; Februar/Maerz 2025 dominiert den Kandidaten.
- Viele DTE-, Exit-, Follow-through-, Cluster-, Debit- und spaetere Strukturvarianten auf denselben 21 Signalen.
- Conservative/Mid-Differenz 2025 teils mehrere hundert USD pro Spread; reale Ausfuehrbarkeit ist Kernrisiko.
- Kein unangetastetes OOS nach Auswahl von `cluster_first_debit_le8`.
- Overlay-DD wird aus Ereignis-PnL abgeleitet, nicht aus taeglichem Options-Mark-to-market/Margin.

## 6. Money Management und Portfolio

Hedge-Groessen 0,25/0,5/1,0x wurden untersucht. Keine Groesse verbessert PnL und MaxDD konsistent ueber beide Samples. Der Hedge ist deshalb nicht als Money-Management-Modul freigegeben.

## 7. IBKR-Integrationsreife

Vor Integration verworfen; kein Trade-Intent, Dry-Run, Paper-Test, Order-Gate oder Restart-Schutz.

## 8. Widersprueche und offene Punkte

- „In beiden Samples positiv“ beim besten Filter ist formal richtig, aber 2024 +190 bei -1.905 DD und nur acht Trades ist keine robuste Bestaetigung.
- Der Overlay-PnL steigt 2024 leicht, der zentrale Risikowert MaxDD verschlechtert sich.
- Der Kandidat ist eher eine neue, enge Hedge-Hypothese als Rettung des generischen Put-Debit-Zweigs.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `statistical_evidence`
- Empfohlene Stufe: `statistical_evidence`
- Begruendung: negative Baseline und selektierter Hedge-Kandidat sind ausreichend dokumentiert; `rejected` fuer den generischen Zweig bleibt korrekt.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Kein weiterer Test fuer diese Strategy ID. Falls der 0,25x-Hedge weiterverfolgt wird, neue Strategy ID anlegen und die bereits fixierte 15DTE/cluster-first/debit<=8-Regel auf neuen Signalen ohne weiteren Struktur-Sweep pruefen.
