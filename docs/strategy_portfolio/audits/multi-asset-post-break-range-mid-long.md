# Pruefbericht: Post-Break Range-Mid Long

- Strategy ID: `multi-asset-post-break-range-mid-long`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `documented_only`
- Empfohlener Reifegrad: `statistical_evidence`
- Empfohlener Arbeitszustand: `rejected`

## 1. Urteil in Kurzform

Die Idee wurde als generische Regel negativ getestet und anschliessend aus der aktiven Engine entfernt. Das ist im Code und Handoff klar nachvollziehbar. Die konkrete Registry-Zahl von 110 Trades, 19 Symbolen und etwa -2.215,55 USD konnte jedoch nicht gegen ein eindeutig passendes vorhandenes Trade-Artefakt verifiziert werden. Der auffindbare Roh-Ledger `trade_ledger_top50_union_live_history_548d_post_break_range.csv` enthaelt fuer die Regel nur 28 Trades auf 22 Symbolen und -47,669 gewichtete Punkte. Das ist kein Gegenbeweis, sondern offenbar eine andere Vor-/Sizing-Stufe; die behauptete 110-Trade-Dollar-Auswertung bleibt dokumentiert, nicht artifact-verifiziert.

## 2. Gepruefte Quellen

### Code

- `manual_regime_rules_backtest.py`: `detect_post_break_midrange`, `classify_intraday_value_profile`, `classify_daily_structure_phase`; aktive Engine enthaelt keine `post_break_range_mid_long`-Entryregel mehr.
- `analyze_multi_asset_post_break_lower_subtypes.py`: spaetere Phasendiagnostik, keine Range-Mid-Long-Strategie; bestaetigt den Wechsel von neuer Regel zu diagnostischer Klassifikation.

### Ergebnisartefakte

- `trade_ledger_top50_union_live_history_548d_post_break_range.csv`: 1.877 Gesamtzeilen; 28 `post_break_range_mid_long`, 22 Symbole, -47,669 gewichtete Punkte, Entry 22.02.2024 bis 11.06.2026.
- `cutoff_forward_top50_variants_live_history_548d_post_break_range_summary.csv`: sieben Korbvarianten, aber keine isolierte Range-Mid-Long-Dollarzerlegung.
- `cutoff_2025_11_top50_live_history_548d_phase_test_summary.csv`: 1.089 Trades, +34.489,13 USD fuer die Gesamtengine nach Entfernung/Phasenlogik; kein isolierter Test der verworfenen Regel.

### Dokumentation

- `IBKR_TradingBot/docs/handoff_2026-07-01_post_break_range_phase.md`

## 3. Tatsaechlich implementierte Strategie

Historischer Test laut Handoff:

- Entry bei erkannter `post_break_midrange`-Phase auf dem zweiten 5-Minuten-Bar.
- Stop unter jungem Range-Low.
- Target Range-High oder naechster Widerstand.
- Groesse 0,5.

Die Regel ist aktuell **nicht implementiert**. Aktiv geblieben ist nur die kausale Phasen-/Value-Erkennung: lokale Swing-Cluster, Mindestbreite 1,4 ATR, Mid-Distanz maximal 0,35 ATR und Profil nur aus Bars vor `decision_dt`.

Kosten-/Fill-Modell des historischen Tests ist nicht separat dokumentiert; der gemeinsame Engine-Simulator nutzt exakte Stop-/Target-Fills und konservativen Stop bei Same-Bar-Kollision.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 110 Trades, 19 Symbole, ca. -2.215,55 USD | Handoff/Registry | kein eindeutig passendes Trade-/MM-Artefakt gefunden | `documented` | nicht unabhaengig belegbar |
| Breite Rohvariante ist negativ | Roh-Ledger | 28 Trades, 22 Symbole, -47,669 gewichtete Punkte | `artifact_verified` | Richtung negativ bestaetigt, aber andere Aggregation |
| Regel aus aktiver Engine entfernt | `manual_regime_rules_backtest.py` | kein Entry-Rule-Vorkommen; nur Klassifikation | `artifact_verified` | bestaetigt |
| Neue Phasenlogik leicht positiv/non-invasiv | Phase-Test-Summary/Handoff | Gesamt-Summary vorhanden | `documented` | nicht mit der verworfenen Regel gleichsetzen |

## 5. Methodische Risiken

- Unterschiedliche Universen/Ledger-/Money-Management-Stufen erzeugen widersprechende Tradezahlen.
- Keine klar versionierte Spezifikation oder Ergebnisdatei des exakt 110-Trade-Laufs.
- Idee entstand aus AZO-Diagnose und wurde danach breit getestet; Parameter der Range-Erkennung koennen durch Fallstudien beeinflusst sein.
- Der Roh-Engine-Simulator modelliert keine Slippage, Gaps oder Kommissionen.
- Breites Top50-Union-Universum ist nicht point-in-time/survivorship-geprueft.
- Negative Gesamtzahl kann Symbolkonzentration enthalten; fuer die Verwerfungsentscheidung reicht sie, nicht fuer feinere Untertypen.

## 6. Money Management und Portfolio

Nur historische Groesse 0,5 und ein spaeterer, nicht eindeutig zuordenbarer Dollar-PnL sind dokumentiert. Kein eigenstaendiges Portfolio-Sizing ist sinnvoll, solange die generische Regel negativ und verworfen ist.

## 7. IBKR-Integrationsreife

Nicht begonnen und nach negativer Evidenz nicht erforderlich. Es gibt keinen Trade-Intent, Replay, TWS-Paper-Test oder Order-Gate fuer diese Regel. Die zugrunde liegende Phase darf als Kontext anderer Strategien genutzt werden, aber nicht unter dieser Strategy-ID als Trade-Regel.

## 8. Widersprueche und offene Punkte

1. Registry/Handoff: 110 Trades, 19 Symbole, -2.215,55 USD; auffindbarer Roh-Ledger: 28 Trades, 22 Symbole, -47,669 gewichtete Punkte.
2. Der genaue Pfad des 110-Trade-Money-Management-Exports fehlt.
3. Positive Gesamtwirkung der spaeteren Phasenlogik ist keine Rehabilitation der negativen Range-Mid-Long-Regel.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `statistical_evidence`
- Empfohlene Stufe: `statistical_evidence`
- Begruendung: ausreichende negative Evidenz zum Verwerfen, aber konkrete Headline-Zahlen nur dokumentiert.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Kein neuer Markt-Test. Einmalig den exakten 110-Trade-Export beziehungsweise Erzeugungsbefehl identifizieren und archivieren; Entscheidungskriterium ist nur die Reproduzierbarkeit der historischen Negativentscheidung. Falls nicht auffindbar, Registry-Kennzahlen auf `documented_only` markieren und die Regel verworfen lassen.
