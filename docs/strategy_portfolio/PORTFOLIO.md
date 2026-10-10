# Strategie-Portfolio

Stand: 2026-08-17

> Diese Uebersicht wird aus `registry.json` erzeugt. Sie ist beschreibend und aktiviert keine Strategie.

| Prioritaet | Strategie | Reifegrad | Arbeit | Evidenz | IBKR | Produktion | Naechster Schritt |
| --- | --- | --- | --- | --- | --- | --- | --- |
| primary | Multi-Asset Daily Regime / Top50 Core | `paper_trading` | `monitoring` | `artifact_verified` | `integration_tested` | nein | Die aktuelle Cluster-/Sizing-Version gegen Backtest und TWS-Ausfuehrungen reconciliieren, Gap-through-Stop und konservative Slippage dokumentieren sowie Risiko-Limits, Kill-Switch und konkrete Versionsfreigabe fuer einen kleinen Live-Pilot festhalten. |
| quick_probe | SPY Failed-Auction Hard-12 First-Touch Options | `bot_integration` | `frozen_candidate` | `reproduced` | `implemented_unit_tested` | nein | Bot an einem Handelstag mit laufender Paper-TWS im Dry-Run starten; danach den ersten echten Paper-BAG-Fill und den Touch-/30m-Exit gegen NBBO und Journal pruefen. |
| quick_probe | SPY Failed-Auction zu POC 2DTE Put-Fly/BWB | `backtest_validation` | `active` | `artifact_verified` | `not_started` | nein | Als unveraenderten Legacy-Kontrollanker behalten; neue Parameter nicht mehr auf dieser gemischten Retest-/First-Touch-Stichprobe auswaehlen. |
| quick_probe | SPY Volume/VIX 0DTE Call-Fly | `statistical_evidence` | `parked` | `artifact_verified` | `not_started` | nein | Parken. Nur mit einer fachlich neuen, vorab festgelegten VIX-skalierten breiteren Struktur und einem neuen Forward-Fenster wieder aufnehmen; keine weiteren Filter auf dem bereits geoeffneten OOS-Block suchen. |
| secondary | SPX 75DTE Daily Paper VIX15 Put-BWB (Tuesday lineage) | `paper_trading` | `monitoring` | `reproduced` | `implemented_paper_verified` | nein | Ab 03.09.2026 taeglichen Paper-Fill-Test mit normalen Risikolimits beobachten: Fill-Quote/Credit je Preisstufe und Journal-Reconciliation. Bestehenden Bot fuer neuen Code neu starten. |
| secondary | SPX Flyagonal 12/15DTE | `paper_trading` | `monitoring` | `artifact_verified` | `not_started` | nein | DTE-Definition und Kostenmodell vereinheitlichen, den Management-Kandidaten einfrieren und die geplante echte Train/Test-Trennung ausfuehren. |
| secondary | XSP VIX-Pullback 100DTE Put-Credit | `paper_trading` | `monitoring` | `reproduced` | `implemented_unit_tested` | nein | Version 1.0.1 unveraendert im DU-Paperkonto handeln und Entry, TP60/60-DTE-Exit, Restart sowie Journal-Reconciliation beobachten; keine weitere historische Filter- oder Parameterwahl. |
| secondary | Multi-Stock IVR70 Put-Credit | `bot_integration` | `frozen_candidate` | `artifact_verified` | `implemented_unit_tested` | nein | Tages-Dry-Run mit laufender Paper-TWS und danach ersten BAG-Paper-Fill, TP-/21DTE-Exit und Journal-Reconciliation verifizieren. |
| secondary | SPX 15DTE Put-Credit Income Core | `bot_integration` | `frozen_candidate` | `artifact_verified` | `implemented_unit_tested` | nein | Bot zunaechst ohne --place-orders an einem Signaltag gegen die Paper-TWS laufen lassen; danach ersten BAG-Paper-Fill, Griechen, Credit, Damage-Exit und Journal-Reconciliation pruefen. |
| secondary | GLD GVZ-Pullback 100DTE Put-Credit | `backtest_validation` | `frozen_candidate` | `reproduced` | `not_started` | nein | Regeln einschliesslich q90-Krisenfilter einfrieren und als Forward-Paper-Scanner beobachten; keine weitere Auswahl auf den 14 Exact-Trades. |
| secondary | IWM RVX-Pullback 100DTE Put-Credit | `backtest_validation` | `frozen_candidate` | `reproduced` | `not_started` | nein | Regeln und Struktur eingefroren lassen und einen unangetasteten Forward-Paperblock sammeln; noch nicht als orderausloesende Bot-Strategie integrieren. |
| secondary | Multi-Asset VWAP Stretch-Revert Long | `backtest_validation` | `active` | `artifact_verified` | `not_started` | nein | Die Higher-Low-Reversal-Bedingung als getrennte risikoarme Research-Variante einfrieren und nur auf neuen Daten fortschreiben; parallel die korrekte Filter-vor-Simulation-Reihenfolge als verbindliche Backtestsemantik dokumentieren. Keine Bot-Integration vor neuer Forward-Evidenz. |
| secondary | Multi-Asset VWAP Stretch-Revert Long / Higher-Low Entry | `backtest_validation` | `frozen_candidate` | `reproduced` | `not_started` | nein | Regel unveraendert ab 30.07.2026 auf neuen Daten fortschreiben; keine weitere Entry- oder Exit-Optimierung bis ausreichend Forward-Trades vorliegen. |
| secondary | SPX 0DTE Bull-Put plus IU30 Butterfly Overlay | `backtest_validation` | `active` | `reproduced` | `integrated_paper_only` | nein | Den Schalter im DU-Paperbetrieb aktivieren und echte SPXW-Combo-Quotes/Fills als Forward-OOS-Evidenz sammeln; bis dahin nicht produktionsberechtigt. |
| backlog | SPX 45DTE Put-Credit Delta30 Width20 | `robustness_validation` | `parked` | `artifact_verified` | `not_started` | nein | Nur mit einer vorab festgelegten Basisdefinition und einem unangetasteten Testfenster wieder aufnehmen; VP zunaechst nur als isolierte Entry-Sperre pruefen. |
| backlog | 15DTE Damage Bear-Call Alternative | `statistical_evidence` | `rejected` | `artifact_verified` | `rejected_before_integration` | nein | Nicht fortsetzen; negative Evidenz beim 15DTE-Core verlinkt lassen. |
| backlog | 15DTE Damage Put-Debit Alternative | `statistical_evidence` | `rejected` | `artifact_verified` | `rejected_before_integration` | nein | Nicht fortsetzen; nur bei einer neuen, vorab begrenzten Hypothese als neue Variante wieder aufnehmen. |
| backlog | Multi-Asset EMA Long plus Options/OI Context | `statistical_evidence` | `parked` | `artifact_verified` | `not_started` | nein | Falls wieder aufgenommen: Asset-Universum und Regeln vorab festlegen, ausschliesslich auf dem Trainingsfenster entwickeln und einen unangetasteten spaeteren OOS-Block reservieren. |
| backlog | Post-Break Range-Mid Long | `statistical_evidence` | `rejected` | `documented_only` | `rejected_before_integration` | nein | Keine Fortsetzung; zuerst die widerspruechlichen Ergebnisquellen klaeren. Nur ein fachlich neues Setup darf spaeter eine neue Strategy-ID erhalten. |
| backlog | USO OVX-Pullback 100DTE Put-Credit | `statistical_evidence` | `rejected` | `reproduced` | `rejected_before_integration` | nein | Nicht weiter optimieren. Nur eine fachlich neue Oelstruktur oder ein anderes Underlying darf spaeter eine neue Strategy-ID erhalten. |
| backlog | SPY Failed-Auction Hard-6 First-Touch Options | `exploration` | `parked` | `reproduced` | `not_started` | nein | Parken; nur bei neuen unabhaengigen Daten oder einer fachlich neuen Exit-/Strukturhypothese wieder aufnehmen. |

## Detailstatus

### Multi-Asset Daily Regime / Top50 Core

- ID: `multi-asset-manual-regime-top50`
- Familie: `multi_asset_equity_regime`
- Status: `paper_trading` / `monitoring` / `primary`
- Kurzstand: Die Research-Baseline ist nach OOS-, TopN- und No-Leak-Pruefungen eingefroren. Der operative TWS-Pfad hat ueber zwei Paper-Versionen 25 Trade-Datensaetze, 46 Execution-Zeilen und 17 abgeschlossene Trades mit realisiertem P/L erzeugt. Die Strategie ist der einzige aktuelle Kandidat fuer einen eng begrenzten Live-Pilot, bleibt bis zur dokumentierten Abschlussfreigabe aber formal Paper-Trading.
- IBKR: `integration_tested`; Dry-Run: `True`; Paper: `True`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-asset-manual-regime-top50.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\current_research_snapshot_2026_07_04.md`
- Naechster Schritt: Die aktuelle Cluster-/Sizing-Version gegen Backtest und TWS-Ausfuehrungen reconciliieren, Gap-through-Stop und konservative Slippage dokumentieren sowie Risiko-Limits, Kill-Switch und konkrete Versionsfreigabe fuer einen kleinen Live-Pilot festhalten.

### SPY Failed-Auction Hard-12 First-Touch Options

- ID: `spy-failed-auction-poc-hard12-first-touch-options`
- Familie: `spy_short_dte_options`
- Status: `bot_integration` / `frozen_candidate` / `quick_probe`
- Kurzstand: Neuer eng definierter Paper-Kandidat: Hard-12, kausaler RVOL 1,00-1,20, erster POC-Touch, 1DTE U10/L10 mit Body am POC und ohne CRV10-Gate. Auf 12 unveraenderten Signalen erzielt der konservative Touch-Mid-30m-Exit +51,42 USD je Fly; die Teilstichprobe 2024-2026 umfasst 7 Trades und +477,50 USD. Wegen n=12 und nicht beobachteter BAG-Fills ausschliesslich Paper-Trading.
- IBKR: `implemented_unit_tested`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spy-failed-auction-poc-hard12-first-touch-options.md`
- Hauptquelle: `D:\Trading\research\spy_failed_auction_poc_fly\signal_heatmaps\REPORT.md`
- Naechster Schritt: Bot an einem Handelstag mit laufender Paper-TWS im Dry-Run starten; danach den ersten echten Paper-BAG-Fill und den Touch-/30m-Exit gegen NBBO und Journal pruefen.

### SPY Failed-Auction zu POC 2DTE Put-Fly/BWB

- ID: `spy-failed-auction-poc-2dte-bwb`
- Familie: `spy_short_dte_options`
- Status: `backtest_validation` / `active` / `quick_probe`
- Kurzstand: Legacy-Kontrollanker: OptionOmega trade-log(27) verifiziert +476 USD und 10/16 Gewinner fuer die alte 2DTE-U10/L10-Auswahl. Die neue Rohbar-Rekonstruktion zeigt, dass diese Stichprobe Retest- und First-Touch-Faelle mischt; strikte Hard-12- und Hard-6-Hypothesen werden deshalb unter eigenen Strategy-IDs weitergefuehrt.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spy-failed-auction-poc-2dte-bwb.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\statistic\spy_failed_auction_poc_fly_research\README.md`
- Naechster Schritt: Als unveraenderten Legacy-Kontrollanker behalten; neue Parameter nicht mehr auf dieser gemischten Retest-/First-Touch-Stichprobe auswaehlen.

### SPY Volume/VIX 0DTE Call-Fly

- ID: `spy-volume-vix-0dte-call-fly`
- Familie: `spy_0dte_options`
- Status: `statistical_evidence` / `parked` / `quick_probe`
- Kurzstand: Exact-Quote-Kontrollanker ueber 864 Sessions: fixer 2-Punkte-0DTE-Call-Fly konservativ -8.453 USD, Midpoint nach Gebuehren -3.194 USD. Die vorab festgelegte Above-Value/Thin-Volume/VIX<=20-Regel scheitert im OOS 2025-2026 ebenfalls (-226 USD konservativ, -14 USD Midpoint bei 30 Trades). Der kleine theoretische Mid-Edge reicht nicht fuer Kosten und Fill-Risiko.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spy-volume-vix-0dte-call-fly.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\statistic\spy_volume_vix_research_part2\README.md`
- Naechster Schritt: Parken. Nur mit einer fachlich neuen, vorab festgelegten VIX-skalierten breiteren Struktur und einem neuen Forward-Fenster wieder aufnehmen; keine weiteren Filter auf dem bereits geoeffneten OOS-Block suchen.

### SPX 75DTE Daily Paper VIX15 Put-BWB (Tuesday lineage)

- ID: `spx-75dte-put-bwb-tuesday-vix15`
- Familie: `spx_options_multileg`
- Status: `paper_trading` / `monitoring` / `secondary`
- Kurzstand: Die rekonstruierte Dienstag-Baseline bleibt Referenz. Paper-Version 1.6.0 prueft ab 03.09.2026 bis auf Weiteres Mo-Fr ab 09:32 ET einen Einstieg bei VIX >= 15. Preisstaffel unveraendert: aktueller Mid-0,20, Mid-0,40 und Mid-0,65 fuer jeweils 5 Minuten, dann Storno; TP40 als GTC-Combo nach Fill. Normale Grenzen bleiben aktiv: maximal sieben offene Positionen und dedizierter BWB-Risikodeckel, standardmaessig 10.500 USD. Keine Produktionsfreigabe und keine Reaktivierung des historischen Budget-Bypasses.
- IBKR: `implemented_paper_verified`; Dry-Run: `True`; Paper: `True`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-75dte-put-bwb-tuesday-vix15.md`
- Hauptquelle: `D:\Trading\research\spx_75dte_put_bwb\daily_mtm_v1\report.md`
- Naechster Schritt: Ab 03.09.2026 taeglichen Paper-Fill-Test mit normalen Risikolimits beobachten: Fill-Quote/Credit je Preisstufe und Journal-Reconciliation. Bestehenden Bot fuer neuen Code neu starten.

### SPX Flyagonal 12/15DTE

- ID: `spx-flyagonal-12-15dte`
- Familie: `spx_options_multileg`
- Status: `paper_trading` / `monitoring` / `secondary`
- Kurzstand: Die Screenshot-Struktur ist reproduzierbar spezifiziert. Baseline-Replays, exakte ThetaData-Reproduktion sowie Management- und Thin-Rally-Varianten liegen vor; die Stichprobe ist klein und nicht unabhaengig validiert.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-flyagonal-12-15dte.md`
- Hauptquelle: `D:\PycharmProjects\Multi_Agent_Option_Test\agent_state\CURRENT_STATE.md`
- Naechster Schritt: DTE-Definition und Kostenmodell vereinheitlichen, den Management-Kandidaten einfrieren und die geplante echte Train/Test-Trennung ausfuehren.

### XSP VIX-Pullback 100DTE Put-Credit

- ID: `vix-reversal-100dte-put-credit`
- Familie: `spx_options_credit`
- Status: `paper_trading` / `monitoring` / `secondary`
- Kurzstand: Version 1.0.1-paper ist kausal mit 12:35 CT = 13:35 ET revalidiert. 26 momentumgefilterte Signale, 23 echte XSP-Strukturen, 22 abgeschlossene Trades: +490,60 USD netto, 19 Gewinner, Q-MTM-DD -569,80 USD, Roh-MTM-DD -919,60 USD. Alle neun benachbarten TP-/Zeitexit-Kombinationen bleiben positiv. Kleiner Paper-Kandidat, nicht produktionsreif.
- IBKR: `implemented_unit_tested`; Dry-Run: `True`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\vix-reversal-100dte-put-credit.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\strategy_ideas\vix_reversal_100dte_put_credit_2026-06-11.md`
- Naechster Schritt: Version 1.0.1 unveraendert im DU-Paperkonto handeln und Entry, TP60/60-DTE-Exit, Restart sowie Journal-Reconciliation beobachten; keine weitere historische Filter- oder Parameterwahl.

### Multi-Stock IVR70 Put-Credit

- ID: `multi-stock-ivr70-put-credit`
- Familie: `multi_asset_single_stock_options_credit`
- Status: `bot_integration` / `frozen_candidate` / `secondary`
- Kurzstand: Die Bugfix-Version 1.0.1 des kausalen IVR-Cross-ueber-70-Kandidaten mit 35-50 DTE, Delta-Ziel 0,19 innerhalb 0,16-0,22 und 10-Punkte-Breite ist als separat schaltbare zweite Paper-Optionsstrategie integriert. Sie qualifiziert echte Leg-Paare und journalisiert DTE-, Earnings-, Delta-, Credit- und Datenfehler getrennt. Der gemeinsame Options-Risikodeckel betraegt im Standardmodell 2.500 USD. Historische Preise und IVR bleiben Proxy-Werte; echte Paper-Fills fehlen.
- IBKR: `implemented_unit_tested`; Dry-Run: `True`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-stock-ivr70-put-credit.md`
- Hauptquelle: `D:\Trading\research\single_stock_drawdown_recovery_put_credit\high_ivr_portfolio_25k_risk10_analysis_v1\ANALYSIS.md`
- Naechster Schritt: Tages-Dry-Run mit laufender Paper-TWS und danach ersten BAG-Paper-Fill, TP-/21DTE-Exit und Journal-Reconciliation verifizieren.

### SPX 15DTE Put-Credit Income Core

- ID: `spx-15dte-put-credit-income-core`
- Familie: `spx_options_credit`
- Status: `bot_integration` / `frozen_candidate` / `secondary`
- Kurzstand: Die kausal korrigierte Version 1.1.0 wertet die abgeschlossene 13:30-SPY-Kerze aus und handelt den 13:35-SPXW-Quote. Der korrigierte Replay umfasst 129 Trades; die Paper-only Bot-Integration ist implementiert und unit-getestet, aber noch nicht durch einen echten Paper-Fill verifiziert.
- IBKR: `implemented_unit_tested`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-15dte-put-credit-income-core.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\stand_15dte_current_baseline_strategy_details_2026-06-12.md`
- Naechster Schritt: Bot zunaechst ohne --place-orders an einem Signaltag gegen die Paper-TWS laufen lassen; danach ersten BAG-Paper-Fill, Griechen, Credit, Damage-Exit und Journal-Reconciliation pruefen.

### GLD GVZ-Pullback 100DTE Put-Credit

- ID: `gld-gvz-pullback-100dte-put-credit`
- Familie: `commodity_etf_options_credit`
- Status: `backtest_validation` / `frozen_candidate` / `secondary`
- Kurzstand: Der kausale GVZ/GLD-Kandidat handelt nur erhoehte, aber nicht extreme Volatilitaet: GVZ-Pullback nach rollierendem q60-Signal, positives GLD-Momentum, GLD ueber SMA200 und Entry-GVZ nicht ueber dem vorherigen q90. Von 19 Signalen besitzen 14 strenge ThetaData-Paare. Exact Replay: 13 Gewinner, ein Verlust, +596,80 USD netto und -638,30 USD MTM-MaxDD. Der q90-Krisenfilter ist post-hoc und verlangt Forward-Evidenz.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\gld-gvz-pullback-100dte-put-credit.md`
- Hauptquelle: `D:/PycharmProjects/IBKR_TradingBot/docs/strategy_portfolio/audits/gld-gvz-pullback-100dte-put-credit.md`
- Naechster Schritt: Regeln einschliesslich q90-Krisenfilter einfrieren und als Forward-Paper-Scanner beobachten; keine weitere Auswahl auf den 14 Exact-Trades.

### IWM RVX-Pullback 100DTE Put-Credit

- ID: `iwm-rvx-pullback-100dte-put-credit`
- Familie: `index_etf_options_credit`
- Status: `backtest_validation` / `frozen_candidate` / `secondary`
- Kurzstand: Der kausale RVX/IWM-Kandidat nutzt einen rollierenden RVX-60%-Schwellenwert, mindestens 4% Rueckgang vom Peak, positives 20-Sitzungen-IWM-Momentum und IWM oberhalb der vorangegangenen SMA200. Alle zehn historischen Signale besitzen nach Feiertags- und Joint-Pair-Aufloesung vollstaendige echte ThetaData-Bid/Ask-Pfade: 10 Gewinner, +580,40 USD netto und -277,30 USD taeglicher MTM-MaxDD. Die Stichprobe beginnt erst Ende 2023 und bleibt fuer Bot-Integration zu klein.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\iwm-rvx-pullback-100dte-put-credit.md`
- Hauptquelle: `D:/PycharmProjects/IBKR_TradingBot/docs/strategy_portfolio/audits/iwm-rvx-pullback-100dte-put-credit.md`
- Naechster Schritt: Regeln und Struktur eingefroren lassen und einen unangetasteten Forward-Paperblock sammeln; noch nicht als orderausloesende Bot-Strategie integrieren.

### Multi-Asset VWAP Stretch-Revert Long

- ID: `multi-asset-vwap-stretch-revert-long`
- Familie: `multi_asset_intraday_vwap`
- Status: `backtest_validation` / `active` / `secondary`
- Kurzstand: Der kausale Audit deckte neben Exit- und Rechtszensurfehlern eine falsche Filterreihenfolge auf: abgelehnte Signale blockierten spaetere Einstiege. Mit Filtern vor der Simulation erzielt die Next-Open-Baseline 2025-29.07.2026 +36.50R bei MaxDD -24.57R. Eine bestaetigte Higher-Low-Reversal-Variante senkt den MaxDD auf -7.60R bei +12.38R, ist aber 2026 nicht durchgehend positiv und bleibt Research-Kandidat.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-asset-vwap-stretch-revert-long.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\2026-05-28\stand_vwap_long_candidate_hardening_2026-05-28.md`
- Naechster Schritt: Die Higher-Low-Reversal-Bedingung als getrennte risikoarme Research-Variante einfrieren und nur auf neuen Daten fortschreiben; parallel die korrekte Filter-vor-Simulation-Reihenfolge als verbindliche Backtestsemantik dokumentieren. Keine Bot-Integration vor neuer Forward-Evidenz.

### Multi-Asset VWAP Stretch-Revert Long / Higher-Low Entry

- ID: `multi-asset-vwap-stretch-revert-long-higher-low`
- Familie: `multi_asset_daily_vwap_reversion_variant`
- Status: `backtest_validation` / `frozen_candidate` / `secondary`
- Kurzstand: Risikoaermere Entry-Variante: Nach dem Stretch-Signal muss der Folgetag gruen schliessen, das Signal-Close zurueckerobern und ein hoeheres Tief bilden; Einstieg am darauffolgenden Open. 2025-29.07.2026: 157 Trades, +12.38R nach 10 bps je Seite, PF 1.27 und MaxDD -7.60R. Noch kein unangetasteter OOS-Zeitraum.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-asset-vwap-stretch-revert-long-higher-low.md`
- Hauptquelle: `D:\Trading\research\multi_asset_vwap_stretch_revert_long\frozen_oos_v1\entry_confirmation_v1\summary.csv`
- Naechster Schritt: Regel unveraendert ab 30.07.2026 auf neuen Daten fortschreiben; keine weitere Entry- oder Exit-Optimierung bis ausreichend Forward-Trades vorliegen.

### SPX 0DTE Bull-Put plus IU30 Butterfly Overlay

- ID: `spx-0dte-bull-put-iu30-butterfly`
- Familie: `spx_0dte_options`
- Status: `backtest_validation` / `active` / `secondary`
- Kurzstand: Vollstaendiger Kausalitaets-Audit: Auch das alte Bull-Put-Breadth-Signal war um 09:35 noch nicht bekannt. Beide Zeitpunkte wurden mit neu berechneten Top50-/SPX-Kontexten und vollstaendigen ThetaData-Snapshots verglichen. 09:40 wurde fuer Forward-Paper gewaehlt: Natural +16249 USD, PF 3.11, MaxDD -2423 bei 189 handelbaren Tagen und 18 IU30-Overlays. 09:35 erzielte mehr P/L, aber 37% hoeheren Drawdown; der gepaarte P/L-Vorteil war statistisch nicht stabil.
- IBKR: `integrated_paper_only`; Dry-Run: `True`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-0dte-bull-put-iu30-butterfly.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\statistic\spx_adx_di_5min_2024_02_03\handoff_2026-07-07_spx_0dte_butterfly_filters_overlay.md`
- Naechster Schritt: Den Schalter im DU-Paperbetrieb aktivieren und echte SPXW-Combo-Quotes/Fills als Forward-OOS-Evidenz sammeln; bis dahin nicht produktionsberechtigt.

### SPX 45DTE Put-Credit Delta30 Width20

- ID: `spx-45dte-put-credit-delta30-width20`
- Familie: `spx_options_credit`
- Status: `robustness_validation` / `parked` / `backlog`
- Kurzstand: Existing-fast-IV-credit bleibt beste vorhandene Basis. Volume-Profile-Full-Close und 50-Prozent-De-Risking verbessern sie nicht robust; VP bleibt als Warnflag oder Entry-Sperre interessant.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-45dte-put-credit-delta30-width20.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\stand_45dte_volume_profile_damage_control_2026-06-10.md`
- Naechster Schritt: Nur mit einer vorab festgelegten Basisdefinition und einem unangetasteten Testfenster wieder aufnehmen; VP zunaechst nur als isolierte Entry-Sperre pruefen.

### 15DTE Damage Bear-Call Alternative

- ID: `spx-15dte-damage-bear-call`
- Familie: `spx_options_credit_variant`
- Status: `statistical_evidence` / `rejected` / `backlog`
- Kurzstand: Als Damage-Signal-Unterzweig verworfen und nicht als aktive Hauptstrategie weiterzufuehren.
- IBKR: `rejected_before_integration`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-15dte-damage-bear-call.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\stand_15dte_bear_call_strategy_2026-06-09.md`
- Naechster Schritt: Nicht fortsetzen; negative Evidenz beim 15DTE-Core verlinkt lassen.

### 15DTE Damage Put-Debit Alternative

- ID: `spx-15dte-damage-put-debit`
- Familie: `spx_options_debit_variant`
- Status: `statistical_evidence` / `rejected` / `backlog`
- Kurzstand: Put-Debit-Varianten nach Damage-Signal wurden untersucht und als aktiver Hauptpfad verworfen.
- IBKR: `rejected_before_integration`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spx-15dte-damage-put-debit.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\stand_15dte_put_debit_followthrough_2026-06-09.md`
- Naechster Schritt: Nicht fortsetzen; nur bei einer neuen, vorab begrenzten Hypothese als neue Variante wieder aufnehmen.

### Multi-Asset EMA Long plus Options/OI Context

- ID: `multi-asset-ema-options-oi-context`
- Familie: `multi_asset_ema_options`
- Status: `statistical_evidence` / `parked` / `backlog`
- Kurzstand: Optionsfilter verbessern einzelne Assets, aber nicht universell. OI-Flow ist schwach und asset-spezifisch; ein allgemein belastbarer Regelstack fehlt.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `artifact_verified`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-asset-ema-options-oi-context.md`
- Hauptquelle: `D:\PycharmProjects\PythonProject\ai_agents\2026-05-28\stand_thetadata_msft_options_client_2026-05-28.md`
- Naechster Schritt: Falls wieder aufgenommen: Asset-Universum und Regeln vorab festlegen, ausschliesslich auf dem Trainingsfenster entwickeln und einen unangetasteten spaeteren OOS-Block reservieren.

### Post-Break Range-Mid Long

- ID: `multi-asset-post-break-range-mid-long`
- Familie: `multi_asset_equity_regime_variant`
- Status: `statistical_evidence` / `rejected` / `backlog`
- Kurzstand: Die generische Range-Mid-Long-Regel bleibt verworfen. Die dokumentierten 110 Trades/-2215,55 USD sind keinem vorhandenen Ergebnisartefakt eindeutig zuordenbar; das auffindbare Ledger zeigt 28 Trades/-47,67 gewichtete Punkte.
- IBKR: `rejected_before_integration`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `documented_only`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\multi-asset-post-break-range-mid-long.md`
- Hauptquelle: `D:\PycharmProjects\IBKR_TradingBot\docs\handoff_2026-07-01_post_break_range_phase.md`
- Naechster Schritt: Keine Fortsetzung; zuerst die widerspruechlichen Ergebnisquellen klaeren. Nur ein fachlich neues Setup darf spaeter eine neue Strategy-ID erhalten.

### USO OVX-Pullback 100DTE Put-Credit

- ID: `uso-ovx-pullback-100dte-put-credit`
- Familie: `commodity_etf_options_credit`
- Status: `statistical_evidence` / `rejected` / `backlog`
- Kurzstand: Die direkte Uebertragung des Volatilitaets-Pullbacks auf OVX/USO wird verworfen. Der USO-Forward-Pfad ist schwach, der gemeinsame Proxy bleibt negativ und nur eines von sieben moderat-q90-gefilterten Signalen besitzt ein vollstaendiges 10-Punkte-Paar nahe Delta -0,20. Das ist keine test- oder handelbare Optionshistorie.
- IBKR: `rejected_before_integration`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\uso-ovx-pullback-100dte-put-credit.md`
- Hauptquelle: `D:/PycharmProjects/IBKR_TradingBot/docs/strategy_portfolio/audits/uso-ovx-pullback-100dte-put-credit.md`
- Naechster Schritt: Nicht weiter optimieren. Nur eine fachlich neue Oelstruktur oder ein anderes Underlying darf spaeter eine neue Strategy-ID erhalten.

### SPY Failed-Auction Hard-6 First-Touch Options

- ID: `spy-failed-auction-poc-hard6-first-touch-options`
- Familie: `spy_short_dte_options`
- Status: `exploration` / `parked` / `backlog`
- Kurzstand: Hard-6 erhoeht die strikte Stichprobe von 71 auf 108 Einstiege bei aehnlicher POC-Touchrate von 68,5%. Im getrennten Options-Grid besteht jedoch keine der 525 Zellen gleichzeitig Zeitblock-, Nachbarschafts- und 22,63-USD-Modellfehler-Gate. Die weichere Hypothese bleibt deshalb geparkt.
- IBKR: `not_started`; Dry-Run: `False`; Paper: `False`
- Produktionsfreigabe: `False`
- Audit-Evidenz: `reproduced`
- Pruefbericht: `D:\PycharmProjects\IBKR_TradingBot\docs\strategy_portfolio\audits\spy-failed-auction-poc-hard6-first-touch-options.md`
- Hauptquelle: `D:\Trading\research\spy_failed_auction_poc_fly\signal_heatmaps\REPORT.md`
- Naechster Schritt: Parken; nur bei neuen unabhaengigen Daten oder einer fachlich neuen Exit-/Strukturhypothese wieder aufnehmen.
