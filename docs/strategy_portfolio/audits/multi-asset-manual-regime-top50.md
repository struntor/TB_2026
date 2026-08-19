# Pruefbericht: Multi-Asset Daily Regime / Top50 Core

- Strategy ID: `multi-asset-manual-regime-top50`
- Audit-Datum: `2026-07-14`
- Audit-Typ: `code_and_artifact_review`
- Gesamtevidenz: `artifact_verified`
- Empfohlener Reifegrad: `bot_integration`
- Empfohlener Arbeitszustand: `frozen_candidate`

## 1. Urteil in Kurzform

Die Research-Ergebnisse der eingefrorenen Baseline sind durch vorhandene Trade-, Summary- und No-Leak-Artefakte gut belegt, aber in diesem Audit nicht neu aus Rohdaten erzeugt. Die strikte Forward-Auswertung ab Entry-Datum bestaetigt fuer den optimierten Cutoff 2025-11 Top50 182 Trades, +4.426,50 USD, PF 1,6705 und MaxDD -744,79 USD. Der operative Pfad ist `live_manual_regime_tws_bot.py`, nicht der generische `live_trade_probe.py`. Er laedt den optimierten Top50-Korb, verwendet 548 Historientage und bildet die aktiven Weak-Retest- und LHF-Cluster-Vetos ab. Damit ist `bot_integration` sachlich erreicht. Nicht belegt sind jedoch eine zeilenweise Live/Replay-Paritaet und ein fortgeschriebener Paper-/Live-Closed-Trade-State fuer das Cluster-Veto; dessen Kontext stammt derzeit aus einer statischen Research-CSV. Eine Produktionsfreigabe folgt daraus nicht.

## 2. Gepruefte Quellen

### Code

- `manual_regime_rules_backtest.py`: `load_bars`, `build_daily`, `classify_*`, `price_acceptance_target`, `simulate_trade_exit`, `run_backtest`; Entscheidung auf zweitem Intraday-Bar, vorherige Tagesdaten, intraday Stop/Target-Simulation.
- `manual_regime_bot_api.py`: Tages-/Multi-Symbol-Adapter auf `run_backtest`; keine Anwendung der spaeteren aktiven Ledger-Vetos.
- `audit_current_baseline_no_leak.py` und im Manifest genannte Builder/Optimierer: Research-Pipeline fuer Auswahl und aktive Layer.
- `live_manual_regime_tws_bot.py`: operativer TWS-Runner; laedt optimierte Top50- und Symbol/Rule-Selektion, wendet Weak-Retest- und LHF-Cluster-Veto an und routet optional Orders.
- `IBKR_TradingBot/src/bot/live_trade_probe.py`: separater generischer Probe-Pfad; nicht der dokumentierte Runner der aktiven Baseline.

### Ergebnisartefakte

- `current_baseline_no_leak_audit_recomputed_forward_entry_split.csv`: 4 Varianten; Kennzahlen gegen CSV geprueft.
- `current_baseline_no_leak_audit_boundary_trades.csv`: 45 Boundary-Zeilen; belegt die Differenz zwischen Exit- und Entry-Split.
- `current_baseline_optimized_topn_30_100_sensitivity_split_entry_date.csv`: 16 TopN/Cutoff-Zeilen; alle ausgewiesenen Forward-Nettoergebnisse positiv.
- `current_baseline_optimized_cutoff_2025_11_top50_vs_old_trades.csv`: 2.043 Trade-/Variantenzeilen.
- `trade_ledger_all_assets_current_baseline_symbol_rules_2026_07_04.csv`: 11.295 Ledgerzeilen, Daten 2024 bis Juni 2026.

### Dokumentation

- `current_research_snapshot_2026_07_04.md/.json`
- `current_baseline_no_leak_audit_2026_07_04.md`
- `current_baseline_rule_stack_2026_07_04.md/.json`

## 3. Tatsaechlich implementierte Strategie

- Universum: optimierter Cutoff-2025-11-Top50-Korb; der operative Runner laedt denselben referenzierten Symbolbestand ueber `OPTIMIZED_LIVE_TOP50_PATH`.
- Entscheidung: zweiter 5-Minuten-Bar, normalerweise 09:35 New York; mindestens zwei Tagesbars erforderlich.
- Entry: Entscheidungspreis; mehrere regelbasierte Long-/Short-Setups mit strukturellen Stops/Targets.
- Exit: intraday Stop/Target; wenn beide im selben Bar getroffen werden, konservativ Stop. Offene Trades werden am Testende zum letzten Close geschlossen.
- Kosten/Fill: Research-Ledger skaliert auf Risiko/Notional; im Kern-Exitmodell keine explizite Kommission oder Slippage. Entry am beobachteten Decision-Preis und Stop/Target exakt ausgefuehrt.
- Aktive Research-Layer: bestehende Gap/LHF/Prior-Churn-Kette im Engine-/Selektionspfad, danach Weak-Retest-Veto und LHF-Verlustcluster-Veto im operativen Runner. Der Cluster-Kontext wird aber nicht aus fortgeschriebenen Paper-/Live-Abschluessen gespeist.

## 4. Ergebnispruefung

| Behauptung | Quelle | Pruefung | Evidenzstatus | Ergebnis |
| --- | --- | --- | --- | --- |
| Optimiert 2025-11 strict forward: 182 Trades, +4.426,50, PF 1,6705, MaxDD -744,79 | Snapshot/CSV | CSV-Zeile gelesen | `artifact_verified` | bestaetigt |
| Alt 2025-11: 174 Trades, +4.034,71, PF 1,6257 | Audit-CSV | CSV-Zeile gelesen | `artifact_verified` | bestaetigt |
| Optimiert 2026-03 schlaegt alten Korb nicht | Audit-CSV | 1.983,42 vs. 2.105,10 | `artifact_verified` | bestaetigt |
| TopN 30-100 robust positiv | Sensitivity-CSV | 16 Zeilen, ausgewiesene Nettoergebnisse positiv | `artifact_verified` | bestaetigt; keine Neuberechnung |
| Kein harter Look-ahead | Codeaudit + Auditdateien | relevante Codepfade stichprobenartig gelesen | `documented` | plausibel, aber kein unabhaengiger Vollaudit |
| Operativer Runner bildet die eingefrorene Baseline ab | operativer Runner/Config/Regelstack | Codepfade verglichen | `partial` | Korb, 548-Tage-Historie und aktive Vetos vorhanden; dynamischer Closed-Trade-State und zeilenweise Replay-Paritaet nicht belegt |

## 5. Methodische Risiken

- Korboptimierung und zahlreiche nachgelagerte Regel-/Veto-Tests verbrauchen die Forward-Perioden; positive Forward-Zahlen sind nicht mehr unangetastetes OOS.
- Survivorship/Universe Selection: Korb wird aus vorhandenen Historien und Rankingfenstern selektiert; Delistings/Point-in-time-Verfuegbarkeit sind nicht belegt.
- Keine realistische Spread-, Slippage-, Partial-Fill- oder Gap-through-stop-Modellierung.
- Exakte Stop-/Target-Fills und Decision-Preis-Fill sind optimistisch; Same-Bar-Reihenfolge ist nur fuer Stop/Target konservativ.
- Portfolio-Cluster-Veto ist pfadabhaengig; reproduzierbare Reihenfolge und dieselben bereits geschlossenen Trades sind zwingend.
- Das LHF-Cluster-Veto liest `top50_baseline_hard_lhf_cluster_veto_trades.csv`; neue Paper-/Live-Abschluesse werden im geprueften Runner nicht in diesen State uebernommen.

## 6. Money Management und Portfolio

Research-Ledger enthaelt Risiko, Shares, Notional und Equity. Der operative Runner berechnet die Stueckzahl aus `risk_per_trade * size_multiplier / Stop-Distanz`; der generische Bot hat separat einen `MoneyManagementBlock` mit Portfolio-Limits. Keiner der beiden Pfade ist als identische Reproduktion der Research-Portfoliokurve belegt. Das Cluster-Veto benoetigt zudem fortlaufenden portfolioweiten Closed-Trade-State; der operative Runner nutzt hier nur ein statisches Research-Artefakt.

## 7. IBKR-Integrationsreife

- Trade-Intent: Engine liefert Entry/Stop/Target/Side/Rule; ein explizit versionierter Vertrag der eingefrorenen Baseline fehlt.
- Dry-Run: Signal-only-Pfad und Trade-Plan-CSV sind dokumentiert.
- TWS Paper: erfolgreicher SPY-Test/Paper-Orderweg vom 06.07.2026 ist dokumentiert; ein laengerer Strategie-Paper-Nachweis fehlt.
- Order-Gate: `--place-orders`; ohne Flag werden keine Orders gesendet.
- Restart/Duplikatschutz: bestehende Positionen und offene Orders werden pro Symbol geprueft; persistenter Strategie-/Signal-ID-Schutz ist nicht belegt.
- Produktionsfreigabe: nicht vorhanden.

## 8. Widersprueche und offene Punkte

1. Der generische `live_trade_probe.py` weicht von der Baseline ab, ist laut Betriebsdokumentation aber nicht ihr operativer Runner.
2. Der operative Cluster-Kontext ist statisch und bildet neue Paper-/Live-Abschluesse nicht fortlaufend ab.
3. Dokumentierte No-Leak-Pruefung ist intern, nicht unabhaengig reproduziert.
4. Forward-Perioden wurden fuer Korb-, TopN- und Veto-Entscheidungen wiederholt betrachtet.

## 9. Reifegradentscheidung

- Bisherige Registry-Stufe: `bot_integration`
- Empfohlene Stufe: `bot_integration`
- Begruendung: operativer Signal- und TWS-Pfad fuer die eingefrorene Baseline ist vorhanden und technisch getestet; Live/Replay-Paritaet, dynamischer Portfolio-State und laengerer Paper-Nachweis bleiben offen.
- Produktionsberechtigt: `false`

## 10. Naechster minimaler Test

Ein deterministischer Replay des operativen Runners auf einem kurzen vorhandenen Zeitraum, einschliesslich fortgeschriebenem Closed-Trade-State, und zeilenweiser Vergleich der Trade-Intents mit dem eingefrorenen Research-Ledger. Entscheidungskriterium: keine unerklaerten Trade-Differenzen; anschliessend denselben State-Mechanismus im Paper-Lauf pruefen.
