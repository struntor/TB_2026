# Pruefbericht: SPX 0DTE Bull-Put plus IU30 Butterfly Overlay

- Strategy ID: `spx-0dte-bull-put-iu30-butterfly`
- Audit-Datum: `2026-07-29`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `reproduced`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Der zuerst dokumentierte Kombinationswert `+18966` ist **nicht handelbar**, weil der Options-Entry um 09:35 ET erfolgte, die verwendete zweite 5-Minuten-Kerze aber erst um 09:40 ET abgeschlossen war. Der Timingfehler wurde reproduziert und durch eine Neuberechnung mit der ersten, um 09:35 ET abgeschlossenen Kerze korrigiert.

Die kausale Variante besitzt wieder 15 Overlay-Trigger: 12 alte Trigger bleiben bestehen, drei entfallen und drei neue kommen hinzu. Mit den vorhandenen OptionOmega-Ergebnissen fuer die 12 bestaetigten Tage und echten ThetaData-09:35-Quotes fuer die drei neuen Tage ergibt sich `+20100.50` bei Midpoint- beziehungsweise `+19953` bei konservativer Natural-Fill-Annahme. PF `3.41`/`3.39`, MaxDD jeweils `-1909`. Das ist ein valider Research-Kandidat, wegen nur 15 entwicklungsinternen Overlay-Ereignissen aber kein OOS-Nachweis.

## 2. Gepruefte Quellen

### Code

- `analyze_spx_iu_butterfly_filters_overlay.py`: IU-Selektion, Feature-/Schwellen-Suche und Overlay-Ledger.
- `analyze_spx_iu_shifted_overlay.py` und `build_spx_baseline_candidate_credit_iu30_m0p25atr.py`: Zusammenfuehrung von `trade-log(14)` und `trade-log(22)`.
- `research/audit_spx_0dte_iu30_signal_timing.py`: kausaler Vergleich erster gegen zweiter 5-Minuten-Bar.
- `research/price_spx_iu30_from_thetadata_quotes.py`: Repricing neuer kausaler Trigger aus echten 09:35-Bid/Ask-Quotes.
- `research/analyze_spx_0dte_iu30_overlay_robustness.py`: Jahres-, Sizing-, Konzentrations- und Leave-one-out-Auswertung.

### Ergebnisartefakte

- `trade-log(14).csv`: 174 Credit-Trades, P/L-Summe `9940`.
- `trade-log(22).csv`: 15 Fly-Trades, P/L-Summe `9026`.
- `spx_baseline_candidate_credit_iu30_m0p25atr_daily.csv`: 174 Tageszeilen, 2024-01-23 bis 2026-06, Komponenten und Equity.
- `spx_iu_butterfly_filter_candidates.csv` und Overlay-Summaries.
- `D:\Trading\research\spx_0dte_bull_put_iu30_overlay\timing_audit_v1`: Timing-Audit, drei neu bepreiste Trigger und kausale Ledger.
- `D:\Trading\research\spx_0dte_bull_put_iu30_overlay\causal_midpoint_v1` und `causal_natural_v1`: reproduzierte Robustheitsreports.

### Dokumentation

- `handoff_2026-07-07_spx_0dte_butterfly_filters_overlay.md` und Baseline-Summary.

## 3. Tatsaechlich implementierte Strategie

- Basis: externer OptionOmega-SPX-0DTE-Bull-Put-Spread aus `trade-log(14)`.
- Overlay nur wenn um 09:35 ET `signal_regime=bullish_tier3`, `big_picture_regime=bullish_primary` und `profile_price_location=inside_value_upper` gelten.
- Long Call-Fly 0DTE `+1/-2/+1`, Center = Entry minus 0.25 vorherige ATR, auf 5er Strike gerundet, 30 Punkte Fluegelbreite, ein Fly.
- Tages-P/L wird aus zwei OptionOmega-Trade-Logs gemerged. Kein eigener Bid/Ask-Fill- oder Intraday-Ausfuehrungsmotor fuer beide Legs gemeinsam.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| Credit: 174 Trades, P/L `9940` | trade-log(14) | Zeilen und P/L-Summe geprueft | `artifact_verified` | bestaetigt |
| Urspruenglicher Fly: 15 Trades, P/L `9026` | trade-log(22) | Zeilen und P/L-Summe geprueft | `artifact_verified` | rechnerisch bestaetigt, aber Signal erst 09:40 bekannt |
| Timing-Kausalitaet | Engine plus OptionOmega-Zeitstempel | erste und zweite 5-Minuten-Bar getrennt gerechnet | `reproduced` | 12/15 Trigger gleich, 3 entfallen, 3 neu |
| Kausal kombiniert, Midpoint | kausales Ledger | 174 Tage neu gerechnet | `reproduced` | P/L `+20100.50`, PF `3.41`, MaxDD `-1909` |
| Kausal kombiniert, Natural | kausales Ledger | drei neue Flys zu natuerlichen Leg-Preisen | `reproduced` | P/L `+19953`, PF `3.39`, MaxDD `-1909` |
| IU30 -0.25 ATR fast gleiches P/L bei weniger Debit | Handoff/Logs | `9026` Fly-P/L und Debit `16520` dokumentiert | `artifact_verified` | bestaetigt fuer 15 Tage |
| Jahresstabilitaet | kausale Reports | 2024/2025/2026 getrennt | `reproduced` | alle Jahre positiv |
| OOS/Robustheit | Registry | keine unangetastete OOS-Auswertung gefunden | `unknown` | nicht belegt |

## 5. Methodische Risiken

- Nur 15 Overlay-Ereignisse; die besten drei liefern rund 55% des Netto-Overlay-P/L.
- Breiten, Center-Shifts, dynamische Regeln und Filter wurden auf derselben Stichprobe verglichen.
- Die 12 bestaetigten Flys stammen aus OptionOmega; die drei neuen wurden lokal aus ThetaData-Bid/Ask rekonstruiert. Die gemischte Preisquelle ist transparent, aber noch kein einheitlicher Combo-Fill-Test.
- Baseline `trade-log(14)` ist nicht identisch mit dem frueher dokumentierten `trade-log(12)`-Kandidaten (126 Trades); deren Signal-/Veto-Unterschied muss explizit eingefroren werden.
- Kombiniertes Tagesledger modelliert keine simultane Margin, Intraday-MTM oder Combo-Leg-Risiken.

## 6. Money Management und Portfolio

Die historische Skalierung von 0 bis 1 Fly ist monoton; maximaler definierter Tageskapitaleinsatz bei voller Groesse lag in der vorhandenen Simulation bei rund `3270`. Ein verbindliches Kontorisikobudget, Intraday-Margin-Stress, Portfolio-Exposure und Korrelation fehlen.

## 7. IBKR-Integrationsreife

Kein verbindlicher Trade-Intent fuer Credit plus bedingtes Multi-Leg-Overlay, kein Contract-Resolver/Dry-Run, keine Combo-Order-Fill-Validierung, kein Paper-Test und kein Restart-/Duplikatschutz. Produktionsfreigabe: nein.

## 8. Widersprueche und offene Punkte

- Der alte `+18966`-Wert bleibt nur als Fehlerhistorie erhalten: Er kombiniert 09:35-Entries mit 09:40-bekannten Signalen.
- `+18995` gilt fuer Entry-Center; registrierte `+18966` fuer den spaeter gewaehlt Shift-Kandidaten. Beide sind korrekt, aber verschiedene Varianten.
- Fruehere Credit-Baseline `trade-log(12)` hat 126 Trades/+8396; Overlay nutzt `trade-log(14)` mit 174/+9940. Die fachliche Differenz ist im finalen Vertrag nicht festgehalten.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `robustness_validation`
- Empfohlene Stufe: `backtest_validation`
- Begruendung: Artefakte und technische Backtests liegen vor, aber Variantenwahl und geringe Overlay-Stichprobe verhindern Robustheitsstatus.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Die jetzt kausal definierte 09:35-Regel unveraendert einfrieren. Neue Signaltage ausschliesslich als vorwaerts laufenden OOS-/Paper-Datensatz sammeln und mit echten SPXW-Combo-Quotes beziehungsweise Paper-Fills bewerten. Keine weitere Filter- oder Strike-Optimierung auf den 15 Entwicklungstagen.

## 11. Frequenz-Erweiterung vom 2026-07-29

Die logisch benachbarte Erweiterung von `inside_value_upper` auf `inside_value_upper OR above_value` erhoehte die Overlay-Tage von 15 auf 57. Alle 42 zusaetzlichen Tage wurden mit echten ThetaData-Quotes um 09:35 ET bepreist. Ein voller SPX-Fly erhoehte den konservativen kombinierten MaxDD von `-1909` auf `-3584` und den Overlay-MaxDD von `-1766` auf `-4143`. Kleine XSP-Groessen hielten zwar den kombinierten MaxDD stabil, aber nicht proportional sinkende Gebuehren verbrauchten den Grossteil des Zusatz-Edges; zwei XSP-Flys brachten im gebuehrenbereinigten Proxy nur `+337.80` ueber 42 Trades bei auf `-2084` gestiegenem Overlay-MaxDD. Entscheidung: Erweiterung nicht uebernehmen, bei den 15 bestaetigten kausalen Triggern bleiben.

## 12. Vollstaendiger Timingvergleich vom 2026-07-29

Ein Folgeaudit zeigte, dass nicht nur das IU30-Overlay, sondern auch das Top50-Breadth-Signal der alten 09:35-OptionOmega-Basis erst nach der zweiten 5-Minuten-Kerze bekannt war. Deshalb wurden alle Top50- und SPX-Kontexte fuer zwei vollstaendig kausale Varianten neu gerechnet und alle fehlenden SPXW-Snapshots bei ThetaData geladen.

Konservativer Vergleich mit Natural-Fills, 20-Punkte-Bull-Put bei einem vorherigen ATR Abstand, strengem IU30-Overlay sowie 4/8 USD Gebuehren:

| Variante | Signale | IU30 | handelbar | P/L | PF | MaxDD | Winrate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 09:35 ET, erste Kerze | 201 | 21 | 198 | +17539 | 2.52 | -3328 | 94.4% |
| 09:40 ET, zweite Kerze | 193 | 18 | 189 | +16249 | 3.11 | -2423 | 94.7% |

Auf 162 gemeinsamen handelbaren Tagen betraegt der P/L-Vorsprung von 09:35 nur 931 USD; das gepaarte 90%-Bootstrapintervall `[-1727, +3963]` schliesst null ein. Der absolute Mehrertrag ist damit nicht stabil genug, um den 37% hoeheren Drawdown zu rechtfertigen. Fuer den Forward-Paper-Test wird **09:40 ET** ausgewaehlt.

Vollstaendige Evidenz: `D:\Trading\research\spx_0dte_bull_put_iu30_overlay\causal_timing_comparison_v1\REPORT.md`.

## Paper-Bot-Integration 2026-07-29

Die ausgewaehlte 09:40-Variante ist separat schaltbar in den gemeinsamen Runner
integriert. Der Signalpfad verwendet dieselben bereits kausal berechneten
Top50-/SPX-DecisionRecords wie der Aktienbot. Die konkrete SPXW-Struktur wird
erst danach aus der Live-Kette qualifiziert und als ein atomarer BAG-Auftrag
gebaut: 20-Punkte-Bull-Put, Short-Strike ein vorheriger ATR unter dem
Entscheidungskurs, sowie nur im strengen SPX-Kontext der 30/30-IU30-Call-Fly.

Schalter: `--enable-spxw-0dte-bull-put-iu30`. Eine Order erfordert zusaetzlich
`--place-orders`. Der Pfad lehnt Nicht-DU-Konten hart ab, prueft Natural-Credit
und Natural-Fly-Debit, reserviert das definierte Risiko im gemeinsamen
Optionsbudget, verwirft eine erst nach 09:50 ET fertig berechnete Struktur,
arbeitet den Auftrag maximal fuenf Minuten und journalisiert
Entscheidung, Faktoren, Legs, Quotes, Griechen und Brokerereignisse. Echte
Paper-Fills stehen noch aus; die Strategie bleibt daher nicht
produktionsberechtigt.

Der gemeinsame Standarddeckel von 2.500 USD bleibt bewusst unveraendert. Auf
den 189 historisch handelbaren 09:40-Tagen haetten 16 Trades diesen Deckel
ueberschritten; es sind 16 der 18 IU30-Tage. Das historische definierte Risiko
lag im Median aller Trades bei 1.960 USD, im Median der IU30-Tage bei 3.147,50
USD und maximal bei 3.715 USD. Ein read-only TWS-Smoke-Test am 2026-07-29
qualifizierte die vollstaendige fuenfbeinige SPXW-Struktur; das konkrete
Beispiel hatte 2.630 USD Maximalrisiko und wuerde daher korrekt vom aktuellen
Budget blockiert. Eine Aenderung des Portfolio-Risikodeckels ist eine separate
Money-Management-Entscheidung und nicht Teil dieser Integration.
