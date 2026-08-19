# Pruefbericht: Multi-Asset VWAP Stretch-Revert Long

- Strategy ID: `multi-asset-vwap-stretch-revert-long`
- Audit-Datum: `2026-07-30`
- Audit-Typ: `code_artifact_and_frozen_forward_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `backtest_validation`
- Empfohlener Arbeitszustand: `active`

## 1. Urteil in Kurzform

Ein deterministischer, train-only selektierter VWAP-Backtest existiert. Die isolierte Long-Familie ist im breiten Universum positiv; der heute genannte Kandidat `stretch_revert_long + rs60_positive + normal_volume` hat 197 Trades auf 104 Assets und +27,0548R brutto. Mit 5 bps pro Seite bleiben +21,6327R bei MaxDD -14,0104R. Diese Zahlen sind gegen Trades und Equity-CSV geprueft. Aber RS60- und Volumenfilter wurden auf derselben 2025-2026-Periode gewaehlt, auf der ihre Leistung berichtet wird. Fuer den kombinierten Filter ist diese Periode daher kein echtes OOS. `frozen_candidate` und `robustness_validation` sind zu optimistisch.

Nachtrag 30.07.2026: Die Regeln wurden unveraendert bis 29.07.2026 fortgeschrieben und parallel mit einer kausalen, gap-bewussten Exit-Simulation geprueft. Ein mangels Folgedaten noch nicht abgeschlossener AMAT-Trade wurde als rechtszensiert entfernt. Im vorher unangetasteten Zeitraum ab 11.04.2026 liefert die bereinigte Variante 32 abgeschlossene Trades, +4,9554R nach 10 bps je Seite, Profit Factor 1,5261 und MaxDD -4,5709R. Das ist ermutigend, aber statistisch noch offen: Der IID-Trade-Bootstrap hat ein 95%-Intervall von -4,63R bis +14,57R und in 15,8% der Resamples ist das Ergebnis nicht positiv. Die Strategie bleibt deshalb in `backtest_validation`.

## 2. Gepruefte Quellen

### Code

- `research/top50_vwap_strategy_oos_noleak_2025_2026.py`: Tagesaggregation, Indikatoren, Varianten, Train-only-Selektion, OOS-Trades.
- `research/sector_candidates_vwap_strategy_oos_noleak_2025_2026.py`: ersetzt nur Universum/Output durch 177 eindeutige Symbole.
- `research/sector_candidates_vwap_long_rs_volume_analysis_2025_2026.py`: nachtraeglicher RS60-/Volumenkontext und R-Normierung.
- `research/sector_candidates_vwap_long_candidate_hardening_2025_2026.py`: Kosten- und Exposure-Stress.
- `research/analyze_vwap_stretch_revert_long_frozen_oos_2026.py`: eingefrorene Fortschreibung, kausaler Vortrags-RVWAP, Gap-Fills und 10-bps-Pruefung.
- `research/summarize_vwap_stretch_revert_long_confirmation.py`: unangetastete Teilperiode, Bootstrap und Konzentration.
- `research/test_vwap_stretch_revert_long_exit_management.py`: vorab begrenzter Vergleich von Baseline, 3-Tage-Time-Stop, Tag-1-No-Rebound und Tag-2-ohne-0,5R-MFE.

### Ergebnisartefakte

- `.../LONG_RS_VOLUME_ANALYSIS/sector_candidates_vwap_long_rs_volume_summary_2025_2026.csv`: 16 Szenariozeilen.
- `.../LONG_CANDIDATE_HARDENING/sector_candidates_vwap_long_candidate_hardening_summary_2025_2026.csv`: 14 Szenarien.
- `...base_net_5bps_side_trades_2025_2026.csv`: 197 Trades, 104 Assets, Entry 02.01.2025 bis 10.04.2026.
- `...base_net_5bps_side_equity_2025_2026.csv`: 468 Kalendertage; Endwert 21,6327079508R, Min-DD -14,0103899542R.
- `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\summary.csv`: Entwicklungs- und unangetastete Ergebnisse beider Fill-Modelle.
- `...\causal_prior_rvwap_gap_aware_filtered_trades.csv`: kausale Einzeltrades bis 29.07.2026.
- `...\frozen_vwap_stretch_revert_long_equity_10bps.png`: Vergleich von Legacy- und kausaler Equity.
- `...\exit_management_v1\development_selection.csv` und `confirmation_once.csv`: getrennte Exit-Auswahl und einmalige Forward-Pruefung.

### Dokumentation

- `ai_agents/2026-05-19/stand_vwap_oos_2026-05-19.md`
- `ai_agents/2026-05-20/stand_vwap_rs_volume_broad_universe_2026-05-20.md`
- `ai_agents/2026-05-28/stand_vwap_long_candidate_hardening_2026-05-28.md`

## 3. Tatsaechlich implementierte Strategie

- Universum: 177 eindeutige sektorbalancierte Kandidaten; manuelles Asset-zu-Sektor-ETF-Mapping, Duplikate werden auf den ersten Eintrag reduziert.
- Trotz der bisherigen Familienbezeichnung ist dies keine Intraday-VWAP-Strategie: Intraday-Rohbars werden zu Tagesbars aggregiert; das Signal nutzt einen 20-/40-Handelstage-Rolling-VWAP und haelt bis zu 3/5 Tage.
- Signal nach Tages-Close: nicht im Bull-Regime, Close mindestens 1,8 ATR unter rollierendem VWAP 20/40, RSI14 <=35, Close unter RVWAP.
- Variantenauswahl je Symbol/Strategie nur auf 2023/24: mindestens 8 Trades, positiver Score/PnL, Top3-Gewinnanteil <=0,9.
- Entry: naechster Tages-Open. Stop 1,2/1,6 ATR, Target 1,5/2,2 ATR, Haltedauer 3/5 Tage; Exit auch bei Rueckkehr zum RVWAP.
- Kandidatenfilter: RS60 positiv gegen Sektor und Signaltags-Volumen zwischen 0,8 und 1,5 des 20-Tage-Mittels.
- Fill-Modell: Stop wird vor Target/RVWAP geprueft; exakte Stop-/Target-/RVWAP-Fills, keine Gaps/Spreads. Kostenstress ist pauschal 5 bps je Entry- und Exitseite.

Die korrigierte Pruefvariante verwendet den am Session-Open bekannten RVWAP des abgeschlossenen Vortags als festen Mean-Level. Gaps durch Stop, Target oder Mean-Level werden zum Tages-Open gefuellt; bei innerhalb einer Tageskerze unklarer Reihenfolge gewinnt konservativ der Stop. Primaerer Kostenstress sind 10 bps je Seite.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| 197 Trades, 104 Assets, +27,0548R brutto | Summary/Trades | Zeilen, Uniques und Summe geprueft | `artifact_verified` | bestaetigt |
| 5 bps/Seite: +21,6327R, MaxDD -14,0104R | Trades/Equity | Trade-Summe = Equity-Endwert; DD-Minimum geprueft | `artifact_verified` | bestaetigt |
| 20 bps/Seite bleibt +5,3664R | Hardening-Summary | CSV-Zeile gelesen | `artifact_verified` | bestaetigt |
| Filter ist OOS-validiert | Code/Handoffs | Filterauswahl nach Betrachtung 2025/26 | `conflict` | nicht bestaetigt; post-OOS Research |
| Backtest-Entrypoint laut Registry unter `D:\Trading\research` | Dateisystem | Pfad fehlt; Datei liegt in `D:\PycharmProjects\PythonProject\research` | `conflict` | Registry-Pfad falsch |
| Kausales Exit-Modell bleibt im Entwicklungsfenster positiv | neuer Replay | 198 Trades, 10 bps/Seite | `reproduced` | +14,0229R, PF 1,2125, MaxDD -14,6479R |
| Eingefrorener Kandidat bleibt nach 10.04.2026 positiv | unangetastete Fortschreibung | 32 abgeschlossene Trades, 10 bps/Seite | `preliminary_forward_oos` | +4,9554R, PF 1,5261, MaxDD -4,5709R |
| Kuerzerer Time-Stop verbessert robust | Exit-Management-Test | Entwicklung waehlt 3 Sessions, danach einmalige Forward-Pruefung | `rejected_variant` | Entwicklung +24,2158R; Forward -3,1619R |
| Fruehes Failure-Management ist zeitstabil | Exit-Management-Test | zwei logisch definierte Failure-Exits | `not_confirmed` | Tag 1 Forward +4,5393R, aber Entwicklung nur +8,4209R; Tag 2 Forward +9,1778R, aber Entwicklung nur +0,1228R |

## 5. Methodische Risiken

- RS60/Normal-Volume-Kombination wurde ex post auf dem OOS-Fenster selektiert; Multiple Testing ueber viele Filter/Szenarien.
- Breites Universum und Sektormapping sind nicht als point-in-time/survivorship-frei belegt.
- Dokumentation/Hypothese sprechen von Intraday-VWAP, waehrend der Code einen mehrtaegigen Rolling-VWAP auf Tagesaggregation handelt. Vor einer Integration muss diese Semantik eindeutig umbenannt werden.
- `drop_duplicates(symbol, keep=first)` macht Multi-Segment-Zuordnung implizit reihenfolgeabhaengig.
- Tages-Open-Fills, Stop-/Target-Fills und RVWAP-Fills ignorieren Gaps, Spread und Liquiditaet.
- Der Legacy-RVWAP-Exit verwendet den rollierenden VWAP inklusive des noch nicht abgeschlossenen Exit-Tags. Das ist fuer einen Intraday-Exit nicht kausal. Die neue Sensitivitaet beseitigt diesen Fehler; der Edge bleibt kleiner, aber positiv.
- 5 bps je Seite ist Kostenstress, kein symbol-/preis-/volumenabhaengiges Ausfuehrungsmodell.
- Exposure-Caps werden ex post mit Exit-Datum verwaltet; Gleichzeitigkeit am Exit-/Entry-Tag und Intraday-Reihenfolge sind grob.
- PnL in R verwendet initiale ATR-Stopdistanz, aber noch keine echte Kapital-/Share-Rundung.

## 6. Money Management und Portfolio

Kosten und einfache Max-Open-/Segment-Caps wurden getestet. Engere Caps reduzieren den Ertrag deutlich; ein Portfoliovertrag mit Kapital, Shares, Korrelation, Sektorbudget, Gap-Risiko, Kill-Switch und Rolling Health fehlt. Die 1R-Skalierung ist noch kein vollstaendiges Money Management.

## 7. IBKR-Integrationsreife

Kein Trade-Intent-Adapter, kein Replay im IBKR-Bot, kein TWS-Paper-Nachweis und kein Order-Gate fuer diese Strategie. Entry am Tages-Open erfordert eine klare Signal-Persistenz vom Vortag sowie Gap-/Limit-Policy.

## 8. Widersprueche und offene Punkte

1. Die fruehere Registry nannte den kombinierten Filter OOS/No-Leak; nur die Grundvariantenauswahl war train-only, nicht die spaetere Filterauswahl. Seit 11.04.2026 existiert nun ein echter, aber noch kleiner unangetasteter Abschnitt.
2. Der fruehere Registry-Backtestpfad existierte nicht am angegebenen Ort; er wurde am 30.07.2026 auf den neuen reproduzierbaren Runner korrigiert.
3. Dokumentation vom 20.05. sagt zunaechst keine Kosten; der 28.05.-Hardening-Lauf ergaenzt pauschale Kosten, aber keine reale Execution.
4. Die Regeln sind jetzt fuer die Forward-Pruefung eingefroren; der Arbeitszustand bleibt bis zur ausreichenden Stichprobe bewusst `active` statt `frozen_candidate`.

## 9. Reifegradentscheidung

- Aktuelle Registry-Stufe: `backtest_validation`
- Empfohlene Stufe: `backtest_validation`
- Begruendung: reproduzierbarer und kausal gehaerteter Backtest sowie ein positives unangetastetes Fenster, aber mit 32 abgeschlossenen Trades, breitem Bootstrap-Intervall und nicht stabilen Exit-Varianten noch zu wenig Evidenz fuer `robustness_validation`.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Nach dem Entry-Nachtrag wird die bestaetigte Higher-Low-Reversal-Regel als getrennte risikoarme Variante eingefroren. Ab 30.07.2026 werden nur neue Daten angehaengt; die bisherige Next-Open-Regel bleibt Kontrollkurve. Vor einem Reifegradwechsel muessen ausserdem die Point-in-time-/Survivorship-Frage des Universums und die Filter-vor-Simulation-Reihenfolge verbindlich dokumentiert sein.

## 11. Entry-Confirmation-Nachtrag 30.07.2026

Der begrenzte Entry-Test verglich ohne Exit-Aenderung vier Regeln: Next Open, Open ueber Signal-Close, bestaetigter gruener Reversal-Tag und gruener Reversal-Tag plus hoeheres Tief. Dabei wurde ein weiterer Pipelinefehler sichtbar: Der alte Ablauf simulierte zunaechst alle Signale und filterte RS60/Volumen erst danach. Somit blockierten nicht handelbare Signale ueber die Non-Overlap-Logik spaetere gueltige Signale. Der neue Test setzt Filter korrekt vor die Simulation; seine Next-Open-Baseline ist deshalb nicht zahlenidentisch mit den frueheren 197/198 Trades.

Ergebnisse 2025 bis 29.07.2026 nach 10 bps je Seite:

| Einstieg | Trades | Netto-R | MaxDD | PF | Urteil |
| --- | ---: | ---: | ---: | ---: | --- |
| Next Open | 325 | +36,5001 | -24,5697 | 1,3379 | ertragreich, aber hoher Cluster-Drawdown |
| Open ueber Signal-Close | 226 | +5,3012 | -14,3311 | 1,0711 | kein ausreichender Mehrwert |
| Gruener Reversal-Tag | 209 | +2,8253 | -16,2726 | 1,0410 | verworfen |
| Gruener Reversal-Tag plus hoeheres Tief | 157 | +12,3823 | -7,5957 | 1,2724 | einziger sinnvoller risikoarmer Kandidat |

Die Higher-Low-Variante reduziert den Drawdown deutlich, erzeugt aber keinen zeitlich gleichmaessigen Edge: 2025 +11,8934R, 01.01.-10.04.2026 -3,4385R und 11.04.-29.07.2026 +3,9274R. Da alle Zeitraeume inzwischen fuer Research betrachtet wurden, ist dies kein Produktionsnachweis. Die Variante darf nur eingefroren auf neuen Daten weiterbeobachtet werden.

Artefakte: `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\entry_confirmation_v1`.
